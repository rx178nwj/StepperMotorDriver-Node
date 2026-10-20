/*
 * position.c の純粋な計算関数のホスト単体テスト（要求仕様 6 章 試験 23・25 のうちホストで検証できるもの）。
 * 期待値は requirements.md 3.10 節（FR-POS-05・06）から決める。ビルドと実行は run_tests.sh / run_tests.ps1。
 */
#include "position.h"
#include <math.h>
#include <stdio.h>

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

/* ---------------------------------------------------------------- FR-POS-05: a と SHAFT_ANGLE */

/* a = (RAW − OFFSET) mod 4096 */
static void test_axis_count(void)
{
    CHECK(position_axis_count(100, 100) == 0);       /* RAW = OFFSET */
    CHECK(position_axis_count(101, 100) == 1);
    CHECK(position_axis_count(100, 101) == 4095);    /* 巻き戻り */
    CHECK(position_axis_count(100, 200) == 3996);
    CHECK(position_axis_count(0, 4095) == 1);
    CHECK(position_axis_count(4095, 0) == 4095);
    CHECK(position_axis_count(0, 0) == 0);
    CHECK(position_axis_count(0, 1) == 4095);
    CHECK(position_axis_count(4095, 4095) == 0);
    CHECK(position_axis_count(2048, 0) == 2048);

    /* RAW × OFFSET の広い範囲を参照式と照合。結果は常に 0〜4095 */
    int bad = 0;
    for (int raw = 0; raw < 4096; raw += 7) {
        for (int off = 0; off < 4096; off += 5) {
            int ref = (raw - off) % 4096;
            if (ref < 0) ref += 4096;
            uint16_t a = position_axis_count((uint16_t)raw, (uint16_t)off);
            if (a != ref || a > 4095) bad++;
        }
    }
    CHECK(bad == 0);
}

/* SHAFT_ANGLE = a × 36000 / 4096（四捨五入、0〜35999） */
static void test_shaft_angle(void)
{
    CHECK(position_shaft_angle(0, 0) == 0);
    CHECK(position_shaft_angle(1234, 1234) == 0);        /* RAW = OFFSET */
    CHECK(position_shaft_angle(0, 1) == 35991);          /* a=4095: 35991.2 → 35991、36000 にならない */
    CHECK(position_shaft_angle(4095, 0) == 35991);
    CHECK(position_shaft_angle(1024, 0) == 9000);        /* 90° */
    CHECK(position_shaft_angle(2048, 0) == 18000);       /* 180° */
    CHECK(position_shaft_angle(3072, 0) == 27000);       /* 270° */
    CHECK(position_shaft_angle(1, 0) == 9);              /* 8.789 → 9 */
    CHECK(position_shaft_angle(455, 0) == 3999);         /* 3998.8 → 3999 */
    /* 四捨五入の境目: a=64 は 562.5 ちょうど → 563、a=192 は 1687.5 → 1688（半分は切り上げ） */
    CHECK(position_shaft_angle(64, 0) == 563);
    CHECK(position_shaft_angle(192, 0) == 1688);
    CHECK(position_shaft_angle(63, 0) == 554);           /* 553.71 → 554 */
    CHECK(position_shaft_angle(65, 0) == 571);           /* 571.29 → 571 */
    /* OFFSET 付き: RAW < OFFSET の巻き戻り */
    CHECK(position_shaft_angle(0, 1024) == 27000);       /* a = 3072 */
    CHECK(position_shaft_angle(100, 4000) == position_shaft_angle(196, 0));   /* a = 196 */

    /* 全 a で参照式（倍精度、半分は切り上げ）と一致し、35999 を超えない。OFFSET を変えても同じ */
    static const uint16_t offs[] = {0, 1, 1000, 4095};
    for (unsigned k = 0; k < sizeof offs / sizeof offs[0]; k++) {
        int bad = 0;
        for (uint32_t a = 0; a < 4096; a++) {
            uint16_t raw = (uint16_t)((a + offs[k]) & 0x0FFFu);
            uint16_t got = position_shaft_angle(raw, offs[k]);
            uint16_t ref = (uint16_t)floor((double)a * 36000.0 / 4096.0 + 0.5);
            if (got != ref || got > 35999) bad++;
        }
        CHECK(bad == 0);
    }

    /* センサ無効（RAW が 12bit の範囲外。position_raw() は無効時 0xFFFF） */
    CHECK(position_shaft_angle(0xFFFF, 0) == 0xFFFF);
    CHECK(position_shaft_angle(0xFFFF, 123) == 0xFFFF);
    CHECK(position_shaft_angle(4096, 0) == 0xFFFF);
    CHECK(position_shaft_angle(4095, 0) != 0xFFFF);
}

