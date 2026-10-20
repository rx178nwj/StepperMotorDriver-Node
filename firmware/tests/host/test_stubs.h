/* テストコードとスタブ（stubs.c）の共有宣言 */
#ifndef TEST_STUBS_H
#define TEST_STUBS_H

#include "comm.h"
#include "symtab.h"
#include <stdbool.h>
#include <stdint.h>

#define TEST_SYM_N    4u    /* stubs.c が確保する名前表のエントリ数（アセンブラで固定） */
#define TEST_BUILD_ID_NOTE_LEN  36u   /* namesz/descsz/type 12 + "GNU\0" 4 + SHA1 20 */

#define SENT_MAX  64u

typedef struct {
    uint8_t  bytes[260];
    uint16_t len;
} sent_frame_t;

/* ---- テストが操作する状態 ---- */
extern uint32_t g_tick;                 /* HAL_GetTick の戻り値 */
extern int g_tx_fail;                   /* 1 なら CDC_Transmit_FS が USBD_BUSY を返す */
extern sent_frame_t g_sent[SENT_MAX];   /* CDC_Transmit_FS に渡されたフレームの記録 */
extern unsigned g_sent_n;
extern unsigned g_primask_sets;         /* __set_PRIMASK の呼び出し回数 */
extern uint32_t g_primask;
extern unsigned g_rd8_calls, g_rd32_calls;
extern uint32_t g_ecc_addr;             /* このアドレスを含む読み出しで nv_ecc_error を立てる（0 なら無効） */

extern uint8_t g_board_id;
extern unsigned g_cr_calls;             /* comm_process_request の呼び出し回数 */
extern uint16_t g_cr_id;
extern uint8_t g_cr_dlc;
extern uint8_t g_cr_data[8];
extern uint8_t g_cr_resp_n;             /* comm_process_request が返す応答数（0 または 1） */
extern comm_frame_t g_cr_resp;

/* USB ミドルウェアの送信完了を模擬する（TxState = 0） */
void test_tx_complete(void);
/* 全スタブ状態を初期化（g_tick は進めたまま） */
void test_stubs_reset(void);
/* 名前表（__sym_table_start〜end、TEST_SYM_N 個）と build-id ノートの書き込み */
void test_sym_set(const sym_entry_t *src);
uint8_t *test_build_id_note(void);
/* 偽メモリの内容（アドレスだけで決まる） */
uint8_t test_pattern(uint32_t addr);

#endif /* TEST_STUBS_H */
