/*
 * stepper.c（TIM1 による CLK 生成と台形加減速）のホスト単体テスト（要求仕様 6 章 試験 5 のうちホストで検証できるもの）。
 * 期待値は requirements.md 3.3 節（FR-MOT-01〜09）から決める。ビルドと実行は run_tests.sh / run_tests.ps1。
 *
 * static 関数（make_period など）と内部状態を直接見るため、stepper.c を #include して同じ翻訳単位に入れる。
 * TIM1 は構造体の実体（stubs_hw.c）で、update イベントを sim_event() で模擬する:
 *   1 周期 = 1 パルス。イベント時に ①その周期のシャドウ値（PSC/ARR/CCR1）からパルス幅を記録、
 *   ②OPM なら CEN を落とす、③プリロードをシャドウへ、④stepper_timer_isr() を呼ぶ。
 */
#include "../../App/Src/stepper.c"
#include <math.h>
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

/* ---------------------------------------------------------------- TIM1 の模擬 */

#define MAXP  400000u
#define TICKS_PER_US  (STEP_TIMER_HZ / 1000000u)   /* 80 */

typedef struct {
    uint32_t period;   /* 周期 [タイマクロック] */
    uint32_t high;     /* High 幅 */
    uint32_t low;      /* Low 幅（パルスの前） */
} pulse_t;

static pulse_t pl[MAXP];
static unsigned pn;               /* 出力したパルス数 */
static period_t shadow;           /* 動作中の周期のシャドウ値 */
static unsigned g_overshoot;      /* 位置決め中に目標を行き過ぎたイベント数 */

static uint32_t period_ticks(const period_t *p)
{
    return ((uint32_t)p->arr + 1u) * ((uint32_t)p->psc + 1u);
}

static uint32_t high_ticks(const period_t *p)
{
    return ((uint32_t)p->arr + 1u - p->ccr) * ((uint32_t)p->psc + 1u);
}

static uint32_t low_ticks(const period_t *p)
{
    return (uint32_t)p->ccr * ((uint32_t)p->psc + 1u);
}

static void sim_reset(float max_speed, float accel, float decel, float start_speed, bool invert)
{
    memset(&g_tim1, 0, sizeof g_tim1);
    g_gpioa.BSRR = 0;
    g_primask = 0;
    stepper_params_t p = {
        .max_speed = max_speed, .accel = accel, .decel = decel, .start_speed = start_speed, .invert_dir = invert,
    };
    stepper_set_params(&p);
    mode = MODE_IDLE;
    target_pos = 0;
    target_vel = 0.0f;
    position = 0;
    running = false;
    dir = 1;
    cur_speed = 0.0f;
    touched = false;
    v = 0.0f;
    commit_pos = 0;
    braking = false;
    pn = 0;
    g_overshoot = 0;
    memset(&shadow, 0, sizeof shadow);
}

static void sim_reset_default(void)
{
    sim_reset(3200.0f, 16000.0f, 16000.0f, 200.0f, false);
}

/* stepper_service() で起動する。起動したら 1 周期目のシャドウ値を記録する（UG による取り込みの再現） */
static bool sim_start(void)
{
    if (!stepper_service()) {
        return false;
    }
    make_period(fabsf(cur_speed), &shadow);
    return true;
}

/* update イベントを 1 回進める。CEN が立っていなければ false */
static bool sim_event(void)
{
    if (!(TIM1->CR1 & TIM_CR1_CEN) || pn >= MAXP) {
        return false;
    }
    pl[pn].period = period_ticks(&shadow);
    pl[pn].high = high_ticks(&shadow);
    pl[pn].low = low_ticks(&shadow);
    pn++;
    TIM1->SR |= TIM_SR_UIF;
    if (TIM1->CR1 & TIM_CR1_OPM) {
        TIM1->CR1 &= ~TIM_CR1_CEN;
    }
    shadow.psc = (uint16_t)TIM1->PSC;
    shadow.arr = (uint16_t)TIM1->ARR;
    shadow.ccr = (uint16_t)TIM1->CCR1;
    stepper_timer_isr();
    if (mode == MODE_POS && ((int64_t)position - target_pos) * dir > 0) {
        g_overshoot++;
    }
    return true;
}

/* n イベント進める（途中で止まったらそこまで）。実際に進めた数を返す */
static unsigned sim_events(unsigned n)
{
    unsigned i = 0;
    while (i < n && sim_event()) {
        i++;
    }
    return i;
}

