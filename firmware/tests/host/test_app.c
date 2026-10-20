/*
 * app.c のレジスタ処理（app_reg_read / app_reg_write / app_broadcast_command）のホスト単体テスト
 * （要求仕様 3.8 節 FR-COM-06・08 とレジスタ表の注記、FR-NV、試験 9・15）。
 * 期待値は requirements.md から決める。
 * app.c と settings.c は実物。stepper・drv・monitor・position・ui・comm・board は偽物（呼び出しの記録と状態の保持だけ）。
 * Flash は空（保存設定なし）として模擬する。
 * ビルドと実行は run_tests.sh / run_tests.ps1（-fshort-enums は app.c の SYM_ENTRY(state) が 1 バイトを要求するため）。
 */
#include "app.h"
#include "board.h"
#include "config.h"
#include "drv.h"
#include "monitor.h"
#include "position.h"
#include "settings.h"
#include "stepper.h"
#include "stm32l4xx_hal.h"
#include "ui.h"
#include "comm.h"
#include <stdio.h>
#include <string.h>

/* ---------------------------------------------------------------- 検査用マクロ */

static int g_checks;
static int g_fail;

#define CHECK(cond) \
    do { \
        g_checks++; \
        if (!(cond)) { \
            g_fail++; \
            printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        } \
    } while (0)

#define RUN(fn) \
    do { \
        int before_ = g_fail; \
        fn(); \
        printf("%s %s\n", (g_fail == before_) ? "PASS" : "FAIL", #fn); \
    } while (0)

/* ---------------------------------------------------------------- 偽物：stepper */

/* 呼び出しの記録（順序を見るため文字で残す） */
static char g_calls[64];
static unsigned g_calls_n;
static int32_t g_to, g_by, g_run, g_setpos_arg;
static bool g_running, g_pending, g_setpos_ok = true;
static int32_t g_pos;
static unsigned g_pos_calls, g_speed_calls;

static void log_call(char c)
{
    if (g_calls_n < sizeof g_calls - 1u) {
        g_calls[g_calls_n++] = c;
        g_calls[g_calls_n] = 0;
    }
}

void stepper_init(void) {}
void stepper_set_params(const stepper_params_t *p) { (void)p; }
void stepper_move_to(int32_t t) { g_to = t; log_call('T'); }
void stepper_move_by(int32_t d) { g_by = d; log_call('B'); }
void stepper_run(int32_t s) { g_run = s; log_call('R'); }
void stepper_stop(void) { log_call('S'); }
void stepper_estop(void) { log_call('E'); }
bool stepper_service(void) { return false; }
bool stepper_has_pending(void) { return g_pending; }
bool stepper_is_running(void) { return g_running; }
bool stepper_set_position(int32_t p)
{
    g_setpos_arg = p;
    log_call('P');
    if (!g_setpos_ok) {
        return false;
    }
    g_pos = p;
    return true;
}
bool stepper_at_target(void) { return false; }
bool stepper_touched(void) { return false; }
void stepper_timer_isr(void) {}

/* 読み出しのたびに値が変わる（スナップショットの検査用）。n 回目の呼び出しは pos = 0x01020304 + n × 0x04040404 */
static bool g_vary;
int32_t stepper_position(void)
{
    if (!g_vary) {
        return g_pos;
    }
    return (int32_t)(0x01020304u + (g_pos_calls++) * 0x04040404u);
}

int32_t stepper_speed(void)
{
    if (!g_vary) {
        return 0;
    }
    return (int32_t)(0x11223344u + (g_speed_calls++) * 0x01010101u);
}

/* ---------------------------------------------------------------- 偽物：drv、monitor、position、ui、comm、board */

static bool g_awake;
static bool g_agc;                  /* AGC0/AGC1 の端子レベル（両方同じ。FR-AGC-02） */
static unsigned g_agc_calls;        /* drv_set_agc の呼び出し回数 */
static unsigned g_arm_calls;        /* drv_fault_irq_arm の呼び出し回数 */
static drv_fault_t g_lo_fault;      /* drv_read_fault が返す値 */
/* drv の呼び出し順序（W 起床、Z スタンバイ、N 励磁 ON、D 励磁 OFF、A AGC 両方 High、a AGC 両方 Low） */
static char g_dlog[64];
static unsigned g_dlog_n;
static void dlog(char c)
{
    if (g_dlog_n < sizeof g_dlog - 1u) {
        g_dlog[g_dlog_n++] = c;
        g_dlog[g_dlog_n] = 0;
    }
}
static void dlog_clear(void)
{
    g_dlog_n = 0;
    g_dlog[0] = 0;
}
bool drv_microstep_valid(uint8_t m) { return m == 1u || m == 2u || m == 4u || m == 8u || m == 16u || m == 32u; }
void drv_init(void) {}
void drv_wake(uint8_t m) { (void)m; g_awake = true; dlog('W'); }
void drv_standby(void) { g_awake = false; dlog('Z'); }
bool drv_is_awake(void) { return g_awake; }
void drv_enable(bool on) { dlog(on ? 'N' : 'D'); }
bool drv_is_enabled(void) { return false; }
void drv_set_agc(bool on) { g_agc = on; g_agc_calls++; dlog(on ? 'A' : 'a'); }
bool drv_get_agc(void) { return g_agc; }
void drv_set_current_ma(uint16_t ma, uint32_t v) { (void)ma; (void)v; }
void drv_set_current_min(void) {}
void drv_pulse_reset(void) {}
drv_fault_t drv_read_fault(void) { return g_lo_fault; }
void drv_fault_irq_arm(bool a) { (void)a; g_arm_calls++; }

static uint32_t g_vm_mv = 24000;
void monitor_init(void) {}
void monitor_sample(monitor_values_t *out, bool ad)
{
    (void)ad;
    out->vdda_mv = 3300u;
    out->vm_mv = g_vm_mv;
    out->board_temp_dc = 250;
    out->mcu_temp_dc = 300;
    out->ad_in_mv = 0xFFFFu;
}

void position_init(void) {}
void position_poll_10ms(uint32_t n) { (void)n; }
bool position_valid(void) { return false; }
uint16_t position_raw(void) { return 0xFFFFu; }
uint8_t position_status_bits(void) { return 0; }
void position_pa7_set(uint8_t m, bool d) { (void)m; (void)d; }
uint16_t position_axis_count(uint16_t r, uint16_t o) { (void)o; return r; }
uint16_t position_shaft_angle(uint16_t r, uint16_t o) { (void)r; (void)o; return 0xFFFFu; }
bool position_sync_steps(uint16_t a, uint16_t g, uint16_t s, uint8_t m, int32_t *o)
{
    (void)a; (void)g; (void)s; (void)m;
    *o = 0;
    return true;
}

void ui_update(uint32_t n, const ui_view_t *v) { (void)n; (void)v; }
void ui_scan_switches(void) {}
bool ui_sw1(void) { return false; }
bool ui_sw2(void) { return false; }

void comm_init(uint8_t id) { (void)id; }
void comm_poll(void) {}
void comm_notify_fault(uint8_t s, uint8_t f) { (void)s; (void)f; }
uint32_t comm_last_request_ms(void) { return 0; }
uint32_t comm_activity_count(void) { return 0; }
bool comm_bus_degraded(void) { return false; }

uint8_t board_read_address_id(void) { return 5; }
bool board_pwr_external(void) { return false; }

/* ---------------------------------------------------------------- 偽物：Flash（既定は保存設定なし） */

FLASH_TypeDef g_flash_regs;

#define NV_PAGE_BYTES 2048u
static uint8_t g_nv[2u * NV_PAGE_BYTES];   /* 設定領域（2 ページ）。nv_clear で消去状態（保存設定なし）にする */

void test_nv_memcpy(void *dst, uint32_t addr, uint32_t n)
{
    uint32_t off = addr - NV_BASE;
    if (off + n <= sizeof g_nv) {
        memcpy(dst, &g_nv[off], n);
    } else {
        memset(dst, 0xFF, n);
    }
}

static void nv_clear(void)
{
    memset(g_nv, 0xFF, sizeof g_nv);
}

static uint32_t crc32_ref(const uint8_t *p, uint32_t n)
{
    uint32_t c = 0xFFFFFFFFu;
    while (n--) {
        c ^= *p++;
        for (int k = 0; k < 8; k++) {
            c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u)));
        }
    }
    return ~c;
}