/* ---------------------------------------------------------------- FR-POS-06: SYNC_POSITION */

/* position_sync_steps の呼び出し。成功なら結果、失敗（N が INT32_MAX 超）なら -1 */
static int64_t S(uint16_t a, uint16_t gear, uint16_t spr, uint8_t ms)
{
    int32_t out = -12345;
    if (!position_sync_steps(a, gear, spr, ms, &out)) {
        return out == -12345 ? -1 : -2;   /* 失敗時は *out を変えない */
    }
    return out;
}

/* 参照式。a × N ≤ 約 8.6e15 < 2^53 なので倍精度で正確（4096 での除算も正確）。
 * 結果は 0〜N−1（四捨五入で N になったら 0）、N が INT32_MAX 超なら -1 */
static int64_t sync_ref(uint32_t a, uint32_t gear, uint32_t spr, uint32_t ms)
{
    double total = (double)gear * spr * ms;
    if (total > 2147483647.0) return -1;
    int64_t r = (int64_t)floor((double)a * total / 4096.0 + 0.5);
    return r >= (int64_t)total ? 0 : r;
}

/* 出力軸 1 回転 = ギヤ × spr × ms ステップ。既知の角度に対する値 */
static void test_sync_steps_known_angles(void)
{
    /* ギヤ 20・STEPS_PER_REV 200・分割 16: 1 回転 64000 */
    CHECK(S(0, 20, 200, 16) == 0);
    CHECK(S(1024, 20, 200, 16) == 16000);   /* 90° */
    CHECK(S(2048, 20, 200, 16) == 32000);   /* 180° */
    CHECK(S(3072, 20, 200, 16) == 48000);   /* 270° */
    /* 分割 1・32 */
    CHECK(S(1024, 20, 200, 1) == 1000);
    CHECK(S(2048, 20, 200, 1) == 2000);
    CHECK(S(1024, 20, 200, 32) == 32000);
    CHECK(S(2048, 20, 200, 32) == 64000);
    /* ギヤ 40 */
    CHECK(S(1024, 40, 200, 1) == 2000);
    CHECK(S(1024, 40, 200, 32) == 64000);
    CHECK(S(3072, 40, 200, 32) == 192000);
    /* ギヤ 1000（上限） */
    CHECK(S(1024, 1000, 200, 1) == 50000);
    CHECK(S(2048, 1000, 200, 32) == 3200000);
    /* 1 回転直前（a=4095） */
    CHECK(S(4095, 20, 200, 16) == 63984);    /* 64000 × 4095/4096 = 63984.375 */
    CHECK(S(4095, 20, 200, 1) == 3999);      /* 4000 × 4095/4096 = 3999.02 */
    CHECK(S(4095, 40, 200, 32) == 255938);   /* 256000 × 4095/4096 = 255937.5 → 切り上げ */
}

static void test_sync_steps_rounding(void)
{
    /* 四捨五入: ギヤ・spr・分割がすべて 1 のとき a/4096 */
    CHECK(S(1, 1, 1, 1) == 0);       /* N=1: 結果は常に 0（0〜N−1） */
    CHECK(S(2047, 1, 1, 1) == 0);
    CHECK(S(4095, 1, 1, 1) == 0);
    CHECK(S(2048, 1, 2, 1) == 1);    /* N=2: 2×2048/4096 = 1 */
    CHECK(S(1024, 1, 2, 1) == 1);    /* 0.5 ちょうど → 切り上げ */
    CHECK(S(1023, 1, 2, 1) == 0);    /* 0.49951 */
    CHECK(S(4095, 1, 2, 1) == 0);    /* 1.9995 → 2 = N なので 0（0〜N−1、FR-POS-06） */
    /* 全 a と代表的な組み合わせを参照式と照合 */
    static const uint16_t gears[] = {1, 20, 40, 1000};
    static const uint8_t mss[] = {1, 2, 4, 8, 16, 32};
    int bad = 0;
    for (unsigned g = 0; g < sizeof gears / sizeof gears[0]; g++) {
        for (unsigned m = 0; m < sizeof mss; m++) {
            for (uint32_t a = 0; a < 4096; a++) {
                if (S((uint16_t)a, gears[g], 200, mss[m]) != sync_ref(a, gears[g], 200, mss[m])) {
                    bad++;
                }
            }
        }
    }
    CHECK(bad == 0);
}

