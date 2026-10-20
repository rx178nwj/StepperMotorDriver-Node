/*
 * usb_proto.c / symtab.c をホストで単体テストするためのスタブ群。
 */
#include "test_stubs.h"
#include "settings.h"
#include "usbd_cdc_if.h"
#include <string.h>

/* ---------------------------------------------------------------- リンカスクリプトのシンボル
 *
 * __sym_table_start/end と __build_id_start/end は「配列の先頭と終端」でなければならない（差でサイズを求める）。
 * C だけでは別オブジェクトを隣接させられないため、gcc のトップレベル asm で .data に確保する。
 * ラベルの先頭 '_' の有無はターゲットで異なるので __USER_LABEL_PREFIX__ を付ける。
 */
#define STR_(x) #x
#define STR(x)  STR_(x)
#define PFX     STR(__USER_LABEL_PREFIX__)

#if UINTPTR_MAX > 0xFFFFFFFFu
#define SYM_ENTRY_BYTES "24"
#define SYM_ENTRY_BYTES_N 24u
#else
#define SYM_ENTRY_BYTES "12"
#define SYM_ENTRY_BYTES_N 12u
#endif

_Static_assert(sizeof(sym_entry_t) == SYM_ENTRY_BYTES_N, "sym_entry_t size differs from the asm reservation");

__asm__(
    ".data\n"
    ".balign 8\n"
    ".globl " PFX "__sym_table_start\n"
    PFX "__sym_table_start:\n"
    ".space " SYM_ENTRY_BYTES "*4\n"
    ".globl " PFX "__sym_table_end\n"
    PFX "__sym_table_end:\n"
    ".balign 4\n"
    ".globl " PFX "__build_id_start\n"
    PFX "__build_id_start:\n"
    ".space 36\n"
    ".globl " PFX "__build_id_end\n"
    PFX "__build_id_end:\n"
    ".space 4\n"
    ".text\n"
);

_Static_assert(TEST_SYM_N == 4u, "update the asm .space count");
_Static_assert(TEST_BUILD_ID_NOTE_LEN == 36u, "update the asm .space count");

extern sym_entry_t __sym_table_start[];
extern uint8_t __build_id_start[];

void test_sym_set(const sym_entry_t *src)
{
    memcpy(__sym_table_start, src, TEST_SYM_N * sizeof(sym_entry_t));
}

uint8_t *test_build_id_note(void)
{
    return __build_id_start;
}

/* ---------------------------------------------------------------- 状態 */

uint32_t g_tick;
int g_tx_fail;
sent_frame_t g_sent[SENT_MAX];
unsigned g_sent_n;
unsigned g_primask_sets;
uint32_t g_primask;
unsigned g_rd8_calls, g_rd32_calls;
uint32_t g_ecc_addr;

uint8_t g_board_id;
unsigned g_cr_calls;
uint16_t g_cr_id;
uint8_t g_cr_dlc;
uint8_t g_cr_data[8];
uint8_t g_cr_resp_n;
comm_frame_t g_cr_resp;

volatile bool nv_ecc_error;
USBD_HandleTypeDef hUsbDeviceFS;
static USBD_CDC_HandleTypeDef cdc;

void test_stubs_reset(void)
{
    g_tx_fail = 0;
    g_sent_n = 0;
    g_primask_sets = 0;
    g_primask = 0;
    g_rd8_calls = g_rd32_calls = 0;
    g_ecc_addr = 0;
    g_board_id = 3;
    g_cr_calls = 0;
    g_cr_resp_n = 0;
    memset(&g_cr_resp, 0, sizeof g_cr_resp);
    nv_ecc_error = false;
    cdc.TxState = 0;
    hUsbDeviceFS.dev_state = USBD_STATE_CONFIGURED;
    hUsbDeviceFS.pClassData = &cdc;
}

void test_tx_complete(void)
{
    cdc.TxState = 0;
}

/* ---------------------------------------------------------------- HAL・USB ミドルウェア */

uint32_t HAL_GetTick(void)
{
    return g_tick;
}

uint32_t __get_PRIMASK(void)
{
    return g_primask;
}

void __set_PRIMASK(uint32_t v)
{
    g_primask = v;
    g_primask_sets++;
}

uint8_t CDC_Transmit_FS(uint8_t *Buf, uint16_t Len)
{
    if (g_tx_fail || cdc.TxState != 0u) {
        return USBD_BUSY;
    }
    if (g_sent_n < SENT_MAX && Len <= sizeof g_sent[0].bytes) {
        memcpy(g_sent[g_sent_n].bytes, Buf, Len);
        g_sent[g_sent_n].len = Len;
        g_sent_n++;
    }
    cdc.TxState = 1u;
    return USBD_OK;
}

/* ---------------------------------------------------------------- 偽メモリ */

uint8_t test_pattern(uint32_t addr)
{
    return (uint8_t)(addr * 7u + 3u + (addr >> 8));
}

uint8_t test_rd8(uint32_t addr)
{
    g_rd8_calls++;
    if (g_ecc_addr != 0u && addr == g_ecc_addr) {
        nv_ecc_error = true;
    }
    return test_pattern(addr);
}

uint32_t test_rd32(uint32_t addr)
{
    g_rd32_calls++;
    if (g_ecc_addr != 0u && g_ecc_addr >= addr && g_ecc_addr < addr + 4u) {
        nv_ecc_error = true;
    }
    return (uint32_t)test_pattern(addr) | ((uint32_t)test_pattern(addr + 1u) << 8) |
           ((uint32_t)test_pattern(addr + 2u) << 16) | ((uint32_t)test_pattern(addr + 3u) << 24);
}

/* ---------------------------------------------------------------- comm.c の代わり */

uint8_t comm_board_id(void)
{
    return g_board_id;
}

uint8_t comm_process_request(uint16_t can_id, uint8_t dlc, const uint8_t *data,
                             comm_frame_t *resp, uint8_t resp_max)
{
    g_cr_calls++;
    g_cr_id = can_id;
    g_cr_dlc = dlc;
    memset(g_cr_data, 0, sizeof g_cr_data);
    if (dlc > 0u) {
        memcpy(g_cr_data, data, dlc);
    }
    if (g_cr_resp_n > 0u && resp_max > 0u) {
        resp[0] = g_cr_resp;
        return 1u;
    }
    return 0u;
}