/* 停止するまで（停止後に起動すべき指令が残っていれば再起動して）進める。max イベントで打ち切り */
static void sim_run_to_stop(unsigned max)
{
    unsigned ev = 0;
    for (;;) {
        if (!running && !sim_start()) {
            break;
        }
        while (running && ev < max) {
            if (!sim_event()) {
                break;
            }
            ev++;
        }
        if (ev >= max) {
            break;
        }
    }
}

/* i 番目のパルスの周期から求めた速度 [step/s] */
static float sp(unsigned i)
{
    return (float)STEP_TIMER_HZ / (float)pl[i].period;
}

static bool near_rel(float a, float b, float rel)
{
    return fabsf(a - b) <= rel * fabsf(b);
}

/* DIR ピン: 最後に書いた BSRR から CW（High）か CCW（Low）か */
static bool dir_is_cw(void)
{
    return (g_gpioa.BSRR & DRV_DIR_PIN) != 0u;
}

static bool dir_is_ccw(void)
{
    return g_gpioa.BSRR == ((uint32_t)DRV_DIR_PIN << 16);
}

/* 全パルスで High・Low とも 1 µs 以上（FR-MOT-01）。違反数を返す */
static unsigned width_violations(void)
{
    unsigned bad = 0;
    for (unsigned i = 0; i < pn; i++) {
        if (pl[i].high < TICKS_PER_US || pl[i].low < TICKS_PER_US) bad++;
    }
    return bad;
}

/* 最高速度のパルス（最大速度の 0.998 倍以上）の最初と最後の番号 */
static void cruise_range(float vmax, unsigned *first, unsigned *last)
{
    *first = pn;
    *last = 0;
    for (unsigned i = 0; i < pn; i++) {
        if (sp(i) >= 0.998f * vmax) {
            if (*first == pn) *first = i;
            *last = i;
        }
    }
}

/* ---------------------------------------------------------------- FR-MOT-01・02: 周期とパルス幅 */

static void test_make_period_widths(void)
{
    period_t p;

    make_period((float)STEP_RATE_MAX, &p);   /* 100,000 step/s: 10 µs */
    CHECK(period_ticks(&p) == 800u);
    CHECK(high_ticks(&p) >= TICKS_PER_US);
    CHECK(low_ticks(&p) >= TICKS_PER_US);

    /* 1〜100,000 step/s の全域（約 2% 刻み）で High・Low とも 1 µs 以上、周期が速度に対して 0.2% 以内 */
    unsigned bad_width = 0, bad_period = 0, bad_order = 0;
    for (float s = 1.0f; s <= 100000.0f; s *= 1.02f) {
        make_period(s, &p);
        if (high_ticks(&p) < TICKS_PER_US || low_ticks(&p) < TICKS_PER_US) bad_width++;
        if (!near_rel((float)STEP_TIMER_HZ / (float)period_ticks(&p), s, 0.002f)) bad_period++;
        if (p.ccr > p.arr) bad_order++;
    }
    CHECK(bad_width == 0);
    CHECK(bad_period == 0);
    CHECK(bad_order == 0);

    /* 1 step/s（下限）: 周期 1 s（16bit のカウンタに収めるため PSC を使う） */
    make_period(1.0f, &p);
    CHECK(near_rel((float)period_ticks(&p), 80000000.0f, 0.001f));
    CHECK(p.psc > 0u);
    CHECK(high_ticks(&p) >= TICKS_PER_US && low_ticks(&p) >= TICKS_PER_US);
}

static void test_make_period_clamps(void)
{
    period_t lo, hi, p;

    /* 範囲外は 1〜100,000 step/s に制限される（FR-MOT-02） */
    make_period(1.0f, &lo);
    make_period((float)STEP_RATE_MAX, &hi);

    make_period(0.0f, &p);
    CHECK(memcmp(&p, &lo, sizeof p) == 0);
    make_period(0.25f, &p);
    CHECK(memcmp(&p, &lo, sizeof p) == 0);
    make_period(-500.0f, &p);
    CHECK(memcmp(&p, &lo, sizeof p) == 0);
    make_period(100001.0f, &p);
    CHECK(memcmp(&p, &hi, sizeof p) == 0);
    make_period(1.0e6f, &p);
    CHECK(memcmp(&p, &hi, sizeof p) == 0);
    CHECK(period_ticks(&hi) >= 800u);   /* 10 µs 未満の周期は出ない */
}