/* 32bit に収まらない中間値（64bit 計算）。結果が i32 に収まる範囲で確認する */
static void test_sync_steps_64bit_intermediate(void)
{
    /* 4095 × 1000 × 200 × 32 = 26,208,000,000 > 2^32。結果は 6,398,437.5 → 6,398,438 */
    CHECK(S(4095, 1000, 200, 32) == 6398438);
    /* 4095 × 1000 × 1000 × 32 = 131,040,000,000。結果は 31,992,187.5 → 31,992,188 */
    CHECK(S(4095, 1000, 1000, 32) == 31992188);
    /* 2048 × 1000 × 65535 × 32 = 4.29e12 > 2^32。結果は 1,048,560,000（i32 に収まる） */
    CHECK(S(2048, 1000, 65535, 32) == 1048560000);
    CHECK(S(4095, 20, 65535, 1) == sync_ref(4095, 20, 65535, 1));
    CHECK(S(4095, 1000, 65535, 8) == sync_ref(4095, 1000, 65535, 8));   /* 約 5.2 億 */
}

/* FR-POS-06・07: 結果は 0〜N−1（四捨五入で N になったら 0）、N が INT32_MAX 超なら失敗 */
static void test_sync_steps_range(void)
{
    /* a=4095, N=200: 199.95 → 200 = N なので 0 */
    CHECK(S(4095, 1, 200, 1) == 0);
    CHECK(S(4094, 1, 200, 1) == 0);     /* 199.90 → 200 → 0 */
    CHECK(S(4086, 1, 200, 1) == 0);     /* 199.51 → 200 → 0 */
    CHECK(S(4085, 1, 200, 1) == 199);   /* 199.46 → 199 */
    /* 全 a で 0〜N−1。N が小さい設定ほど N に丸まりやすい */
    static const struct { uint16_t g, spr; uint8_t ms; } cfgs[] = {
        {1, 200, 1}, {1, 200, 2}, {20, 200, 1}, {20, 200, 16}, {3, 7, 1}, {1, 1, 1}, {1, 2, 1}, {40, 200, 32},
    };
    int bad_range = 0, bad_ref = 0;
    for (unsigned k = 0; k < sizeof cfgs / sizeof cfgs[0]; k++) {
        int64_t n = (int64_t)cfgs[k].g * cfgs[k].spr * cfgs[k].ms;
        for (uint32_t a = 0; a < 4096; a++) {
            int64_t r = S((uint16_t)a, cfgs[k].g, cfgs[k].spr, cfgs[k].ms);
            if (r < 0 || r >= n) bad_range++;
            if (r != sync_ref(a, cfgs[k].g, cfgs[k].spr, cfgs[k].ms)) bad_ref++;
        }
    }
    CHECK(bad_range == 0);
    CHECK(bad_ref == 0);

    /* N が INT32_MAX を超える設定は失敗し、*out は変わらない。N=INT32_MAX ちょうどは 2^31−1 が素数で作れないので、
     * 超えない最大側（ギヤ 1024: N = 2,147,450,880）と超える最小側（ギヤ 1025: N = 2,149,548,000 付近）で境界を挟む */
    CHECK(1024u * 65535u * 32u == 2147450880u);
    CHECK(S(4095, 1024, 65535, 32) == sync_ref(4095, 1024, 65535, 32));
    CHECK(S(4095, 1024, 65535, 32) >= 0 && S(4095, 1024, 65535, 32) < 2147450880LL);
    CHECK(1025u * 65535u * 32u > 2147483647u);
    CHECK(S(0, 1025, 65535, 32) == -1);
    CHECK(S(4095, 1025, 65535, 32) == -1);
    CHECK(S(4095, 65535, 65535, 32) == -1);
}

int main(void)
{
    RUN(test_axis_count);
    RUN(test_shaft_angle);
    RUN(test_sync_steps_known_angles);
    RUN(test_sync_steps_rounding);
    RUN(test_sync_steps_64bit_intermediate);
    RUN(test_sync_steps_range);

    printf("\n%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
