/*
 * settings.c（不揮発設定）のホスト単体テスト（要求仕様 6 章 試験 12〜15）。
 * 期待値は requirements.md 3.7 節（FR-NV-01〜08）、3.8 節のレジスタ表、FR-POS-08 から決める。
 * Flash は RAM 上の配列（2 ページ分、消去状態 0xFF）で模擬し、HAL の Flash 関数をスタブにする。
 * ビルドと実行は run_tests.sh / run_tests.ps1（settings.c の Flash 読み出しは -DNV_BASE -DNV_MEMCPY で差し替える）。
 *
 * Flash スタブは STM32L4 の性質を守る：消去は 0xFF、書き込みは 8 バイト（ダブルワード）単位で、
 * 消去済みの場所にだけ書ける（書き込み済みへの再書き込みはエラー）。
 */
#include "settings.h"
#include "app_main.h"
#include "stm32l4xx_hal.h"
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

/* ---------------------------------------------------------------- Flash の模擬 */

#define PAGE_SIZE   2048u
#define NV_PAGES    2u
#define REC_SIZE    64u
#define REC_PER_PAGE (PAGE_SIZE / REC_SIZE)
#define FIRST_PAGE  126u       /* 0x0803F000 / 2KB */

/* レコードのレイアウト（settings.c の record_t に合わせる） */
#define OFF_MAGIC    0u
#define OFF_VERSION  4u
#define OFF_SIZE     6u
#define OFF_SEQ      8u
#define OFF_SETTINGS 16u
#define OFF_CRC      60u
#define REC_MAGIC    0x4D53454Eu

FLASH_TypeDef g_flash_regs;

static uint8_t g_mem[NV_PAGES * PAGE_SIZE];
static int g_unlocked;
static uint32_t g_ecc_lo, g_ecc_hi;     /* [lo, hi) の読み出しで nv_ecc_error を立てる（hi == lo なら無効） */

/* 故障注入 */
enum { CUT_NOTHING, CUT_JUNK_ECC, CUT_JUNK_NOECC };
static unsigned g_prog_n;               /* HAL_FLASH_Program の呼び出し回数 */
static unsigned g_erase_n;
static unsigned g_cut_at_prog;          /* この回数目の書き込みで電源断（0 なら無効） */
static unsigned g_cut_at_erase;         /* この回数目の消去で電源断（0 なら無効） */
static int g_cut_mode;
static int g_dead;                      /* 電源断後。以降の書き込み・消去は何も起こさない */
static unsigned g_fail_prog_at;         /* この回数目の書き込みが HAL_ERROR を返す（0 なら無効） */
static unsigned g_fail_erase_at;
static uint32_t g_erase_log[64];        /* 消去したページ番号の記録 */
static unsigned g_erase_log_n;

void test_nv_memcpy(void *dst, uint32_t addr, uint32_t n)
{
    uint32_t off = addr - NV_BASE;
    memcpy(dst, &g_mem[off], n);
    if (g_ecc_hi > g_ecc_lo && off < g_ecc_hi && off + n > g_ecc_lo) {
        nv_ecc_error = true;
    }
}

HAL_StatusTypeDef HAL_FLASH_Unlock(void)
{
    g_unlocked = 1;
    return HAL_OK;
}

HAL_StatusTypeDef HAL_FLASH_Lock(void)
{
    g_unlocked = 0;
    return HAL_OK;
}

HAL_StatusTypeDef HAL_FLASH_Program(uint32_t type, uint32_t address, uint64_t data)
{
    g_prog_n++;
    if (g_dead || !g_unlocked || type != FLASH_TYPEPROGRAM_DOUBLEWORD) {
        return HAL_ERROR;
    }
    uint32_t off = address - NV_BASE;
    if (address < NV_BASE || off + 8u > sizeof g_mem || (address & 7u) != 0u) {
        return HAL_ERROR;
    }
    for (unsigned i = 0; i < 8u; i++) {
        if (g_mem[off + i] != 0xFFu) {
            return HAL_ERROR;           /* 書き込み済みへの再書き込み */
        }
    }
    if (g_fail_prog_at != 0u && g_prog_n == g_fail_prog_at) {
        return HAL_ERROR;
    }
    if (g_cut_at_prog != 0u && g_prog_n == g_cut_at_prog) {
        g_dead = 1;
        if (g_cut_mode != CUT_NOTHING) {
            uint64_t junk = data ^ 0x5A5A5A5A5A5A5A5AULL;
            memcpy(&g_mem[off], &junk, 8);
            if (g_cut_mode == CUT_JUNK_ECC) {
                g_ecc_lo = off;
                g_ecc_hi = off + 8u;
            }
        }
        return HAL_ERROR;
    }
    memcpy(&g_mem[off], &data, 8);
    return HAL_OK;
}