static void test_init_registers(void)
{
    memset(&g_tim1, 0, sizeof g_tim1);
    stepper_init();
    CHECK(!(TIM1->CR1 & TIM_CR1_CEN));           /* 起動時はパルスを出さない */
    CHECK(TIM1->CR1 & TIM_CR1_ARPE);             /* ARR・PSC・CCR1 はプリロード */
    CHECK(TIM1->CCER & TIM_CCER_CC1E);
    CHECK((TIM1->CCMR1 & TIM_CCMR1_OC1M) == (4u << TIM_CCMR1_OC1M_Pos));   /* 強制 Low */
    CHECK(TIM1->DIER & TIM_DIER_UIE);
    CHECK(TIM1->BDTR & TIM_BDTR_MOE);
}

/* ---------------------------------------------------------------- FR-MOT-03・04: 台形加減速 */

/* 長い移動: 加速 → 最高速度で巡航 → 減速、目標でちょうど停止 */
static void test_trapezoid_long(void)
{
    const float vmax = 3200.0f, a = 16000.0f, d = 16000.0f, vs = 200.0f;
    sim_reset(vmax, a, d, vs, false);
    stepper_move_to(5000);
    CHECK(stepper_has_pending());
    CHECK(!stepper_is_running());
    sim_run_to_stop(100000);

    CHECK(pn == 5000u);
    CHECK(stepper_position() == 5000);
    CHECK(!stepper_is_running());
    CHECK(stepper_at_target());
    CHECK(stepper_speed() == 0);
    CHECK(g_overshoot == 0u);
    CHECK(width_violations() == 0u);
    CHECK(dir_is_cw());

    /* 開始速度から始まる */
    CHECK(near_rel(sp(0), vs, 0.005f));

    unsigned c0, c1;
    cruise_range(vmax, &c0, &c1);
    CHECK(c0 < pn);
    /* 加速ステップ数 (vmax² − vs²)/(2a) ≈ 319。減速も同数 */
    CHECK(c0 >= 314u && c0 <= 323u);
    CHECK(pn - 1u - c1 >= 314u && pn - 1u - c1 <= 323u);

    /* 加速区間: 速度は単調増加し、v² は 1 ステップごとに 2a ずつ増える */
    unsigned bad_mono = 0, bad_sq = 0;
    for (unsigned i = 1; i <= c0; i++) {
        if (sp(i) < sp(i - 1) * 0.999f) bad_mono++;
        float exp_v2 = vs * vs + 2.0f * a * (float)i;
        if (exp_v2 > vmax * vmax) exp_v2 = vmax * vmax;
        if (!near_rel(sp(i) * sp(i), exp_v2, 0.01f)) bad_sq++;
    }
    CHECK(bad_mono == 0u);
    CHECK(bad_sq == 0u);

    /* 巡航区間: 一定（最高速度を超えない） */
    unsigned bad_cruise = 0;
    for (unsigned i = c0; i <= c1; i++) {
        if (!near_rel(sp(i), vmax, 0.003f)) bad_cruise++;
    }
    CHECK(bad_cruise == 0u);

    /* 減速区間: 単調減少し、v² は 1 ステップごとに約 2d ずつ減る（端の数ステップは除く） */
    unsigned bad_dec = 0, bad_dsq = 0;
    for (unsigned i = c1 + 1u; i < pn; i++) {
        if (sp(i) > sp(i - 1) * 1.001f) bad_dec++;
    }
    for (unsigned i = c1 + 3u; i + 4u < pn; i++) {
        float dv2 = sp(i) * sp(i) - sp(i + 1) * sp(i + 1);
        if (!near_rel(dv2, 2.0f * d, 0.05f)) bad_dsq++;
    }
    CHECK(bad_dec == 0u);
    CHECK(bad_dsq == 0u);

    /* 停止直前は低速（開始速度の付近） */
    CHECK(sp(pn - 1u) <= 1.5f * vs);

    /* 最高速度を超えない */
    float peak = 0.0f;
    for (unsigned i = 0; i < pn; i++) {
        if (sp(i) > peak) peak = sp(i);
    }
    CHECK(peak <= vmax * 1.002f);
}

/* 加速度と減速度が異なる */
static void test_trapezoid_asymmetric(void)
{
    const float vmax = 3200.0f, a = 8000.0f, d = 32000.0f, vs = 200.0f;
    sim_reset(vmax, a, d, vs, false);
    stepper_move_to(8000);
    sim_run_to_stop(100000);

    CHECK(pn == 8000u);
    CHECK(stepper_position() == 8000);
    CHECK(g_overshoot == 0u);
    CHECK(width_violations() == 0u);

    unsigned c0, c1;
    cruise_range(vmax, &c0, &c1);
    /* 加速 (vmax² − vs²)/(2a) ≈ 637.5、減速 (vmax² − vs²)/(2d) ≈ 159.4 */
    CHECK(c0 >= 630u && c0 <= 645u);
    CHECK(pn - 1u - c1 >= 154u && pn - 1u - c1 <= 165u);
    CHECK(c0 > 3u * (pn - 1u - c1));   /* 加速のほうが減速より約 4 倍長い */
}