/* 保存設定（版 3、連番 1）を 1 件置く。レコードは settings.c と同じ 64 バイト（"NESM"、版、大きさ、連番、設定、CRC） */
static void nv_put(const settings_t *s)
{
    uint8_t rec[64];
    memset(rec, 0xFF, sizeof rec);
    uint32_t magic = 0x4D53454Eu;
    uint16_t ver = 3, size = 40;
    uint32_t seq = 1;
    memcpy(rec + 0, &magic, 4);
    memcpy(rec + 4, &ver, 2);
    memcpy(rec + 6, &size, 2);
    memcpy(rec + 8, &seq, 4);
    memcpy(rec + 16, s, 40);
    uint32_t crc = crc32_ref(rec, 60);
    memcpy(rec + 60, &crc, 4);
    memcpy(g_nv, rec, sizeof rec);
}

HAL_StatusTypeDef HAL_FLASH_Unlock(void) { return HAL_OK; }
HAL_StatusTypeDef HAL_FLASH_Lock(void) { return HAL_OK; }
HAL_StatusTypeDef HAL_FLASH_Program(uint32_t t, uint32_t a, uint64_t d)
{
    (void)t; (void)a; (void)d;
    return HAL_ERROR;
}
HAL_StatusTypeDef HAL_FLASHEx_Erase(FLASH_EraseInitTypeDef *i, uint32_t *e)
{
    (void)i; (void)e;
    return HAL_ERROR;
}

/* ---------------------------------------------------------------- テストの道具 */

static uint32_t g_now = 1000;

static void tick10(unsigned n)
{
    while (n--) {
        g_now += 10u;
        app_tick_10ms(g_now);
    }
}

/* 起動して、停止中の DISABLED にする（保存設定は nv_clear / nv_put で用意しておく） */
static void boot(void)
{
    g_calls_n = 0;
    g_calls[0] = 0;
    g_running = g_pending = false;
    g_setpos_ok = true;
    g_vary = false;
    g_pos = 0;
    g_vm_mv = 24000;
    g_awake = false;
    g_agc = false;              /* リセット直後は Low（FR-SYS-02） */
    g_agc_calls = g_arm_calls = 0;
    g_lo_fault = DRV_FAULT_NONE;
    dlog_clear();
    app_init();                 /* 保存設定を読み込む。なければ工場出荷値。state = NO_VM */
    tick10(6);                  /* VM が安定するまで 50 ms。NO_VM → DISABLED */
}

/* 設定を工場出荷値にし、停止中の DISABLED にする */
static void setup(void)
{
    nv_clear();
    boot();
}

static void tick1(unsigned n)
{
    while (n--) {
        g_now += 1u;
        app_tick_1ms(g_now);
    }
}

static uint8_t rd8(uint8_t reg)
{
    uint8_t b;
    app_reg_read(reg, 1, &b);
    return b;
}

static uint16_t rd16(uint8_t reg)
{
    uint8_t b[2];
    app_reg_read(reg, 2, b);
    return (uint16_t)(b[0] | (b[1] << 8));
}