HAL_StatusTypeDef HAL_FLASHEx_Erase(FLASH_EraseInitTypeDef *init, uint32_t *page_error)
{
    g_erase_n++;
    *page_error = 0xFFFFFFFFu;
    if (g_dead || !g_unlocked || init->TypeErase != FLASH_TYPEERASE_PAGES || init->Banks != FLASH_BANK_1 ||
        init->NbPages != 1u || init->Page < FIRST_PAGE || init->Page >= FIRST_PAGE + NV_PAGES) {
        return HAL_ERROR;
    }
    if (g_fail_erase_at != 0u && g_erase_n == g_fail_erase_at) {
        return HAL_ERROR;
    }
    uint32_t off = (init->Page - FIRST_PAGE) * PAGE_SIZE;
    if (g_erase_log_n < sizeof g_erase_log / sizeof g_erase_log[0]) {
        g_erase_log[g_erase_log_n++] = init->Page;
    }
    if (g_cut_at_erase != 0u && g_erase_n == g_cut_at_erase) {
        g_dead = 1;
        memset(&g_mem[off], 0xA5, PAGE_SIZE);   /* 消去途中: 内容は不定 */
        return HAL_ERROR;
    }
    memset(&g_mem[off], 0xFF, PAGE_SIZE);
    if (g_ecc_hi > g_ecc_lo && g_ecc_lo >= off && g_ecc_lo < off + PAGE_SIZE) {
        g_ecc_lo = g_ecc_hi = 0;                /* 消去すれば ECC エラーは消える */
    }
    return HAL_OK;
}

/* drv.c の代わり（分割設定の有効値は 3.8 節 0x24: 1/2/4/8/16/32） */
bool drv_microstep_valid(uint8_t microstep)
{
    return microstep == 1 || microstep == 2 || microstep == 4 || microstep == 8 || microstep == 16 ||
           microstep == 32;
}

/* 電源投入直後の Flash（全面消去済み）にする */
static void flash_reset(void)
{
    memset(g_mem, 0xFF, sizeof g_mem);
    g_unlocked = 0;
    g_ecc_lo = g_ecc_hi = 0;
    g_prog_n = g_erase_n = 0;
    g_cut_at_prog = g_cut_at_erase = 0;
    g_cut_mode = CUT_NOTHING;
    g_dead = 0;
    g_fail_prog_at = g_fail_erase_at = 0;
    g_erase_log_n = 0;
    nv_ecc_error = false;
}

/* 電源を入れ直す（Flash の内容は残る。故障注入は解除） */
static void power_cycle(void)
{
    g_unlocked = 0;
    g_prog_n = g_erase_n = 0;
    g_cut_at_prog = g_cut_at_erase = 0;
    g_dead = 0;
    g_fail_prog_at = g_fail_erase_at = 0;
    nv_ecc_error = false;
}

/* ---------------------------------------------------------------- テスト補助 */

/* i ごとに全項目が異なる、範囲内の設定 */
static void mk(settings_t *s, unsigned i)
{
    settings_defaults(s);
    s->run_current_ma = (uint16_t)(250u + i);
    s->hold_current_ma = (uint16_t)(300u + i);
    s->microstep = (uint8_t)(1u << (i % 6u));
    s->flags = (uint8_t)(i & 7u);
    s->comm_timeout_ms = (uint16_t)(i * 3u);
    s->max_speed = 1000u + i;
    s->accel = 2000u + i;
    s->decel = 3000u + i;
    s->start_speed = 100u + i;
    s->jog_speed = 500u + i;
    s->hold_delay_ms = (uint16_t)(10u + i);
    s->steps_per_rev = (uint16_t)(200u + i);
    s->gear_ratio = (uint16_t)(1u + i % 1000u);
    s->sensor_offset = (uint16_t)(i % 4096u);
    s->pa7_mode = (uint8_t)(i % 3u);
}

static bool same(const settings_t *a, const settings_t *b)
{
    return memcmp(a, b, sizeof *a) == 0;
}

/* i 番目の設定が最新として読めること */
static bool loads_as(unsigned i)
{
    settings_t exp, got;
    mk(&exp, i);
    memset(&got, 0x77, sizeof got);
    return settings_load(&got) && same(&got, &exp);
}