/* 最高速度に届かない三角形のプロファイル */
static void test_triangle_profile(void)
{
    const float vmax = 3200.0f, a = 16000.0f, d = 16000.0f, vs = 200.0f;
    sim_reset(vmax, a, d, vs, false);
    stepper_move_to(400);
    sim_run_to_stop(100000);

    CHECK(pn == 400u);
    CHECK(stepper_position() == 400);
    CHECK(stepper_at_target());
    CHECK(g_overshoot == 0u);
    CHECK(width_violations() == 0u);

    unsigned ipk = 0;
    float peak = 0.0f;
    for (unsigned i = 0; i < pn; i++) {
        if (sp(i) > peak) {
            peak = sp(i);
            ipk = i;
        }
    }
    CHECK(peak < 0.99f * vmax);                 /* 最高速度に届かない */
    /* 加速距離 = N·d/(a+d) = 200 ステップ、ピーク v² = vs² + 2a × 200 */
    CHECK(ipk >= 195u && ipk <= 205u);
    CHECK(near_rel(peak * peak, vs * vs + 2.0f * a * 200.0f, 0.04f));

    unsigned bad_up = 0;
    for (unsigned i = 1; i <= ipk; i++) {
        if (sp(i) < sp(i - 1) * 0.999f) bad_up++;
    }
    CHECK(bad_up == 0u);

    /* ピーク後の減速は単調減少（FR-MOT-03）。減速中に速度が戻らない */
    unsigned bad_down = 0;
    for (unsigned i = ipk + 1u; i < pn; i++) {
        if (sp(i) > sp(i - 1) * 1.001f) bad_down++;
    }
    CHECK(bad_down == 0u);
}

/* 短い相対移動: 1 ステップから。正負とも目標でちょうど止まる */
static void test_short_moves(void)
{
    const int deltas[] = {1, 2, 3, 4, 5, 10};
    for (unsigned k = 0; k < sizeof deltas / sizeof deltas[0]; k++) {
        for (int sgn = -1; sgn <= 1; sgn += 2) {
            int delta = sgn * deltas[k];
            sim_reset_default();
            stepper_move_by(delta);
            sim_run_to_stop(1000);
            CHECK(pn == (unsigned)deltas[k]);
            CHECK(stepper_position() == delta);
            CHECK(!stepper_is_running());
            CHECK(stepper_at_target());
            CHECK(g_overshoot == 0u);
            CHECK(sgn > 0 ? dir_is_cw() : dir_is_ccw());
            CHECK(near_rel(sp(0), 200.0f, 0.005f));   /* 最初のパルスは開始速度 */
            CHECK(width_violations() == 0u);
        }
    }
}

/* 1〜数千ステップの移動を、パラメータの組み合わせを変えて全部目標で止める */
static void test_exact_stop_sweep(void)
{
    static const struct { float vmax, a, d, vs; } sets[] = {
        {3200.0f, 16000.0f, 16000.0f, 200.0f},
        {100000.0f, 1.0e6f, 1.0e6f, 1000.0f},
        {1000.0f, 500.0f, 500.0f, 1.0f},
        {3200.0f, 100.0f, 100000.0f, 200.0f},     /* 加速が遅く減速が速い */
        {3200.0f, 100000.0f, 100.0f, 200.0f},     /* 加速が速く減速が遅い */
        {50.0f, 10.0f, 10.0f, 50.0f},             /* 開始速度 = 最高速度（加減速なし） */
    };
    static const int lens[] = {1, 2, 3, 4, 5, 7, 10, 33, 100, 333, 1000, 2500};
    unsigned bad = 0, bad_over = 0, bad_w = 0;
    for (unsigned s = 0; s < sizeof sets / sizeof sets[0]; s++) {
        for (unsigned k = 0; k < sizeof lens / sizeof lens[0]; k++) {
            for (int sgn = -1; sgn <= 1; sgn += 2) {
                sim_reset(sets[s].vmax, sets[s].a, sets[s].d, sets[s].vs, false);
                stepper_move_to(sgn * lens[k]);
                sim_run_to_stop(200000);
                if (!(pn == (unsigned)lens[k] && stepper_position() == sgn * lens[k] && !stepper_is_running() &&
                      stepper_at_target())) {
                    bad++;
                    printf("  sweep: set %u len %d dir %d: pulses %u pos %d\n", s, lens[k], sgn, pn,
                           (int)stepper_position());
                }
                if (g_overshoot != 0u) bad_over++;
                if (width_violations() != 0u) bad_w++;
            }
        }
    }
    CHECK(bad == 0u);
    CHECK(bad_over == 0u);
    CHECK(bad_w == 0u);
}