static uint32_t rd32(uint8_t reg)
{
    uint8_t b[4];
    app_reg_read(reg, 4, b);
    return (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
}

static uint8_t wr(uint8_t reg, const uint8_t *d, uint8_t len)
{
    return app_reg_write(reg, d, len);
}

static uint8_t wr8(uint8_t reg, uint8_t v)
{
    return app_reg_write(reg, &v, 1);
}

static uint8_t wr16(uint8_t reg, uint16_t v)
{
    uint8_t b[2] = {(uint8_t)v, (uint8_t)(v >> 8)};
    return app_reg_write(reg, b, 2);
}

static uint8_t wr32(uint8_t reg, uint32_t v)
{
    uint8_t b[4] = {(uint8_t)v, (uint8_t)(v >> 8), (uint8_t)(v >> 16), (uint8_t)(v >> 24)};
    return app_reg_write(reg, b, 4);
}

#define R_STATE      0x04
#define R_CMD_RESULT 0x07
#define R_CFG_STATUS 0x18
#define R_FAULT      0x05

static void to_enabled(void)
{
    setup();
    CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
    CHECK(rd8(R_STATE) == APP_ENABLED);
}

/* ---------------------------------------------------------------- 読み出し：固定値・既定値・マップ外 */

static void test_read_fixed_and_defaults(void)
{
    setup();
    CHECK(rd8(0x00) == DEVICE_ID);
    CHECK(rd8(0x00) == 0xA3);                 /* DEVICE_ID: 0xA3 固定 */
    CHECK(rd8(0x01) == FW_VERSION_MAJOR);
    CHECK(rd8(0x02) == FW_VERSION_MINOR);
    CHECK(rd8(0x03) == 5);                    /* BOARD_ID: DIP 値 */
    CHECK(rd8(R_STATE) == APP_DISABLED);
    CHECK(rd8(0x05) == 0);                    /* FAULT */
    CHECK(rd8(0x06) == 0);                    /* STATUS */
    CHECK(rd8(R_CMD_RESULT) == 0);

    /* 工場出荷値（レジスタ表） */
    CHECK(rd16(0x20) == 1000);
    CHECK(rd16(0x22) == 500);
    CHECK(rd8(0x24) == 16);
    CHECK(rd8(0x25) == 0x02);
    CHECK(rd16(0x26) == 0);
    CHECK(rd32(0x28) == 3200);
    CHECK(rd32(0x2C) == 16000);
    CHECK(rd32(0x30) == 16000);
    CHECK(rd32(0x34) == 200);
    CHECK(rd32(0x38) == 1600);
    CHECK(rd16(0x3C) == 500);
    CHECK(rd16(0x3E) == 200);
    CHECK(rd16(0x54) == 20);
    CHECK(rd16(0x56) == 0);
    CHECK(rd8(0x58) == 1);
    CHECK(rd8(0x1E) == 0);                    /* INPUT_STATUS（J10 未使用） */
    CHECK(rd8(0x59) == 0);                    /* J10_MODE: 0（未使用） */
    CHECK(rd16(0x5A) == 800);                 /* HOME_SPEED */
    CHECK(rd32(0x5C) == 0);                   /* ENC_COUNT */
}

static void test_read_little_endian_and_span(void)
{
    setup();
    CHECK(wr32(0x28, 0x00012345u) == RESULT_OK);   /* 74565 step/s */
    uint8_t b[6];
    app_reg_read(0x28, 4, b);
    CHECK(b[0] == 0x45 && b[1] == 0x23 && b[2] == 0x01 && b[3] == 0x00);   /* リトルエンディアン */
    CHECK(wr16(0x22, 0x0123) == RESULT_OK);
    app_reg_read(0x22, 2, b);
    CHECK(b[0] == 0x23 && b[1] == 0x01);

    /* 複数レジスタにまたがる読み出し：0x20 から 6 バイト = RUN(2) HOLD(2) MICROSTEP FLAGS */
    app_reg_read(0x20, 6, b);
    CHECK(b[0] == (1000 & 0xFF) && b[1] == (1000 >> 8));
    CHECK(b[2] == 0x23 && b[3] == 0x01);
    CHECK(b[4] == 16 && b[5] == 0x02);
    /* レジスタの途中から読む */
    app_reg_read(0x29, 3, b);
    CHECK(b[0] == 0x23 && b[1] == 0x01 && b[2] == 0x00);
    /* 長さ 1〜6 */
    for (uint8_t len = 1; len <= 6; len++) {
        uint8_t o[6] = {0};
        app_reg_read(0x00, len, o);
        CHECK(o[0] == 0xA3);
    }
}

static void test_read_out_of_map_and_zero_regs(void)
{
    setup();
    uint8_t b[6];
    /* マップ外（0x60 以降）は 0xFF。0xFF をまたぐ読み出しも */
    app_reg_read(0x60, 6, b);
    for (int i = 0; i < 6; i++) CHECK(b[i] == 0xFF);
    app_reg_read(0xFA, 6, b);
    for (int i = 0; i < 6; i++) CHECK(b[i] == 0xFF);
    app_reg_read(0x80, 1, b);
    CHECK(b[0] == 0xFF);
    /* マップの内側から外側へまたがる読み出し：外側のバイトは 0xFF（0x5E〜0x5F は仕様ではマップ内） */
    app_reg_read(0x5E, 6, b);
    for (int i = 2; i < 6; i++) CHECK(b[i] == 0xFF);

    /* 0x40〜0x50 は書き込み専用で、読み出すと 0。値を書いた後も 0 */
    CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
    CHECK(wr32(0x4C, 12345u) == RESULT_OK);
    CHECK(wr32(0x44, 777u) == RESULT_OK);
    app_reg_read(0x40, 6, b);
    for (int i = 0; i < 6; i++) CHECK(b[i] == 0);
    app_reg_read(0x46, 6, b);
    for (int i = 0; i < 6; i++) CHECK(b[i] == 0);
    CHECK(rd32(0x4C) == 0);
    CHECK(rd8(0x50) == 0);
    /* 0x1E（INPUT_STATUS、J10 未使用で 0）、0x1F は予約（0） */
    CHECK(rd8(0x1E) == 0);
    CHECK(rd8(0x1F) == 0);
}

/* ---------------------------------------------------------------- 書き込み：範囲 */

typedef struct {
    uint8_t reg;
    uint8_t size;
    uint32_t lo, hi;        /* 受け付ける範囲 */
    uint32_t bad_lo, bad_hi;/* 範囲外の値（bad_lo == bad_hi の 2 点を見る。0xFFFFFFFF なら無効） */
} range_t;

static uint32_t rd_n(uint8_t reg, uint8_t size)
{
    return size == 1 ? rd8(reg) : (size == 2 ? rd16(reg) : rd32(reg));
}

static uint8_t wr_n(uint8_t reg, uint8_t size, uint32_t v)
{
    return size == 1 ? wr8(reg, (uint8_t)v) : (size == 2 ? wr16(reg, (uint16_t)v) : wr32(reg, v));
}

static void test_write_ranges(void)
{
    /* レジスタ表の範囲（3.8 節）。範囲外の値は反映せず CMD_RESULT=2 */
    static const range_t t[] = {
        {0x20, 2, 250, 3000, 249, 3001},
        {0x22, 2, 250, 3000, 249, 3001},
        {0x28, 4, 1, 100000, 0, 100001},
        {0x2C, 4, 1, 10000000, 0, 10000001},
        {0x30, 4, 1, 10000000, 0, 10000001},
        {0x34, 4, 1, 100000, 0, 100001},
        {0x38, 4, 1, 100000, 0, 100001},
        {0x54, 2, 1, 1000, 0, 1001},
        {0x56, 2, 0, 4095, 4096, 65535},
        {0x58, 1, 0, 2, 3, 255},
        {0x59, 1, 0, 2, 3, 255},
        {0x5A, 2, 1, 65535, 0, 0},
    };
    for (unsigned i = 0; i < sizeof t / sizeof t[0]; i++) {
        const range_t *r = &t[i];
        setup();
        /* 範囲の端は受け付ける */
        CHECK(wr_n(r->reg, r->size, r->lo) == RESULT_OK);
        CHECK(rd_n(r->reg, r->size) == r->lo);
        CHECK(rd8(R_CMD_RESULT) == RESULT_OK);
        CHECK(wr_n(r->reg, r->size, r->hi) == RESULT_OK);
        CHECK(rd_n(r->reg, r->size) == r->hi);
        /* 範囲外は反映せず 2 */
        uint32_t keep = rd_n(r->reg, r->size);
        CHECK(wr_n(r->reg, r->size, r->bad_lo) == RESULT_INVALID);
        CHECK(rd_n(r->reg, r->size) == keep);
        CHECK(rd8(R_CMD_RESULT) == RESULT_INVALID);
        CHECK(wr_n(r->reg, r->size, r->bad_hi) == RESULT_INVALID);
        CHECK(rd_n(r->reg, r->size) == keep);
        /* 次の正しい書き込みで CMD_RESULT は 0 に戻る */
        CHECK(wr_n(r->reg, r->size, r->lo) == RESULT_OK);
        CHECK(rd8(R_CMD_RESULT) == RESULT_OK);
    }
}

/* 試験 15：RUN_CURRENT 3500 → 反映せず CMD_RESULT=2 */
static void test_run_current_3500(void)
{
    setup();
    CHECK(wr16(0x20, 3500) == RESULT_INVALID);
    CHECK(rd8(R_CMD_RESULT) == 2);
    CHECK(rd16(0x20) == 1000);
}

static void test_write_microstep_and_flags(void)
{
    setup();
    static const uint8_t ok[] = {1, 2, 4, 8, 16, 32};
    for (unsigned i = 0; i < sizeof ok; i++) {
        CHECK(wr8(0x24, ok[i]) == RESULT_OK);
        CHECK(rd8(0x24) == ok[i]);
    }
    static const uint8_t ng[] = {0, 3, 5, 6, 7, 12, 64, 128, 255};
    CHECK(wr8(0x24, 8) == RESULT_OK);
    for (unsigned i = 0; i < sizeof ng; i++) {
        CHECK(wr8(0x24, ng[i]) == RESULT_INVALID);
        CHECK(rd8(0x24) == 8);
    }
    /* FLAGS：bit0 方向反転、bit1 ローカルジョグ、bit2 センサ方向 */
    for (uint8_t f = 0; f <= 7u; f++) {
        CHECK(wr8(0x25, f) == RESULT_OK);
        CHECK(rd8(0x25) == f);
    }
    /* 未定義のビット（bit5〜7）を含む値は反映しない */
    CHECK(wr8(0x25, 0x03) == RESULT_OK);
    CHECK(wr8(0x25, 0x83) == RESULT_INVALID);
    CHECK(wr8(0x25, 0x23) == RESULT_INVALID);
    CHECK(rd8(0x25) == 0x03);
    /* 16bit の全範囲が有効なレジスタ */
    CHECK(wr16(0x26, 0) == RESULT_OK);
    CHECK(wr16(0x26, 65535) == RESULT_OK);
    CHECK(rd16(0x26) == 65535);
    CHECK(wr16(0x3C, 0) == RESULT_OK);
    CHECK(wr16(0x3C, 65535) == RESULT_OK);
    CHECK(rd16(0x3C) == 65535);
    CHECK(wr16(0x3E, 1) == RESULT_OK);
    CHECK(wr16(0x3E, 65535) == RESULT_OK);
    CHECK(rd16(0x3E) == 65535);
}

/* 分割の変更は停止中のみ（レジスタ表 0x24） */
static void test_microstep_only_when_stopped(void)
{
    to_enabled();
    g_running = true;
    CHECK(wr8(0x24, 8) == RESULT_REJECTED);
    CHECK(rd8(R_CMD_RESULT) == RESULT_REJECTED);
    CHECK(rd8(0x24) == 16);
    g_running = false;
    g_pending = true;
    CHECK(wr8(0x24, 8) == RESULT_REJECTED);
    CHECK(rd8(0x24) == 16);
    g_pending = false;
    CHECK(wr8(0x24, 8) == RESULT_OK);
    CHECK(rd8(0x24) == 8);
    /* 移動中でも他の設定は変えられる */
    g_running = true;
    CHECK(wr16(0x20, 1500) == RESULT_OK);
    g_running = false;
}

/* ---------------------------------------------------------------- 書き込み：32bit の全バイト条件、複数レジスタ（FR-COM-08） */

static void test_partial_32bit_writes(void)
{
    static const uint8_t regs[] = {0x28, 0x2C, 0x30, 0x34, 0x38};
    const uint8_t d[6] = {0x11, 0x22, 0x00, 0x00, 0x55, 0x66};   /* 4 バイトで 0x2211 = 8721（全レジスタの範囲内） */
    for (unsigned i = 0; i < sizeof regs; i++) {
        setup();
        uint32_t before = rd32(regs[i]);
        /* 1〜3 バイトだけの書き込みは反映せず 2 */
        for (uint8_t len = 1; len <= 3u; len++) {
            CHECK(wr(regs[i], d, len) == RESULT_INVALID);
            CHECK(rd8(R_CMD_RESULT) == RESULT_INVALID);
            CHECK(rd32(regs[i]) == before);
        }
        /* 下位側に外れる（レジスタの途中から始まる）書き込みも反映しない */
        CHECK(wr((uint8_t)(regs[i] + 1u), d, 3) == RESULT_INVALID);
        CHECK(wr((uint8_t)(regs[i] + 2u), d, 2) == RESULT_INVALID);
        CHECK(rd32(regs[i]) == before);
        /* 4 バイト全てなら反映 */
        CHECK(wr(regs[i], d, 4) == RESULT_OK);
        CHECK(rd32(regs[i]) == 0x2211u);
    }
}

static void test_partial_move_commands(void)
{
    static const uint8_t regs[] = {0x40, 0x44, 0x48, 0x4C};
    const uint8_t d[4] = {0x01, 0x00, 0x00, 0x00};
    for (unsigned i = 0; i < sizeof regs; i++) {
        to_enabled();
        g_calls_n = 0;
        g_calls[0] = 0;
        for (uint8_t len = 1; len <= 3u; len++) {
            CHECK(wr(regs[i], d, len) == RESULT_INVALID);
        }
        CHECK(g_calls_n == 0u);   /* 部分書き込みでは動作指令を実行しない */
        CHECK(wr(regs[i], d, 4) == RESULT_OK);
        CHECK(g_calls_n == 1u);
    }
    /* 移動指令の値は符号付き 32bit（リトルエンディアン） */
    to_enabled();
    CHECK(wr32(0x40, 0xFFFFFFFEu) == RESULT_OK);
    CHECK(g_to == -2);
    CHECK(wr32(0x44, 0x80000000u) == RESULT_OK);
    CHECK(g_by == INT32_MIN);
    CHECK(wr32(0x48, 0x7FFFFFFFu) == RESULT_OK);
    CHECK(g_run == INT32_MAX);
    CHECK(wr32(0x4C, 0xFFFFFF00u) == RESULT_OK);
    CHECK(g_setpos_arg == -256);
}

/* 16bit レジスタ：仕様 FR-COM-08 は 32bit だけを規定している。実装は 16bit も全バイトでなければ反映しない */
static void test_partial_16bit_writes(void)
{
    static const uint8_t regs[] = {0x20, 0x22, 0x26, 0x3C, 0x3E, 0x54, 0x56, 0x5A};
    for (unsigned i = 0; i < sizeof regs; i++) {
        setup();
        uint16_t before = rd16(regs[i]);
        CHECK(wr8(regs[i], 0x77) == RESULT_INVALID);
        CHECK(rd16(regs[i]) == before);
        CHECK(wr8((uint8_t)(regs[i] + 1u), 0x01) == RESULT_INVALID);
        CHECK(rd16(regs[i]) == before);
    }
}

static void test_multi_register_write(void)
{
    setup();
    /* 0x24 MICROSTEP と 0x25 FLAGS を 1 フレームで */
    const uint8_t d1[] = {4, 0x05};
    CHECK(wr(0x24, d1, 2) == RESULT_OK);
    CHECK(rd8(0x24) == 4);
    CHECK(rd8(0x25) == 0x05);
    CHECK(rd8(R_CMD_RESULT) == RESULT_OK);

    /* 0x20 から 6 バイト：RUN、HOLD、MICROSTEP、FLAGS */
    const uint8_t d2[] = {0xDC, 0x05, 0x58, 0x02, 0x08, 0x03};   /* 1500, 600, 8, 3 */
    CHECK(wr(0x20, d2, 6) == RESULT_OK);
    CHECK(rd16(0x20) == 1500);
    CHECK(rd16(0x22) == 600);
    CHECK(rd8(0x24) == 8);
    CHECK(rd8(0x25) == 0x03);

    /* 一部が範囲外：範囲外のレジスタだけ反映せず、結果は 2。ほかは反映する */
    const uint8_t d3[] = {0xAC, 0x0D, 0x58, 0x02};   /* RUN 3500（範囲外）、HOLD 600 */
    CHECK(wr(0x20, d3, 4) == RESULT_INVALID);
    CHECK(rd16(0x20) == 1500);
    CHECK(rd16(0x22) == 600);
    const uint8_t d4[] = {0xB8, 0x0B, 0x00, 0x00};   /* RUN 3000、HOLD 0（範囲外） */
    CHECK(wr(0x20, d4, 4) == RESULT_INVALID);
    CHECK(rd16(0x20) == 3000);
    CHECK(rd16(0x22) == 600);

    /* 32bit レジスタ全体 + 次のレジスタの一部（0x28 は 4 バイトそろい、0x2C は 2 バイトだけ）：結果 2、0x28 だけ反映 */
    const uint8_t d5[] = {0x10, 0x27, 0x00, 0x00, 0x01, 0x00};   /* MAX_SPEED 10000 */
    uint32_t accel = rd32(0x2C);
    CHECK(wr(0x28, d5, 6) == RESULT_INVALID);
    CHECK(rd32(0x28) == 10000);
    CHECK(rd32(0x2C) == accel);

    /* 2 つの 32bit レジスタにまたがる中途半端な書き込み：どちらも反映しない */
    uint32_t mx = rd32(0x28);
    CHECK(wr(0x2A, d5, 4) == RESULT_INVALID);
    CHECK(rd32(0x28) == mx);
    CHECK(rd32(0x2C) == accel);

    /* アドレス昇順で反映する：0x4C（SET_POSITION）の後に 0x50（COMMAND）が実行される */
    to_enabled();
    g_calls_n = 0;
    g_calls[0] = 0;
    const uint8_t d6[] = {0x0A, 0x00, 0x00, 0x00, CMD_STOP};
    CHECK(wr(0x4C, d6, 5) == RESULT_OK);
    CHECK(strcmp(g_calls, "PS") == 0);
    CHECK(g_setpos_arg == 10);
}

/* ---------------------------------------------------------------- 書き込み：読み出し専用・マップ外 */

static void test_write_read_only_and_unmapped(void)
{
    setup();
    static const uint8_t ro[] = {0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x0B, 0x0C, 0x10,
                                 0x12, 0x14, 0x16, 0x18, 0x19, 0x1A, 0x1C, 0x1E, 0x1F, 0x60, 0x7F, 0xFF};
    for (unsigned i = 0; i < sizeof ro; i++) {
        uint8_t before = rd8(ro[i]);
        CHECK(wr8(ro[i], 0x5A) == RESULT_INVALID);
        CHECK(rd8(R_CMD_RESULT) == RESULT_INVALID);
        if (ro[i] != R_CMD_RESULT) {
            CHECK(rd8(ro[i]) == before);   /* CMD_RESULT 以外は変わらない */
        }
    }
    /* 書き込み可能なレジスタの値を読み出し専用レジスタとまとめて書く：書き込み可能な側だけ反映し、結果は 2 */
    const uint8_t m[] = {0x10, 0x00};                      /* 0x1E,0x1F: 読み出し専用 */
    CHECK(wr(0x1E, m, 2) == RESULT_INVALID);
    const uint8_t mix[] = {0x00, 0x00, 0xE8, 0x03};        /* 0x1E,0x1F（読み出し専用）+ 0x20 RUN_CURRENT = 1000 */
    CHECK(wr16(0x20, 2000) == RESULT_OK);
    CHECK(wr(0x1E, mix, 4) == RESULT_INVALID);
    CHECK(rd16(0x20) == 1000);   /* 同じフレームの書き込み可能な側は反映する */
}

/* ---------------------------------------------------------------- 動作指令・COMMAND の結果 */

static void test_motion_commands_require_enabled(void)
{
    setup();   /* DISABLED */
    CHECK(wr32(0x40, 100) == RESULT_REJECTED);
    CHECK(wr32(0x44, 100) == RESULT_REJECTED);
    CHECK(wr32(0x48, 100) == RESULT_REJECTED);
    CHECK(rd8(R_CMD_RESULT) == RESULT_REJECTED);
    CHECK(g_calls_n == 0u);

    CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
    CHECK(rd8(R_STATE) == APP_ENABLED);
    CHECK(wr32(0x40, 100) == RESULT_OK);
    CHECK(g_to == 100);
    CHECK(wr32(0x44, (uint32_t)-50) == RESULT_OK);
    CHECK(g_by == -50);
    CHECK(wr32(0x48, 800) == RESULT_OK);
    CHECK(g_run == 800);

    /* SET_POSITION は停止中のみ */
    g_setpos_ok = false;
    CHECK(wr32(0x4C, 5) == RESULT_REJECTED);
    g_setpos_ok = true;
    CHECK(wr32(0x4C, 5) == RESULT_OK);
}

static void test_command_register(void)
{
    setup();
    CHECK(rd8(R_STATE) == APP_DISABLED);
    CHECK(wr8(0x50, CMD_DISABLE) == RESULT_REJECTED);   /* DISABLED では DISABLE は拒否 */
    CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
    CHECK(rd8(R_STATE) == APP_ENABLED);
    CHECK(wr8(0x50, CMD_ENABLE) == RESULT_REJECTED);
    g_calls_n = 0;
    g_calls[0] = 0;
    CHECK(wr8(0x50, CMD_STOP) == RESULT_OK);
    CHECK(strcmp(g_calls, "S") == 0);
    CHECK(wr8(0x50, CMD_ESTOP) == RESULT_OK);
    CHECK(strcmp(g_calls, "SE") == 0);
    CHECK(wr8(0x50, CMD_DISABLE) == RESULT_OK);
    CHECK(rd8(R_STATE) == APP_DISABLED);
    CHECK(wr8(0x50, CMD_CLEAR_FAULT) == RESULT_REJECTED);   /* FAULT 以外では拒否 */
    /* 未定義のコマンド */
    CHECK(wr8(0x50, 0) == RESULT_INVALID);
    CHECK(wr8(0x50, 99) == RESULT_INVALID);
    CHECK(wr8(0x50, 255) == RESULT_INVALID);
    CHECK(rd8(R_CMD_RESULT) == RESULT_INVALID);
    /* 保存設定がないとき LOAD は 3（保存設定なし） */
    CHECK(wr8(0x50, CMD_LOAD_SETTINGS) == RESULT_FAILED);
    CHECK(rd8(R_CMD_RESULT) == RESULT_FAILED);
    /* 動作中は設定の保存・読み込み・工場出荷値を拒否 */
    g_running = true;
    CHECK(wr8(0x50, CMD_SAVE_SETTINGS) == RESULT_REJECTED);
    CHECK(wr8(0x50, CMD_LOAD_SETTINGS) == RESULT_REJECTED);
    CHECK(wr8(0x50, CMD_FACTORY_SETTINGS) == RESULT_REJECTED);
    g_running = false;
    /* FACTORY_SETTINGS：工場出荷値に戻り、未保存の変更ありになる（CONFIG_STATUS bit1） */
    CHECK(wr16(0x20, 2000) == RESULT_OK);
    CHECK((rd8(R_CFG_STATUS) & 0x02) != 0);
    CHECK(wr8(0x50, CMD_FACTORY_SETTINGS) == RESULT_OK);
    CHECK(rd16(0x20) == 1000);
}

/* ブロードキャスト指令：CMD_RESULT は更新しない */
static void test_broadcast_command(void)
{
    setup();
    CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
    CHECK(wr32(0x40, 1) == RESULT_OK);
    CHECK(wr16(0x20, 3500) == RESULT_INVALID);
    CHECK(rd8(R_CMD_RESULT) == RESULT_INVALID);
    g_calls_n = 0;
    g_calls[0] = 0;
    app_broadcast_command(CMD_STOP);
    CHECK(strcmp(g_calls, "S") == 0);
    app_broadcast_command(CMD_ESTOP);
    CHECK(strcmp(g_calls, "SE") == 0);
    CHECK(rd8(R_CMD_RESULT) == RESULT_INVALID);   /* 変わらない */
    app_broadcast_command(CMD_DISABLE);
    CHECK(rd8(R_STATE) == APP_DISABLED);
    CHECK(rd8(R_CMD_RESULT) == RESULT_INVALID);
}

/* ---------------------------------------------------------------- 読み出しのスナップショット（FR-COM-06） */

/* 値が読み出しのたびに変わる状況で、1 フレーム内の多バイト値が別々の時点の値の混ざりにならない。
 * POSITION（0x08）と SPEED（0x0C）は app_reg_read の呼び出し 1 回につき 1 回ずつだけ取得される */
static void test_read_snapshot(void)
{
    setup();
    g_vary = true;
    g_pos_calls = g_speed_calls = 0;

    uint32_t last_pos = 0;
    for (unsigned i = 0; i < 20u; i++) {
        uint8_t b[6];
        unsigned p0 = g_pos_calls, s0 = g_speed_calls;
        app_reg_read(0x08, 6, b);   /* POSITION(4) + SPEED の下位 2 バイト */
        /* 1 回の読み出しで POSITION、SPEED を取得するのは 1 回ずつ */
        CHECK(g_pos_calls == p0 + 1u);
        CHECK(g_speed_calls == s0 + 1u);
        uint32_t pos = (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
        uint32_t expect_pos = 0x01020304u + p0 * 0x04040404u;
        CHECK(pos == expect_pos);
        uint32_t expect_speed = 0x11223344u + s0 * 0x01010101u;
        CHECK(b[4] == (uint8_t)expect_speed && b[5] == (uint8_t)(expect_speed >> 8));
        if (i > 0) {
            CHECK(pos != last_pos);   /* 値は実際に変化している（偽物が働いている） */
        }
        last_pos = pos;
    }
    /* SPEED の上位バイトと、そのあとのレジスタをまたぐ読み出し */
    uint8_t b[6];
    unsigned s0 = g_speed_calls;
    app_reg_read(0x0A, 6, b);   /* POSITION の上位 2 バイト + SPEED(4) */
    uint32_t sp = (uint32_t)b[2] | ((uint32_t)b[3] << 8) | ((uint32_t)b[4] << 16) | ((uint32_t)b[5] << 24);
    CHECK(sp == 0x11223344u + s0 * 0x01010101u);
    g_vary = false;
}

/* ---------------------------------------------------------------- 設定の変更が反映される（CONFIG_STATUS） */

static void test_config_status_dirty(void)
{
    setup();
    uint8_t before = rd8(R_CFG_STATUS);
    /* 値が変わらない書き込みでは未保存の変更ありにならない（初期状態が 0 のとき） */
    if ((before & 0x02) == 0) {
        CHECK(wr16(0x20, 1000) == RESULT_OK);
        CHECK((rd8(R_CFG_STATUS) & 0x02) == 0);
    }
    CHECK(wr16(0x20, 1100) == RESULT_OK);
    CHECK((rd8(R_CFG_STATUS) & 0x02) != 0);
    /* 保存設定なしで起動したので bit0（読み込み済み）は 0 */
    CHECK((rd8(R_CFG_STATUS) & 0x01) == 0);
}

/* ---------------------------------------------------------------- P2：J10 のレジスタ（0x1E、0x59〜0x5F） */

static void test_j10_registers(void)
{
    setup();
    /* マップの末尾は 0x5F。0x5F は ENC_COUNT の最上位バイト（0）、0x60 以降は 0xFF */
    uint8_t b[6];
    app_reg_read(0x5F, 2, b);
    CHECK(b[0] == 0 && b[1] == 0xFF);
    app_reg_read(0x58, 6, b);   /* PA7_MODE、J10_MODE、HOME_SPEED(2)、ENC_COUNT の下位 2 バイト */
    CHECK(b[0] == 1 && b[1] == 0 && b[2] == (800 & 0xFF) && b[3] == (800 >> 8) && b[4] == 0 && b[5] == 0);

    /* J10_MODE：0〜2、書いた値が読める。未保存の変更ありになる */
    CHECK(wr8(0x59, 1) == RESULT_OK);
    CHECK(rd8(0x59) == 1);
    CHECK((rd8(R_CFG_STATUS) & 0x02) != 0);
    CHECK(wr8(0x59, 2) == RESULT_OK);
    CHECK(rd8(0x59) == 2);
    CHECK(wr8(0x59, 3) == RESULT_INVALID);
    CHECK(rd8(R_CMD_RESULT) == RESULT_INVALID);
    CHECK(rd8(0x59) == 2);

    /* HOME_SPEED：1〜65535、0 は反映せず 2。1 バイトだけの書き込み（部分書き込み）は拒否 */
    CHECK(wr16(0x5A, 0x1234) == RESULT_OK);
    CHECK(rd16(0x5A) == 0x1234);
    CHECK(wr16(0x5A, 0) == RESULT_INVALID);
    CHECK(rd8(R_CMD_RESULT) == RESULT_INVALID);
    CHECK(rd16(0x5A) == 0x1234);
    CHECK(wr8(0x5A, 0x77) == RESULT_INVALID);
    CHECK(wr8(0x5B, 0x77) == RESULT_INVALID);
    CHECK(rd16(0x5A) == 0x1234);
    /* 0x58 から 4 バイトの 1 フレーム（PA7_MODE、J10_MODE、HOME_SPEED） */
    const uint8_t d[] = {0, 1, 0x20, 0x03};
    CHECK(wr(0x58, d, 4) == RESULT_OK);
    CHECK(rd8(0x58) == 0 && rd8(0x59) == 1 && rd16(0x5A) == 800);

    /* INPUT_STATUS（0x1E）と ENC_COUNT（0x5C〜0x5F）は読み出し専用：書くと 2、値は 0 のまま */
    for (uint8_t a = 0x5C; a <= 0x5F; a++) {
        CHECK(wr8(a, 0x5A) == RESULT_INVALID);
        CHECK(rd8(R_CMD_RESULT) == RESULT_INVALID);
    }
    CHECK(wr32(0x5C, 5) == RESULT_INVALID);
    CHECK(rd32(0x5C) == 0);
    CHECK(wr8(0x1E, 0xFF) == RESULT_INVALID);
    CHECK(rd8(0x1E) == 0);
    /* 0x60 以降は書けない */
    CHECK(wr8(0x60, 1) == RESULT_INVALID);
    CHECK(wr(0x5E, d, 4) == RESULT_INVALID);   /* マップの内側から外側へまたがる書き込み */
}

/* J10_MODE は停止中（パルス出力なし）のみ書ける。動作中は 1（FR-J10-02）。HOME_SPEED は制限しない */
static void test_j10_mode_only_when_stopped(void)
{
    to_enabled();
    g_running = true;
    CHECK(wr8(0x59, 1) == RESULT_REJECTED);
    CHECK(rd8(R_CMD_RESULT) == RESULT_REJECTED);
    CHECK(rd8(0x59) == 0);
    CHECK(wr16(0x5A, 1000) == RESULT_OK);
    g_running = false;
    g_pending = true;
    CHECK(wr8(0x59, 2) == RESULT_REJECTED);
    CHECK(rd8(0x59) == 0);
    g_pending = false;
    CHECK(wr8(0x59, 2) == RESULT_OK);   /* ENABLED でも停止中なら書ける */
    CHECK(rd8(0x59) == 2);
    CHECK(rd8(R_CMD_RESULT) == RESULT_OK);
}

/* FLAGS：bit3（AGC）・bit4（HOME 論理）は書ける。bit5〜7 は 2 */
static void test_flags_agc_home_bits(void)
{
    setup();
    for (uint8_t f = 0; f <= 0x1Fu; f++) {
        CHECK(wr8(0x25, f) == RESULT_OK);
        CHECK(rd8(0x25) == f);
    }
    CHECK(wr8(0x25, 0x1B) == RESULT_OK);
    CHECK(wr8(0x25, 0x38) == RESULT_INVALID);
    CHECK(wr8(0x25, 0x98) == RESULT_INVALID);
    CHECK(wr8(0x25, 0xFF) == RESULT_INVALID);
    CHECK(rd8(R_CMD_RESULT) == RESULT_INVALID);
    CHECK(rd8(0x25) == 0x1B);
}

/* ---------------------------------------------------------------- P2：AGC（FR-AGC-01〜06） */

/* DISABLED 中に bit3 を書くと即時に端子へ（両方同じレベル）。bit3 以外の変更では端子レベルを変えない */
static void test_agc_write_disabled(void)
{
    setup();
    CHECK(!g_agc);                       /* 既定は OFF（Low） */
    dlog_clear();
    CHECK(wr8(0x25, 0x0A) == RESULT_OK);
    CHECK(g_agc);
    CHECK(rd8(0x25) == 0x0A);
    CHECK(g_dlog[0] == 'A' && strchr(g_dlog, 'a') == NULL);   /* 起床中の DMODE 書き換え（W）が続いてもよい */
    CHECK(wr8(0x25, 0x0B) == RESULT_OK); /* bit3 は変えない */
    CHECK(g_agc);
    CHECK(wr8(0x25, 0x02) == RESULT_OK);
    CHECK(!g_agc);
    CHECK(rd8(0x25) == 0x02);
    CHECK(wr8(0x25, 0x1A) == RESULT_OK); /* bit4 も立てた状態で ON */
    CHECK(g_agc);
}

/* NO_VM・FAULT 中の書き込みも即時 */
static void test_agc_write_no_vm_and_fault(void)
{
    nv_clear();
    g_vm_mv = 24000;
    g_awake = false;
    app_init();                          /* tick しない: NO_VM のまま */
    CHECK(rd8(R_STATE) == APP_NO_VM);
    CHECK(wr8(0x25, 0x0A) == RESULT_OK);
    CHECK(g_agc);
    CHECK(wr8(0x25, 0x02) == RESULT_OK);
    CHECK(!g_agc);

    setup();
    CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
    app_on_driver_fault_isr(DRV_FAULT_ISD);
    tick1(1);
    CHECK(rd8(R_STATE) == APP_FAULT);
    CHECK(wr8(0x25, 0x0A) == RESULT_OK);
    CHECK(g_agc);
    CHECK(wr8(0x25, 0x02) == RESULT_OK);
    CHECK(!g_agc);
}

/* ENABLED 中に bit3 を変える書き込みは FLAGS 全体を反映せず 1。端子も設定も変わらない */
static void test_agc_write_enabled_rejected(void)
{
    for (int on = 0; on <= 1; on++) {
        setup();
        CHECK(wr8(0x25, on ? 0x0A : 0x02) == RESULT_OK);
        CHECK(g_agc == (on != 0));
        CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
        uint8_t keep = rd8(0x25);
        unsigned calls = g_agc_calls;
        uint8_t st = rd8(R_CFG_STATUS);
        /* bit3 だけ反転、bit3 と bit0 を同時に変更（bit0 も反映されないこと） */
        CHECK(wr8(0x25, (uint8_t)(keep ^ 0x08)) == RESULT_REJECTED);
        CHECK(rd8(R_CMD_RESULT) == RESULT_REJECTED);
        CHECK(rd8(0x25) == keep);
        CHECK(wr8(0x25, (uint8_t)(keep ^ 0x09)) == RESULT_REJECTED);
        CHECK(rd8(0x25) == keep);
        CHECK(g_agc == (on != 0));
        CHECK(g_agc_calls == calls);
        CHECK(rd8(R_CFG_STATUS) == st);
        /* bit3 を変えない FLAGS の書き込みは受け付け、端子には触れない */
        CHECK(wr8(0x25, (uint8_t)(keep ^ 0x01)) == RESULT_OK);
        CHECK(rd8(0x25) == (uint8_t)(keep ^ 0x01));
        CHECK(g_agc == (on != 0));
        CHECK(g_agc_calls == calls);
        /* 励磁 OFF になれば書ける */
        CHECK(wr8(0x50, CMD_DISABLE) == RESULT_OK);
        CHECK(wr8(0x25, (uint8_t)(keep ^ 0x08)) == RESULT_OK);
        CHECK(g_agc == (on == 0));
    }
}

/* ENABLED 中の FACTORY：RAM の設定は変わるが端子は変えず、DISABLE（EN=Low）／FAULT で反映する */
static void test_agc_factory_while_enabled(void)
{
    setup();
    CHECK(wr8(0x25, 0x0A) == RESULT_OK);   /* AGC ON（DISABLED 中なので即時） */
    CHECK(g_agc);
    CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
    dlog_clear();
    CHECK(wr8(0x50, CMD_FACTORY_SETTINGS) == RESULT_OK);   /* 励磁したままの停止で受け付ける */
    CHECK(rd8(0x25) == 0x02);              /* 設定は工場出荷値（AGC OFF）になる */
    CHECK(g_agc);                          /* 端子は変えない */
    CHECK(strchr(g_dlog, 'a') == NULL && strchr(g_dlog, 'A') == NULL);
    CHECK(rd8(R_STATE) == APP_ENABLED);

    dlog_clear();
    CHECK(wr8(0x50, CMD_DISABLE) == RESULT_OK);
    CHECK(!g_agc);                         /* EN=Low になったときに反映 */
    CHECK(strcmp(g_dlog, "Da") == 0);      /* 励磁 OFF の後に AGC を変える */

    /* FAULT への遷移で反映（LO 割り込みは EN=Low にするだけで、main 側の enter_fault で反映） */
    setup();
    CHECK(wr8(0x25, 0x0A) == RESULT_OK);
    CHECK(g_agc);
    CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
    CHECK(wr8(0x50, CMD_FACTORY_SETTINGS) == RESULT_OK);
    CHECK(g_agc);
    app_on_driver_fault_isr(DRV_FAULT_OPD);
    CHECK(g_agc);
    tick1(1);
    CHECK(rd8(R_STATE) == APP_FAULT);
    CHECK(!g_agc);
}

/* ENABLED 中の LOAD：RAM の設定は保存値の AGC になるが、端子は DISABLE で反映 */
static void test_agc_load_while_enabled(void)
{
    for (int saved = 0; saved <= 1; saved++) {
        nv_clear();
        settings_t t;
        settings_defaults(&t);
        t.flags = saved ? 0x0A : 0x02;
        nv_put(&t);
        boot();
        CHECK((rd8(R_CFG_STATUS) & 0x01) != 0);   /* 起動時に保存設定を読み込んだ */
        CHECK(g_agc == (saved != 0));
        CHECK(wr8(0x25, saved ? 0x02 : 0x0A) == RESULT_OK);   /* DISABLED 中に反対にする（即時） */
        CHECK(g_agc == (saved == 0));
        CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
        dlog_clear();
        CHECK(wr8(0x50, CMD_LOAD_SETTINGS) == RESULT_OK);
        CHECK(rd8(0x25) == (saved ? 0x0A : 0x02));
        CHECK(g_agc == (saved == 0));                    /* 端子は変えない */
        CHECK(strchr(g_dlog, 'a') == NULL && strchr(g_dlog, 'A') == NULL);
        CHECK(wr8(0x50, CMD_DISABLE) == RESULT_OK);
        CHECK(g_agc == (saved != 0));
    }
    /* DISABLED 中の LOAD は即時 */
    nv_clear();
    settings_t t;
    settings_defaults(&t);
    t.flags = 0x0A;
    nv_put(&t);
    boot();
    CHECK(wr8(0x25, 0x02) == RESULT_OK);
    CHECK(!g_agc);
    CHECK(wr8(0x50, CMD_LOAD_SETTINGS) == RESULT_OK);
    CHECK(g_agc);
}

/* 起動・スタンバイ解除：drv_wake より前に保存値の AGC を端子へ出す（FR-AGC-03） */
static void test_agc_before_wake(void)
{
    for (int saved = 0; saved <= 1; saved++) {
        nv_clear();
        settings_t t;
        settings_defaults(&t);
        t.flags = saved ? 0x0A : 0x02;
        nv_put(&t);
        boot();
        CHECK(rd8(R_STATE) == APP_DISABLED);
        CHECK(g_agc == (saved != 0));
        CHECK(strcmp(g_dlog, saved ? "AW" : "aW") == 0);   /* 設定読み込み → AGC → 起床 */

        /* VM 喪失：EN=Low の後、スタンバイの前に AGC を出す。復帰で再び AGC → 起床 */
        dlog_clear();
        g_vm_mv = 0;
        tick10(3);
        CHECK(rd8(R_STATE) == APP_NO_VM);
        CHECK(strcmp(g_dlog, saved ? "DAZ" : "DaZ") == 0);
        dlog_clear();
        g_vm_mv = 24000;
        tick10(6);
        CHECK(rd8(R_STATE) == APP_DISABLED);
        CHECK(strcmp(g_dlog, saved ? "AW" : "aW") == 0);

        /* FAULT → CLEAR_FAULT のスタンバイ解除 */
        CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
        app_on_driver_fault_isr(DRV_FAULT_TSD);
        tick1(1);
        CHECK(rd8(R_STATE) == APP_FAULT);
        dlog_clear();
        CHECK(wr8(0x50, CMD_CLEAR_FAULT) == RESULT_OK);
        CHECK(strcmp(g_dlog, saved ? "AW" : "aW") == 0);
        CHECK(g_agc == (saved != 0));
    }
    /* 保存設定なし：OFF で起動 */
    setup();
    CHECK(!g_agc);
    CHECK(strcmp(g_dlog, "aW") == 0);
}

/* setup() の後、前のテストが残した FAULT レジスタ（app_init は消さない）を 0 にして DISABLED に戻す */
static void setup_clean_fault(void)
{
    setup();
    if (rd8(R_FAULT) != 0) {
        CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
        app_on_driver_fault_isr(DRV_FAULT_TSD);
        tick1(1);
        CHECK(wr8(0x50, CMD_CLEAR_FAULT) == RESULT_OK);
    }
    CHECK(rd8(R_FAULT) == 0);
    CHECK(rd8(R_STATE) == APP_DISABLED);
}

/* AGC の ON/OFF で LO 異常の検出・保護の動作は変わらない（FR-AGC-06） */
static void test_agc_does_not_change_fault_handling(void)
{
    for (int on = 0; on <= 1; on++) {
        /* LO 割り込み（ISD）→ FAULT、パルス即停止 */
        setup_clean_fault();
        CHECK(wr8(0x25, on ? 0x0A : 0x02) == RESULT_OK);
        CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
        unsigned arm = g_arm_calls;
        CHECK(wr8(0x25, on ? 0x0B : 0x03) == RESULT_OK);   /* FLAGS の書き込みは LO 割り込みの許可に触れない */
        CHECK(g_arm_calls == arm);
        g_calls_n = 0;
        g_calls[0] = 0;
        app_on_driver_fault_isr(DRV_FAULT_ISD);
        tick1(1);
        CHECK(rd8(R_STATE) == APP_FAULT);
        CHECK(rd8(R_FAULT) == DRV_FAULT_ISD);
        CHECK(strchr(g_calls, 'E') != NULL);

        /* LO のポーリング（割り込みの取りこぼし）→ OPD */
        setup_clean_fault();
        CHECK(wr8(0x25, on ? 0x0A : 0x02) == RESULT_OK);
        CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
        tick1(8);                                          /* LO_ARM_DELAY_MS 後にアーム */
        CHECK(rd8(R_STATE) == APP_ENABLED);
        g_lo_fault = DRV_FAULT_OPD;
        tick1(1);
        CHECK(rd8(R_STATE) == APP_FAULT);
        CHECK(rd8(R_FAULT) == DRV_FAULT_OPD);
        g_lo_fault = DRV_FAULT_NONE;

        /* LO 異常がなければ AGC ON でも ENABLED のまま */
        setup_clean_fault();
        CHECK(wr8(0x25, on ? 0x0A : 0x02) == RESULT_OK);
        CHECK(wr8(0x50, CMD_ENABLE) == RESULT_OK);
        tick1(20);
        CHECK(rd8(R_STATE) == APP_ENABLED);
        CHECK(rd8(R_FAULT) == 0);
    }
}

int main(void)
{
    printf("test_app: app.c register tests\n");
    RUN(test_read_fixed_and_defaults);
    RUN(test_read_little_endian_and_span);
    RUN(test_read_out_of_map_and_zero_regs);
    RUN(test_write_ranges);
    RUN(test_run_current_3500);
    RUN(test_write_microstep_and_flags);
    RUN(test_microstep_only_when_stopped);
    RUN(test_partial_32bit_writes);
    RUN(test_partial_move_commands);
    RUN(test_partial_16bit_writes);
    RUN(test_multi_register_write);
    RUN(test_write_read_only_and_unmapped);
    RUN(test_motion_commands_require_enabled);
    RUN(test_command_register);
    RUN(test_broadcast_command);
    RUN(test_read_snapshot);
    RUN(test_config_status_dirty);
    RUN(test_j10_registers);
    RUN(test_j10_mode_only_when_stopped);
    RUN(test_flags_agc_home_bits);
    RUN(test_agc_write_disabled);
    RUN(test_agc_write_no_vm_and_fault);
    RUN(test_agc_write_enabled_rejected);
    RUN(test_agc_factory_while_enabled);
    RUN(test_agc_load_while_enabled);
    RUN(test_agc_before_wake);
    RUN(test_agc_does_not_change_fault_handling);

    printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