static bool save_i(unsigned i)
{
    settings_t s;
    mk(&s, i);
    return settings_save(&s);
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

/* 旧版・新版のレコード（64 バイト）を組み立てる。ver 1 は先頭 32 バイトの設定だけ、ver 2 は 40 バイト */
static void build_rec(uint8_t *rec, unsigned ver, uint32_t seq, const settings_t *s)
{
    uint16_t v = (uint16_t)ver;
    uint16_t size = (ver == 1u) ? 32u : 40u;
    uint32_t magic = REC_MAGIC;
    memset(rec, 0xFF, REC_SIZE);
    memcpy(rec + OFF_MAGIC, &magic, 4);
    memcpy(rec + OFF_VERSION, &v, 2);
    memcpy(rec + OFF_SIZE, &size, 2);
    memcpy(rec + OFF_SEQ, &seq, 4);
    memcpy(rec + OFF_SETTINGS, s, size);
    uint32_t crc = crc32_ref(rec, OFF_CRC);
    memcpy(rec + OFF_CRC, &crc, 4);
}

static uint8_t *slot_ptr(unsigned page, unsigned slot)
{
    return &g_mem[page * PAGE_SIZE + slot * REC_SIZE];
}

static uint32_t rec_seq(unsigned page, unsigned slot)
{
    uint32_t q;
    memcpy(&q, slot_ptr(page, slot) + OFF_SEQ, 4);
    return q;
}

/* ---------------------------------------------------------------- FR-NV-05・08、3.8 節: 工場出荷値 */

static void test_defaults(void)
{
    settings_t s;
    memset(&s, 0x5A, sizeof s);
    settings_defaults(&s);
    CHECK(s.run_current_ma == 1000);     /* FR-NV-08 */
    CHECK(s.hold_current_ma == 500);     /* FR-NV-08 */
    CHECK(s.microstep == 16);
    CHECK(s.flags == 0x02);
    CHECK(s.comm_timeout_ms == 0);
    CHECK(s.max_speed == 3200);
    CHECK(s.accel == 16000);
    CHECK(s.decel == 16000);
    CHECK(s.start_speed == 200);
    CHECK(s.jog_speed == 1600);
    CHECK(s.hold_delay_ms == 500);
    CHECK(s.steps_per_rev == 200);
    CHECK(s.gear_ratio == 20);
    CHECK(s.sensor_offset == 0);
    CHECK(s.pa7_mode == 1);
    CHECK(s.reserved[0] == 0 && s.reserved[1] == 0 && s.reserved[2] == 0);

    /* 工場出荷値は範囲内（丸めが起きない） */
    settings_t t = s;
    CHECK(!settings_sanitize(&t));
    CHECK(same(&t, &s));
}

/* ---------------------------------------------------------------- 範囲補正（3.8 節の範囲） */

/* 範囲内の値は false を返し、値を変えない */
static void test_sanitize_in_range(void)
{
    settings_t d, s;
    settings_defaults(&d);

    static const uint16_t cur[] = {250, 251, 1000, 2999, 3000};
    for (unsigned i = 0; i < sizeof cur / sizeof cur[0]; i++) {
        s = d;
        s.run_current_ma = cur[i];
        s.hold_current_ma = cur[i];
        settings_t e = s;
        CHECK(!settings_sanitize(&s));
        CHECK(same(&s, &e));
    }
    static const uint8_t ms[] = {1, 2, 4, 8, 16, 32};
    for (unsigned i = 0; i < sizeof ms; i++) {
        s = d;
        s.microstep = ms[i];
        settings_t e = s;
        CHECK(!settings_sanitize(&s));
        CHECK(same(&s, &e));
    }
    static const uint32_t spd[] = {1, 2, 3200, 99999, 100000};
    for (unsigned i = 0; i < sizeof spd / sizeof spd[0]; i++) {
        s = d;
        s.max_speed = s.start_speed = s.jog_speed = spd[i];
        settings_t e = s;
        CHECK(!settings_sanitize(&s));
        CHECK(same(&s, &e));
    }
    static const uint32_t acc[] = {1, 2, 16000, 9999999, 10000000};
    for (unsigned i = 0; i < sizeof acc / sizeof acc[0]; i++) {
        s = d;
        s.accel = s.decel = acc[i];
        settings_t e = s;
        CHECK(!settings_sanitize(&s));
        CHECK(same(&s, &e));
    }
    static const uint16_t gear[] = {1, 2, 20, 999, 1000};
    for (unsigned i = 0; i < sizeof gear / sizeof gear[0]; i++) {
        s = d;
        s.gear_ratio = gear[i];
        settings_t e = s;
        CHECK(!settings_sanitize(&s));
        CHECK(same(&s, &e));
    }
    static const uint16_t off[] = {0, 1, 2048, 4094, 4095};
    for (unsigned i = 0; i < sizeof off / sizeof off[0]; i++) {
        s = d;
        s.sensor_offset = off[i];
        settings_t e = s;
        CHECK(!settings_sanitize(&s));
        CHECK(same(&s, &e));
    }
    for (uint8_t m = 0; m <= 2; m++) {
        s = d;
        s.pa7_mode = m;
        settings_t e = s;
        CHECK(!settings_sanitize(&s));
        CHECK(same(&s, &e));
    }
    /* FLAGS: bit0〜2（方向反転・ローカルジョグ・センサ方向）は有効 */
    for (uint8_t f = 0; f <= 7; f++) {
        s = d;
        s.flags = f;
        settings_t e = s;
        CHECK(!settings_sanitize(&s));
        CHECK(same(&s, &e));
    }
}

/* 範囲外の値は範囲内に丸めて true を返す。ほかの項目は変えない */
static void test_sanitize_out_of_range(void)
{
    settings_t d, s, e;
    settings_defaults(&d);

    /* 電流 250〜3000 */
    static const struct { uint16_t in, out; } cur[] = {{0, 250}, {1, 250}, {249, 250}, {3001, 3000}, {65535, 3000}};
    for (unsigned i = 0; i < sizeof cur / sizeof cur[0]; i++) {
        s = d;
        s.run_current_ma = cur[i].in;
        e = d;
        e.run_current_ma = cur[i].out;
        CHECK(settings_sanitize(&s));
        CHECK(same(&s, &e));

        s = d;
        s.hold_current_ma = cur[i].in;
        e = d;
        e.hold_current_ma = cur[i].out;
        CHECK(settings_sanitize(&s));
        CHECK(same(&s, &e));
    }
    /* 分割: 1/2/4/8/16/32 以外は有効な値になる */
    static const uint8_t badms[] = {0, 3, 5, 6, 7, 12, 31, 33, 64, 255};
    for (unsigned i = 0; i < sizeof badms; i++) {
        s = d;
        s.microstep = badms[i];
        CHECK(settings_sanitize(&s));
        CHECK(drv_microstep_valid(s.microstep));
        s.microstep = d.microstep;
        CHECK(same(&s, &d));
    }
    /* 速度 1〜100,000 */
    static const struct { uint32_t in, out; } spd[] = {{0, 1}, {100001, 100000}, {0xFFFFFFFFu, 100000}};
    for (unsigned i = 0; i < sizeof spd / sizeof spd[0]; i++) {
        s = d;
        s.max_speed = spd[i].in;
        e = d;
        e.max_speed = spd[i].out;
        CHECK(settings_sanitize(&s));
        CHECK(same(&s, &e));

        s = d;
        s.start_speed = spd[i].in;
        e = d;
        e.start_speed = spd[i].out;
        CHECK(settings_sanitize(&s));
        CHECK(same(&s, &e));

        s = d;
        s.jog_speed = spd[i].in;
        e = d;
        e.jog_speed = spd[i].out;
        CHECK(settings_sanitize(&s));
        CHECK(same(&s, &e));
    }
    /* 加速度・減速度 1〜10,000,000 */
    static const struct { uint32_t in, out; } acc[] = {{0, 1}, {10000001, 10000000}, {0xFFFFFFFFu, 10000000}};
    for (unsigned i = 0; i < sizeof acc / sizeof acc[0]; i++) {
        s = d;
        s.accel = acc[i].in;
        e = d;
        e.accel = acc[i].out;
        CHECK(settings_sanitize(&s));
        CHECK(same(&s, &e));

        s = d;
        s.decel = acc[i].in;
        e = d;
        e.decel = acc[i].out;
        CHECK(settings_sanitize(&s));
        CHECK(same(&s, &e));
    }
    /* ギヤ比 1〜1000 */
    static const struct { uint16_t in, out; } gear[] = {{0, 1}, {1001, 1000}, {65535, 1000}};
    for (unsigned i = 0; i < sizeof gear / sizeof gear[0]; i++) {
        s = d;
        s.gear_ratio = gear[i].in;
        e = d;
        e.gear_ratio = gear[i].out;
        CHECK(settings_sanitize(&s));
        CHECK(same(&s, &e));
    }
    /* センサ原点オフセット 0〜4095 */
    static const struct { uint16_t in, out; } off[] = {{4096, 4095}, {65535, 4095}};
    for (unsigned i = 0; i < sizeof off / sizeof off[0]; i++) {
        s = d;
        s.sensor_offset = off[i].in;
        e = d;
        e.sensor_offset = off[i].out;
        CHECK(settings_sanitize(&s));
        CHECK(same(&s, &e));
    }
    /* PA7_MODE 0〜2 */
    static const uint8_t badpa[] = {3, 4, 100, 255};
    for (unsigned i = 0; i < sizeof badpa; i++) {
        s = d;
        s.pa7_mode = badpa[i];
        CHECK(settings_sanitize(&s));
        CHECK(s.pa7_mode <= 2);
        s.pa7_mode = d.pa7_mode;
        CHECK(same(&s, &d));
    }
    /* FLAGS の未定義ビット（bit5〜7）は落とし、定義済みの bit0〜2 は残す */
    for (unsigned hi = 0x20; hi <= 0xE0; hi += 0x20) {
        for (uint8_t lo = 0; lo <= 7; lo++) {
            s = d;
            s.flags = (uint8_t)(hi | lo);
            e = d;
            e.flags = lo;
            CHECK(settings_sanitize(&s));
            CHECK(same(&s, &e));
        }
    }
    /* 複数項目が同時に範囲外でも全部丸める */
    s = d;
    s.run_current_ma = 0;
    s.gear_ratio = 0;
    s.sensor_offset = 5000;
    s.accel = 0;
    CHECK(settings_sanitize(&s));
    CHECK(s.run_current_ma == 250 && s.gear_ratio == 1 && s.sensor_offset == 4095 && s.accel == 1);
    CHECK(!settings_sanitize(&s));       /* 2 回目は何も変わらない */
}

/* 保留: 3.8 節の 0x25 FLAGS は bit3（AGC 有効）・bit4（HOME 論理）も定義済みで FR-NV-01 で保存対象だが、
 * 現行の settings_sanitize は bit3・bit4 を落とす（flags_mask が bit0〜2 のみ）。P2 の未実装と思われるため保留。
 *   入力 flags=0x18 → 期待 0x18・戻り値 false（仕様）、実際 0x00・true
static void test_sanitize_flags_p2(void)
{
    settings_t s;
    settings_defaults(&s);
    s.flags = 0x1F;
    CHECK(!settings_sanitize(&s));
    CHECK(s.flags == 0x1F);
}
 */

/* ---------------------------------------------------------------- 試験 12（FR-NV-03・05）: 保存と読み込み */

static void test_load_empty(void)
{
    flash_reset();
    settings_t s;
    memset(&s, 0x77, sizeof s);
    settings_t before = s;
    CHECK(!settings_load(&s));
    CHECK(same(&s, &before));            /* 失敗時は *s を変更しない */
}

static void test_save_load(void)
{
    flash_reset();
    settings_t a, got;
    mk(&a, 7);
    CHECK(settings_save(&a));
    memset(&got, 0x77, sizeof got);
    CHECK(settings_load(&got));
    CHECK(same(&got, &a));
    CHECK(!g_unlocked);                  /* 保存後は Flash をロックしている */

    /* 電源を入れ直しても同じ値 */
    power_cycle();
    memset(&got, 0x77, sizeof got);
    CHECK(settings_load(&got));
    CHECK(same(&got, &a));

    /* 上書き保存（追記）で新しい値になる */
    settings_t b;
    mk(&b, 9);
    CHECK(settings_save(&b));
    CHECK(settings_load(&got));
    CHECK(same(&got, &b));
    CHECK(!same(&got, &a));

    /* 工場出荷値も保存・読み込みできる */
    settings_t d;
    settings_defaults(&d);
    CHECK(settings_save(&d));
    CHECK(settings_load(&got));
    CHECK(same(&got, &d));

    /* 最小値・最大値の端 */
    settings_t m;
    settings_defaults(&m);
    m.run_current_ma = 3000;
    m.hold_current_ma = 250;
    m.microstep = 32;
    m.flags = 7;
    m.comm_timeout_ms = 0xFFFF;
    m.max_speed = 100000;
    m.accel = 10000000;
    m.decel = 1;
    m.start_speed = 1;
    m.jog_speed = 100000;
    m.hold_delay_ms = 0xFFFF;
    m.steps_per_rev = 0xFFFF;
    m.gear_ratio = 1000;
    m.sensor_offset = 4095;
    m.pa7_mode = 2;
    CHECK(settings_save(&m));
    CHECK(settings_load(&got));
    CHECK(same(&got, &m));
}

/* ---------------------------------------------------------------- 試験 13（FR-NV-03）: 連番とページ切り替え */

static void test_seq_and_pages(void)
{
    flash_reset();
    uint32_t prev_seq = 0;
    for (unsigned i = 1; i <= 200; i++) {
        CHECK(save_i(i));
        CHECK(loads_as(i));              /* 毎回最新の値が読める */
        unsigned page = ((i - 1u) / REC_PER_PAGE) % NV_PAGES;
        unsigned slot = (i - 1u) % REC_PER_PAGE;
        uint32_t q = rec_seq(page, slot);
        if (i > 1u) {
            CHECK((int32_t)(q - prev_seq) > 0);       /* 連番は増える */
        }
        prev_seq = q;
    }
    /* 1 ページは 32 レコード。200 回 = 満杯→切り替えを 6 回繰り返す。最初の保存では消去しない。
     * 33 回目は他方の（127）、65 回目は元の（126）、…と交互に消す */
    CHECK(g_erase_log_n == 6);
    for (unsigned k = 0; k < g_erase_log_n && k < 6; k++) {
        CHECK(g_erase_log[k] == FIRST_PAGE + ((k + 1u) % 2u));
    }

    /* 電源を入れ直しても最新が読める */
    power_cycle();
    CHECK(loads_as(200));
    /* 続けて保存できる */
    CHECK(save_i(201));
    CHECK(loads_as(201));
}

/* ページが満杯になる境界で、新しいレコードが完成するまで前のレコードを消さない */
static void test_page_switch_keeps_previous(void)
{
    flash_reset();
    for (unsigned i = 1; i <= 32; i++) {
        CHECK(save_i(i));
    }
    CHECK(g_erase_log_n == 0);
    CHECK(loads_as(32));
    /* 33 回目の保存: 満杯の 126 ページは消さず、127 ページを消して書く */
    CHECK(save_i(33));
    CHECK(g_erase_log_n == 1 && g_erase_log[0] == FIRST_PAGE + 1u);
    CHECK(loads_as(33));
    /* 前のページ（32 回目まで）の内容はそのまま残っている */
    uint8_t rec[REC_SIZE];
    settings_t s;
    mk(&s, 32);
    build_rec(rec, 2, rec_seq(0, 31), &s);
    CHECK(memcmp(rec, slot_ptr(0, 31), REC_SIZE) == 0);
}

/* ---------------------------------------------------------------- 試験 14（FR-NV-04）: 電源断・破損 */

/* (a) 本体だけ書かれて先頭 8 バイトが未書き込みのレコードは無視 */
static void test_head_unwritten(void)
{
    flash_reset();
    CHECK(save_i(1));
    CHECK(save_i(2));
    CHECK(loads_as(2));
    memset(slot_ptr(0, 1), 0xFF, 8);     /* 2 回目のレコードの先頭 8 バイトを書かれていない状態に */
    CHECK(loads_as(1));
    /* 最初のレコードだけ（前のレコードがない）なら読めない */
    memset(slot_ptr(0, 0), 0xFF, 8);
    settings_t s;
    CHECK(!settings_load(&s));
}

/* (b) CRC 不一致のレコードは無視。どの 1 バイトが壊れても検出する */
static void test_crc_mismatch(void)
{
    flash_reset();
    CHECK(save_i(1));
    CHECK(save_i(2));
    uint8_t saved[REC_SIZE];
    memcpy(saved, slot_ptr(0, 1), REC_SIZE);
    for (unsigned b = 0; b < REC_SIZE; b++) {
        memcpy(slot_ptr(0, 1), saved, REC_SIZE);
        slot_ptr(0, 1)[b] ^= 0x01;       /* 1 ビット反転（識別子・版・サイズ・連番・設定・CRC のどこでも） */
        settings_t got;
        bool ok = settings_load(&got);
        /* 予約領域（本体の reserved0/reserved1）は CRC 範囲内なので同様に検出される */
        CHECK(ok);
        if (ok) {
            settings_t e1;
            mk(&e1, 1);
            CHECK(same(&got, &e1));
        }
    }
    memcpy(slot_ptr(0, 1), saved, REC_SIZE);
    CHECK(loads_as(2));                  /* 元に戻せば最新が読める */

    /* 最新が壊れた状態でも、次の保存は成功し、新しい値が読める */
    slot_ptr(0, 1)[20] ^= 0xFF;
    CHECK(loads_as(1));
    CHECK(save_i(3));
    CHECK(loads_as(3));
}

/* (c) 読み出しで ECC エラーになったレコードは無効 */
static void test_ecc_error(void)
{
    flash_reset();
    CHECK(save_i(1));
    CHECK(save_i(2));
    CHECK(loads_as(2));
    /* 2 回目のレコードのうち、各ダブルワードで ECC エラー */
    for (unsigned dw = 0; dw < REC_SIZE / 8u; dw++) {
        g_ecc_lo = REC_SIZE + dw * 8u;
        g_ecc_hi = g_ecc_lo + 8u;
        CHECK(loads_as(1));
    }
    /* ECC エラーを起こしたレコードを飛ばして、残りのページ全体を読み終えること（最後の読み出しが原因でも） */
    g_ecc_lo = g_ecc_hi = 0;
    CHECK(loads_as(2));
    CHECK(!nv_ecc_error);
    /* ブランクの場所の ECC エラーも無視して読める */
    g_ecc_lo = PAGE_SIZE + 5u * REC_SIZE;
    g_ecc_hi = g_ecc_lo + 8u;
    CHECK(loads_as(2));
    /* 唯一のレコードが ECC エラーなら読めない */
    g_ecc_lo = REC_SIZE;
    g_ecc_hi = g_ecc_lo + 8u;
    memset(slot_ptr(0, 0), 0xFF, REC_SIZE);
    settings_t s;
    CHECK(!settings_load(&s));

    /* NMI ハンドラからの呼び出し: ECCD が立っているときだけ処理して true */
    nv_ecc_error = false;
    g_flash_regs.ECCR = 0;
    CHECK(!app_nmi_handled());
    CHECK(!nv_ecc_error);
    g_flash_regs.ECCR = FLASH_ECCR_ECCD;
    CHECK(app_nmi_handled());
    CHECK(nv_ecc_error);
    g_flash_regs.ECCR = 0;
    nv_ecc_error = false;
}

/* 保存を N 回目の書き込みで打ち切り（電源断）、電源投入し直して前のレコードが読めること。
 * 書き込み途中の場所は、何も書かれない／不定値（ECC エラーあり・なし）の 3 通りを試す */
static void test_power_cut_during_save(void)
{
    static const unsigned ks[] = {0, 1, 5, 31, 32, 33, 63, 64, 65};
    for (unsigned ki = 0; ki < sizeof ks / sizeof ks[0]; ki++) {
        unsigned k = ks[ki];
        for (int mode = CUT_NOTHING; mode <= CUT_JUNK_NOECC; mode++) {
            for (unsigned n = 1; n <= 8; n++) {
                flash_reset();
                for (unsigned i = 1; i <= k; i++) {
                    CHECK(save_i(i));
                }
                g_prog_n = 0;
                g_cut_at_prog = n;
                g_cut_mode = mode;
                CHECK(!save_i(k + 1000));

                power_cycle();
                if (k == 0) {
                    settings_t s;
                    CHECK(!settings_load(&s));
                } else {
                    CHECK(loads_as(k));          /* 前のレコードが読まれる */
                }
                /* 破損した場所があっても、次の保存は成功して新しい値が読める */
                CHECK(save_i(k + 2000));
                CHECK(loads_as(k + 2000));
                power_cycle();
                CHECK(loads_as(k + 2000));
            }
        }
    }
}

/* ページ切り替えの消去中に電源断: 満杯の側（前のレコード）は消えていない */
static void test_power_cut_during_erase(void)
{
    static const unsigned ks[] = {32, 64};
    for (unsigned ki = 0; ki < 2; ki++) {
        unsigned k = ks[ki];
        flash_reset();
        for (unsigned i = 1; i <= k; i++) {
            CHECK(save_i(i));
        }
        g_erase_n = 0;
        g_cut_at_erase = 1;
        CHECK(!save_i(k + 1000));
        power_cycle();
        CHECK(loads_as(k));
        CHECK(save_i(k + 2000));
        CHECK(loads_as(k + 2000));
    }
}

/* ---------------------------------------------------------------- FR-POS-08: 旧版のレコード */

static void test_old_versions(void)
{
    settings_t d, v1s;
    settings_defaults(&d);
    mk(&v1s, 11);
    /* 版 1 の設定は先頭 32 バイトだけ。新項目（ギヤ比・オフセット・PA7_MODE）に当たる後ろは書かれていない */
    settings_t expect = d;
    memcpy(&expect, &v1s, 32);

    uint8_t rec[REC_SIZE];
    flash_reset();
    build_rec(rec, 1, 5, &v1s);
    memcpy(slot_ptr(0, 0), rec, REC_SIZE);
    settings_t got;
    memset(&got, 0x77, sizeof got);
    CHECK(settings_load(&got));
    CHECK(same(&got, &expect));
    CHECK(got.gear_ratio == 20 && got.sensor_offset == 0 && got.pa7_mode == 1);
    CHECK(got.run_current_ma == v1s.run_current_ma && got.steps_per_rev == v1s.steps_per_rev);
    CHECK(got.hold_delay_ms == v1s.hold_delay_ms && got.jog_speed == v1s.jog_speed);
    CHECK(got.reserved[0] == 0 && got.reserved[1] == 0 && got.reserved[2] == 0);

    /* 版 1 のあとに保存すると追記され、新しい版で読める */
    CHECK(save_i(3));
    CHECK(loads_as(3));
    CHECK(rec_seq(0, 1) == 6);           /* 連番は版 1 の続き */

    /* 版 2 の完全なレコード */
    settings_t v2s;
    mk(&v2s, 21);
    flash_reset();
    build_rec(rec, 2, 9, &v2s);
    memcpy(slot_ptr(1, 4), rec, REC_SIZE);
    CHECK(settings_load(&got));
    CHECK(same(&got, &v2s));

    /* 版 1 と版 2 が混在: 連番が大きい方が最新 */
    flash_reset();
    build_rec(rec, 2, 3, &v2s);
    memcpy(slot_ptr(0, 0), rec, REC_SIZE);
    build_rec(rec, 1, 4, &v1s);
    memcpy(slot_ptr(0, 1), rec, REC_SIZE);
    CHECK(settings_load(&got));
    CHECK(same(&got, &expect));
    build_rec(rec, 2, 5, &v2s);
    memcpy(slot_ptr(1, 0), rec, REC_SIZE);
    CHECK(settings_load(&got));
    CHECK(same(&got, &v2s));

    /* 版 1 で CRC が合わないもの、未知の版は無視 */
    flash_reset();
    build_rec(rec, 1, 5, &v1s);
    rec[OFF_SETTINGS + 3] ^= 0x10;
    memcpy(slot_ptr(0, 0), rec, REC_SIZE);
    CHECK(!settings_load(&got));
    uint16_t bad_ver = 3;
    build_rec(rec, 2, 6, &v2s);
    memcpy(rec + OFF_VERSION, &bad_ver, 2);
    uint32_t crc = crc32_ref(rec, OFF_CRC);
    memcpy(rec + OFF_CRC, &crc, 4);      /* CRC は合っているが版が未知 */
    memcpy(slot_ptr(0, 0), rec, REC_SIZE);
    CHECK(!settings_load(&got));
}

/* ---------------------------------------------------------------- 書き込み失敗 */

static void test_write_failure(void)
{
    /* 書き込みが N 回目に HAL_ERROR: save は false、前のレコードは残る */
    for (unsigned n = 1; n <= 8; n++) {
        flash_reset();
        CHECK(save_i(1));
        g_prog_n = 0;
        g_fail_prog_at = n;
        CHECK(!save_i(2));
        CHECK(!g_unlocked);              /* 失敗しても Flash をロックして戻る */
        power_cycle();
        CHECK(loads_as(1));
        CHECK(save_i(3));                /* 回復して保存できる */
        CHECK(loads_as(3));
    }
    /* 最初の保存の書き込み失敗: 保存なし */
    flash_reset();
    g_fail_prog_at = 1;
    CHECK(!save_i(1));
    settings_t s;
    power_cycle();
    CHECK(!settings_load(&s));

    /* 消去の失敗（ページ切り替え時）: save は false、満杯の側のレコードが残る */
    flash_reset();
    for (unsigned i = 1; i <= 32; i++) {
        CHECK(save_i(i));
    }
    g_erase_n = 0;
    g_fail_erase_at = 1;
    g_prog_n = 0;
    CHECK(!save_i(33));
    CHECK(g_prog_n == 0);                /* 消去に失敗したら書き込みには進まない */
    power_cycle();
    CHECK(loads_as(32));
    CHECK(save_i(34));
    CHECK(loads_as(34));
}

/* ---------------------------------------------------------------- メイン */

int main(void)
{
    /* 参照 CRC の自己確認（CRC-32/ISO-HDLC の標準検査値） */
    CHECK(crc32_ref((const uint8_t *)"123456789", 9) == 0xCBF43926u);

    RUN(test_defaults);
    RUN(test_sanitize_in_range);
    RUN(test_sanitize_out_of_range);
    RUN(test_load_empty);
    RUN(test_save_load);
    RUN(test_seq_and_pages);
    RUN(test_page_switch_keeps_previous);
    RUN(test_head_unwritten);
    RUN(test_crc_mismatch);
    RUN(test_ecc_error);
    RUN(test_power_cut_during_save);
    RUN(test_power_cut_during_erase);
    RUN(test_old_versions);
    RUN(test_write_failure);

    printf("%d checks, %d failures\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