/* 負方向・絶対位置・DIR の反転設定（FR-MOT-09）。位置は常に出力パルスで数える */
static void test_negative_and_invert(void)
{
    sim_reset_default();
    stepper_move_to(-3000);
    sim_run_to_stop(100000);
    CHECK(pn == 3000u);
    CHECK(stepper_position() == -3000);
    CHECK(dir_is_ccw());
    CHECK(g_overshoot == 0u);

    /* 続けて +1000 へ: 4000 パルス、DIR は CW */
    unsigned before = pn;
    stepper_move_to(1000);
    sim_run_to_stop(100000);
    CHECK(pn - before == 4000u);
    CHECK(stepper_position() == 1000);
    CHECK(dir_is_cw());

    /* invert_dir: DIR のレベルだけが反転し、位置は同じ向きに数える */
    sim_reset(3200.0f, 16000.0f, 16000.0f, 200.0f, true);
    stepper_move_to(500);
    sim_run_to_stop(10000);
    CHECK(stepper_position() == 500);
    CHECK(dir_is_ccw());
    stepper_move_to(-500);
    sim_run_to_stop(10000);
    CHECK(stepper_position() == -500);
    CHECK(dir_is_cw());
}

/* 相対移動: 停止中は現在位置から、動作中の位置決めは目標から */
static void test_move_by_semantics(void)
{
    sim_reset_default();
    CHECK(stepper_set_position(1000));
    stepper_move_by(500);
    sim_run_to_stop(10000);
    CHECK(stepper_position() == 1500);
    CHECK(pn == 500u);

    stepper_move_by(-1500);
    sim_run_to_stop(10000);
    CHECK(stepper_position() == 0);

    /* 動作中に move_by: 目標に加算される（1000 → 1300） */
    stepper_move_to(1000);
    CHECK(sim_start());
    sim_events(100);
    CHECK(stepper_is_running());
    stepper_move_by(300);
    sim_run_to_stop(100000);
    CHECK(stepper_position() == 1300);
    CHECK(stepper_at_target());
    CHECK(g_overshoot == 0u);

    /* 32bit の端 */
    sim_reset_default();
    CHECK(stepper_set_position(2147483640));
    stepper_move_to(2147483647);
    sim_run_to_stop(1000);
    CHECK(stepper_position() == 2147483647);
    CHECK(pn == 7u);
    CHECK(stepper_set_position(-2147483640));
    stepper_move_to(-2147483647 - 1);
    sim_run_to_stop(1000);
    CHECK(stepper_position() == (-2147483647 - 1));
    CHECK(pn == 7u + 8u);
}

/* 動作中に目標を現在位置より後ろへ変える: 減速して止まり、DIR を切り替えて目標へ戻る（FR-MOT-04・06） */
static void test_retarget_and_reverse(void)
{
    sim_reset_default();
    stepper_move_to(10000);
    CHECK(sim_start());
    sim_events(1000);
    CHECK(near_rel(sp(pn - 1u), 3200.0f, 0.003f));
    CHECK(dir_is_cw());
    uint32_t bsrr = g_gpioa.BSRR;

    stepper_move_to(500);   /* 現在位置（約 1000）より後ろ */
    /* 停止するまでは DIR を変えない。速度は単調に減る */
    unsigned bad_dec = 0, n0 = pn;
    while (running && pn < MAXP) {
        sim_event();
        if (running && g_gpioa.BSRR != bsrr) bad_dec += 1000;
        if (pn > n0 + 1u && sp(pn - 1u) > sp(pn - 2u) * 1.001f) bad_dec++;
    }
    CHECK(bad_dec == 0u);
    CHECK(!running);
    CHECK(stepper_position() > 1000);          /* 減速距離（約 319）ぶん進んで止まる */
    CHECK(stepper_position() < 1000 + 330);

    sim_run_to_stop(100000);
    CHECK(stepper_position() == 500);
    CHECK(stepper_at_target());
    CHECK(dir_is_ccw());
    CHECK(width_violations() == 0u);
}

/* 位置決めの速度は 加速 → 一定 → 減速 の順で、一度減速に入ったら減速中に速度を上げない（FR-MOT-03）。
 * 短い移動（三角形）を含め N=2〜2000 の全域で確認する */
static void test_decel_never_speeds_up(void)
{
    unsigned bad_down = 0, bad_up = 0, bad_stop = 0;
    for (int n = 2; n <= 2000; n++) {
        sim_reset_default();
        stepper_move_to(n);
        sim_run_to_stop(100000);
        unsigned ipk = 0;
        float pk = 0.0f;
        for (unsigned i = 0; i < pn; i++) {
            if (sp(i) > pk * 1.0005f) {
                pk = sp(i);
                ipk = i;
            }
        }
        for (unsigned i = ipk + 1u; i < pn; i++) {
            if (sp(i) > sp(i - 1) * 1.001f) bad_down++;
        }
        for (unsigned i = 1; i <= ipk; i++) {
            if (sp(i) < sp(i - 1) * 0.999f) bad_up++;
        }
        if (stepper_position() != n || pn != (unsigned)n || g_overshoot != 0u) bad_stop++;
    }
    CHECK(bad_down == 0u);
    CHECK(bad_up == 0u);
    CHECK(bad_stop == 0u);
}

/* 動作中に目標を現在位置のすぐ先（減速距離より近く）へ変える（FR-MOT-04）:
 * 減速度 d を守って減速し、目標を行き過ぎて停止してから、DIR を切り替えて目標へ戻る */
static void test_retarget_closer_than_braking_distance(void)
{
    const float d = 16000.0f;
    sim_reset_default();
    stepper_move_to(10000);
    CHECK(sim_start());
    sim_events(1000);
    CHECK(dir_is_cw());
    uint32_t bsrr = g_gpioa.BSRR;

    stepper_move_to(1200);   /* 残り約 200 ステップ < 減速距離約 319 */
    unsigned n0 = pn, bad_decel = 0, bad_dir = 0;
    bool passed_target = false;
    while (running && pn < MAXP) {
        sim_event();
        if (stepper_position() > 1200) passed_target = true;
        if (running && g_gpioa.BSRR != bsrr) bad_dir++;                    /* (c) 動作中は DIR を変えない */
        if (pn > n0 + 1u) {
            float dv2 = sp(pn - 2u) * sp(pn - 2u) - sp(pn - 1u) * sp(pn - 1u);
            if (dv2 > 2.0f * d * 1.02f) bad_decel++;                       /* (a) 急停止しない */
            if (sp(pn - 1u) > sp(pn - 2u) * 1.001f) bad_decel++;
        }
    }
    CHECK(bad_decel == 0u);
    CHECK(bad_dir == 0u);
    CHECK(!running);
    CHECK(passed_target);                       /* (b) 目標を行き過ぎてから停止 */
    CHECK(stepper_position() > 1200);
    CHECK(stepper_position() < 1000 + 330);     /* 減速距離（約 319）以内で止まる */
    CHECK(sp(pn - 1u) <= 1.5f * 200.0f);        /* 停止直前は低速 */

    sim_run_to_stop(100000);                    /* (d) DIR を切り替えて目標へ戻り、ちょうど止まる */
    CHECK(stepper_position() == 1200);
    CHECK(stepper_at_target());
    CHECK(dir_is_ccw());
    CHECK(width_violations() == 0u);
}

/* ---------------------------------------------------------------- FR-MOT-05・06・07: 速度モード、STOP、ESTOP */

static void test_velocity_mode(void)
{
    sim_reset_default();
    stepper_run(2000);
    CHECK(stepper_has_pending());
    CHECK(sim_start());
    CHECK(near_rel(sp(0), 200.0f, 0.005f));
    sim_events(500);
    CHECK(running);
    CHECK(near_rel(sp(pn - 1u), 2000.0f, 0.003f));
    CHECK(abs(stepper_speed() - 2000) <= 2);
    CHECK(stepper_position() == (int32_t)pn);

    /* 加速は単調、最高速度（2000）を超えない */
    unsigned bad = 0;
    for (unsigned i = 1; i < pn; i++) {
        if (sp(i) < sp(i - 1) * 0.999f || sp(i) > 2000.0f * 1.002f) bad++;
    }
    CHECK(bad == 0u);

    /* 最高速度を超える指令は最高速度（3200）に制限される。速度変更は加減速を伴う */
    stepper_run(5000);
    unsigned n1 = pn;
    sim_events(800);
    CHECK(near_rel(sp(pn - 1u), 3200.0f, 0.003f));
    bad = 0;
    for (unsigned i = n1; i < pn; i++) {
        if (sp(i) < sp(i - 1) * 0.999f || sp(i) > 3200.0f * 1.002f) bad++;
    }
    CHECK(bad == 0u);

    /* 速度を下げる: 減速して新しい速度で一定 */
    stepper_run(1000);
    unsigned n2 = pn;
    sim_events(800);
    CHECK(near_rel(sp(pn - 1u), 1000.0f, 0.003f));
    bad = 0;
    for (unsigned i = n2; i < pn; i++) {
        if (sp(i) > sp(i - 1) * 1.001f || sp(i) < 1000.0f * 0.998f) bad++;
    }
    CHECK(bad == 0u);
    CHECK(running);
    CHECK(width_violations() == 0u);
}

static void test_stop_decelerates(void)
{
    sim_reset_default();
    stepper_run(2000);
    CHECK(sim_start());
    sim_events(500);
    int32_t pos_at_stop = stepper_position();
    unsigned n0 = pn;

    stepper_stop();
    sim_events(10000);
    CHECK(!running);
    CHECK(stepper_speed() == 0);
    CHECK(!stepper_has_pending());   /* 目標速度 0: 再起動しない */
    /* 減速ステップ数 (2000² − 200²)/(2d) ≈ 124。減速中は単調減少 */
    CHECK(pn - n0 >= 118u && pn - n0 <= 132u);
    CHECK(stepper_position() == pos_at_stop + (int32_t)(pn - n0));
    unsigned bad = 0;
    for (unsigned i = n0; i < pn; i++) {
        if (sp(i) > sp(i - 1) * 1.001f) bad++;
    }
    CHECK(bad == 0u);
    CHECK(sp(pn - 1u) <= 1.5f * 200.0f);
    CHECK(width_violations() == 0u);
}

/* 逆方向の指令: 一度減速停止し、停止後に DIR を切り替えて再始動する */
static void test_reverse_in_velocity_mode(void)
{
    sim_reset_default();
    stepper_run(1500);
    CHECK(sim_start());
    sim_events(400);
    CHECK(near_rel(sp(pn - 1u), 1500.0f, 0.003f));
    CHECK(dir_is_cw());
    uint32_t bsrr = g_gpioa.BSRR;

    stepper_run(-1500);
    unsigned n0 = pn, bad = 0;
    while (running && pn < MAXP) {
        sim_event();
        if (running && g_gpioa.BSRR != bsrr) bad++;                /* 動作中は DIR を変えない */
        if (pn > n0 + 1u && sp(pn - 1u) > sp(pn - 2u) * 1.001f) bad++;   /* 減速のみ */
    }
    CHECK(bad == 0u);
    CHECK(!running);
    int32_t pos_stopped = stepper_position();
    CHECK(pos_stopped > 0);
    CHECK(stepper_has_pending());   /* 負方向の指令が残っている */

    CHECK(sim_start());
    CHECK(dir_is_ccw());
    unsigned n1 = pn;
    sim_events(400);
    CHECK(near_rel(sp(pn - 1u), 1500.0f, 0.003f));
    CHECK(stepper_speed() < 0);
    CHECK(stepper_position() == pos_stopped - (int32_t)(pn - n1));
    CHECK(near_rel(sp(n1), 200.0f, 0.005f));
}

static void test_estop(void)
{
    sim_reset_default();
    stepper_run(3000);
    CHECK(sim_start());
    sim_events(1000);
    CHECK(running);
    CHECK(stepper_position() == (int32_t)pn);

    unsigned n0 = pn;
    stepper_estop();
    CHECK(!running);
    CHECK(!stepper_is_running());
    CHECK(stepper_speed() == 0);
    CHECK(!(TIM1->CR1 & TIM_CR1_CEN));                                   /* パルス即停止 */
    CHECK((TIM1->CCMR1 & TIM_CCMR1_OC1M) == OC1M_FORCE_INACTIVE);        /* CLK を Low に固定 */
    CHECK(!sim_event());
    CHECK(pn == n0);
    CHECK(stepper_position() == (int32_t)n0);
    CHECK(!stepper_has_pending());   /* 指令は破棄される（勝手に再起動しない） */
    CHECK(!sim_start());

    /* 出し終えたが ISR が未実行のパルスは位置に数える */
    sim_reset_default();
    stepper_move_to(5000);
    CHECK(sim_start());
    sim_events(300);
    int32_t pos = stepper_position();
    TIM1->SR |= TIM_SR_UIF;
    stepper_estop();
    CHECK(stepper_position() == pos + 1);
    CHECK(stepper_speed() == 0);

    /* ESTOP 後の再指令は開始速度から */
    unsigned n1 = pn;
    stepper_move_to(stepper_position() + 100);
    sim_run_to_stop(10000);
    CHECK(stepper_position() == pos + 101);
    CHECK(near_rel(sp(n1), 200.0f, 0.005f));
    CHECK(pn - n1 == 100u);
}

/* ---------------------------------------------------------------- FR-MOT-02: 速度範囲 */

static void test_speed_limits(void)
{
    /* 最高速度は 100,000 step/s に制限される */
    sim_reset(500000.0f, 1.0e7f, 1.0e7f, 1000.0f, false);
    CHECK(prm.max_speed == (float)STEP_RATE_MAX);
    stepper_run(200000);
    CHECK(sim_start());
    sim_events(2000);
    float peak = 0.0f;
    for (unsigned i = 0; i < pn; i++) {
        if (sp(i) > peak) peak = sp(i);
        if (pl[i].period < 800u) peak = 1.0e9f;
    }
    CHECK(near_rel(sp(pn - 1u), 100000.0f, 0.002f));
    CHECK(peak <= 100000.0f * 1.002f);
    CHECK(width_violations() == 0u);

    /* 停止まで（高速からの減速）も上限を守る */
    stepper_stop();
    sim_events(100000);
    CHECK(!running);
    CHECK(width_violations() == 0u);

    /* 最低速度は 1 step/s: 開始速度 0.2 は 1 に引き上げられ、1 ステップの移動は約 1 s */
    sim_reset(1000.0f, 500.0f, 500.0f, 0.2f, false);
    CHECK(prm.start_speed == 1.0f);
    stepper_move_by(1);
    sim_run_to_stop(10);
    CHECK(pn == 1u);
    CHECK(near_rel((float)pl[0].period, 80000000.0f, 0.001f));
    CHECK(stepper_position() == 1);
    CHECK(width_violations() == 0u);

    /* 低速から長い移動でも全域で速度範囲 1〜100,000 とパルス幅を満たす */
    sim_reset(100000.0f, 2.0e6f, 2.0e6f, 1.0f, false);
    stepper_move_to(150000);
    sim_run_to_stop(MAXP);
    CHECK(pn == 150000u);
    CHECK(stepper_position() == 150000);
    CHECK(width_violations() == 0u);
    unsigned bad = 0;
    for (unsigned i = 0; i < pn; i++) {
        if (pl[i].period < 800u || sp(i) < 0.99f) bad++;
    }
    CHECK(bad == 0u);
}

/* ---------------------------------------------------------------- FR-MOT-08: 位置の更新と再定義 */

static void test_position_tracking(void)
{
    sim_reset_default();
    CHECK(!stepper_touched());
    CHECK(stepper_position() == 0);

    stepper_move_to(2000);
    CHECK(sim_start());
    CHECK(stepper_touched());
    /* パルスごとに更新される */
    unsigned bad = 0;
    for (int i = 1; i <= 500; i++) {
        sim_event();
        if (stepper_position() != i) bad++;
    }
    CHECK(bad == 0u);

    /* 動作中は SET_POSITION を受け付けない */
    CHECK(!stepper_set_position(-777));
    CHECK(stepper_position() == 500);
    CHECK(stepper_is_running());

    sim_run_to_stop(100000);
    CHECK(stepper_position() == 2000);
    CHECK(!stepper_is_running());

    /* 停止中は任意値に再定義でき、動作はしない（目標も同じ値になる） */
    CHECK(stepper_set_position(-12345));
    CHECK(stepper_position() == -12345);
    CHECK(!stepper_has_pending());
    CHECK(!sim_start());
    CHECK(stepper_touched());

    stepper_move_by(10);
    sim_run_to_stop(1000);
    CHECK(stepper_position() == -12335);

    /* 起動前でも再定義で touched になる */
    sim_reset_default();
    CHECK(!stepper_touched());
    CHECK(stepper_set_position(5));
    CHECK(stepper_touched());
    CHECK(stepper_position() == 5);
}

int main(void)
{
    RUN(test_make_period_widths);
    RUN(test_make_period_clamps);
    RUN(test_init_registers);
    RUN(test_trapezoid_long);
    RUN(test_trapezoid_asymmetric);
    RUN(test_triangle_profile);
    RUN(test_short_moves);
    RUN(test_exact_stop_sweep);
    RUN(test_negative_and_invert);
    RUN(test_move_by_semantics);
    RUN(test_decel_never_speeds_up);
    RUN(test_retarget_and_reverse);
    RUN(test_retarget_closer_than_braking_distance);
    RUN(test_velocity_mode);
    RUN(test_stop_decelerates);
    RUN(test_reverse_in_velocity_mode);
    RUN(test_estop);
    RUN(test_speed_limits);
    RUN(test_position_tracking);

    printf("\n%d checks, %d failed\n", g_checks, g_fail);
    return g_fail == 0 ? 0 : 1;
}
