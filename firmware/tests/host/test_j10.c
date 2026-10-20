/*
 * j10.c（J10.3/J10.4 = PA15/PA1 の機能切り替え、エンコーダ、HOME 入力）のホスト単体テスト
 * （要求仕様 3.12 節 FR-J10-01〜04、FR-ENC-01〜03、FR-HOME-01〜02）。期待値は requirements.md から決める。
 * GPIOA・TIM2 は stubs_hw.c の構造体の実体で、j10.c が書いたレジスタ値をそのまま読んで確かめる。
 */
#include "board.h"
#include "config.h"
#include "j10.h"
#include <stdio.h>
#include <string.h>

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

/* PA15・PA1 のレジスタフィールド */
static uint32_t moder(unsigned pin) { return (g_gpioa.MODER >> (pin * 2u)) & 3u; }
static uint32_t pupdr(unsigned pin) { return (g_gpioa.PUPDR >> (pin * 2u)) & 3u; }
static uint32_t afr(unsigned pin) { return (g_gpioa.AFR[pin >> 3] >> ((pin & 7u) * 4u)) & 0xFu; }

#define A 15u   /* PA15 = ENC_A / HOME1 */
#define B 1u    /* PA1  = ENC_B / HOME2 */

/* リセット直後を模す: PA15 は JTDI（AF0、プルアップ）、PA1 はアナログ。他のピンの設定（PA8 = AF1 など）は残す */
static void reset_hw(void)
{
    memset(&g_tim2, 0, sizeof g_tim2);
    g_tim2_clk = 0;
    memset(&g_gpioa, 0, sizeof g_gpioa);
    g_gpioa.MODER = 0xABFFFFFFu;   /* PA13・PA14 = AF（SWD）、PA15 = AF（JTDI）、他はアナログ */
    g_gpioa.PUPDR = 0x64000000u;   /* PA15 = プルアップ、PA13 = プルアップ、PA14 = プルダウン（リセット値） */
    j10_init();
}

/* PA15・PA1 の入力レベルを設定する（IDR の bit15・bit1） */
static void set_pins(bool a, bool b)
{
    g_gpioa.IDR = (a ? GPIO_PIN_15 : 0u) | (b ? GPIO_PIN_1 : 0u);
}

/* ---- モード 0：未使用 ---- */

static void test_mode0_analog(void)
{
    reset_hw();
    j10_set_mode(J10_MODE_NONE);   /* 起動時の最初の呼び出し（リセット直後の JTDI を上書きする。FR-J10-04） */
    CHECK(moder(A) == 3u && moder(B) == 3u);
    CHECK(pupdr(A) == 0u && pupdr(B) == 0u);
    CHECK(g_tim2_clk == 0);
    CHECK(j10_mode() == J10_MODE_NONE);
    CHECK(j10_enc_count() == 0);
    /* 他のピンを壊さない（PA13・PA14 の SWD の MODER と PUPDR） */
    CHECK(((g_gpioa.MODER >> 26) & 3u) == 2u);
    CHECK(((g_gpioa.PUPDR >> 26) & 3u) == 1u && ((g_gpioa.PUPDR >> 28) & 3u) == 2u);
    /* HOME 入力は常に非作動 */
    set_pins(false, false);
    for (int k = 0; k < 5; k++) j10_poll_1ms(false);
    CHECK(!j10_home1_active() && !j10_home2_active());
}

/* ---- モード 1：エンコーダ ---- */

static void test_mode1_encoder(void)
{
    reset_hw();
    g_tim2.CNT = 0x1234;           /* 以前の値が残っていても 0 から数え始める（FR-ENC-03） */
    j10_set_mode(J10_MODE_ENC);
    CHECK(g_tim2_clk == 1);
    /* PA15 → TIM2_CH1、PA1 → TIM2_CH2: AF1、内部プルアップ（FR-ENC-01） */
    CHECK(moder(A) == 2u && moder(B) == 2u);
    CHECK(afr(A) == 1u && afr(B) == 1u);
    CHECK(pupdr(A) == 1u && pupdr(B) == 1u);
    /* エンコーダモード 3（SMS = 011、TI1・TI2 の両エッジ、4 逓倍） */
    CHECK((g_tim2.SMCR & 7u) == 3u);
    /* CC1S/CC2S = 01（入力）、IC1F/IC2F は config.h の ENC_FILTER_ICF（FR-ENC-02） */
    CHECK((g_tim2.CCMR1 & 3u) == 1u && ((g_tim2.CCMR1 >> 8) & 3u) == 1u);
    CHECK(((g_tim2.CCMR1 >> 4) & 0xFu) == ENC_FILTER_ICF && ((g_tim2.CCMR1 >> 12) & 0xFu) == ENC_FILTER_ICF);
    CHECK((g_tim2.CCER & (TIM_CCER_CC1E | TIM_CCER_CC2E)) == (TIM_CCER_CC1E | TIM_CCER_CC2E));
    CHECK((g_tim2.CCER & ((1u << 1) | (1u << 5))) == 0u);   /* 極性は反転しない（配線のとおり） */
    CHECK(g_tim2.ARR == 0xFFFFFFFFu);
    CHECK(g_tim2.CNT == 0u);
    CHECK((g_tim2.CR1 & TIM_CR1_CEN) != 0u);
    CHECK(j10_mode() == J10_MODE_ENC);
}

/* フィルタ値は 200 kHz の入力を通す: fSAMPLING = fDTS/8（80 MHz → 10 MHz）、8 サンプル = 0.8 µs < 半周期 2.5 µs */
static void test_encoder_filter_value(void)
{
    /* ICxF の符号化（RM0394）: 0b1001 = fDTS/8、N=8。2 相とも 0.8 µs 以上の変化だけを通す */
    CHECK(ENC_FILTER_ICF == 0x9u);
    const double fdts = 80e6;
    const double div = 8.0;
    const double samples = 8.0;
    const double window_us = samples * div / fdts * 1e6;
    const double half_period_us = 1e6 / 200e3 / 2.0;   /* 各相 200 kHz、デューティ 50% */
    CHECK(window_us < half_period_us);
    CHECK(half_period_us / window_us >= 3.0);          /* 余裕 3 倍以上 */
}

static void test_enc_count_reads_tim2(void)
{
    reset_hw();
    j10_set_mode(J10_MODE_ENC);
    CHECK(j10_enc_count() == 0);
    g_tim2.CNT = 1234;
    CHECK(j10_enc_count() == 1234);
    g_tim2.CNT = 0xFFFFFFFFu;      /* 32bit カウンタをそのまま符号付きに（下回り = −1） */
    CHECK(j10_enc_count() == -1);
    g_tim2.CNT = 0x80000000u;
    CHECK(j10_enc_count() == INT32_MIN);
    g_tim2.CNT = 0x7FFFFFFFu;
    CHECK(j10_enc_count() == INT32_MAX);
    /* エンコーダ中は HOME 入力を確定しない */
    set_pins(false, false);
    for (int k = 0; k < 5; k++) j10_poll_1ms(false);
    CHECK(!j10_home1_active() && !j10_home2_active());
}

/* ---- モード 2：HOME 入力 ---- */

static void test_mode2_gpio_input(void)
{
    reset_hw();
    j10_set_mode(J10_MODE_HOME);
    CHECK(moder(A) == 0u && moder(B) == 0u);   /* GPIO 入力 */
    CHECK(pupdr(A) == 1u && pupdr(B) == 1u);   /* 内部プルアップ（FR-HOME-01） */
    CHECK(g_tim2_clk == 0);
    CHECK(j10_mode() == J10_MODE_HOME);
    CHECK(j10_enc_count() == 0);
}

/* アクティブ Low（既定）: Low が作動。2 回連続で同じなら確定（FR-HOME-02） */
static void test_home_debounce_active_low(void)
{
    reset_hw();
    j10_set_mode(J10_MODE_HOME);
    set_pins(true, true);                       /* プルアップで High = 非作動 */
    j10_poll_1ms(false);
    j10_poll_1ms(false);
    CHECK(!j10_home1_active() && !j10_home2_active());

    set_pins(false, true);                      /* HOME1 が Low = 作動 */
    j10_poll_1ms(false);
    CHECK(!j10_home1_active());                 /* 1 回目では確定しない */
    j10_poll_1ms(false);
    CHECK(j10_home1_active() && !j10_home2_active());   /* 2 回連続で確定 */

    set_pins(true, false);                      /* HOME1 解除、HOME2 作動 */
    j10_poll_1ms(false);
    CHECK(j10_home1_active() && !j10_home2_active());   /* 1 回目: どちらも変わらない */
    j10_poll_1ms(false);
    CHECK(!j10_home1_active() && j10_home2_active());
}

/* ノイズ（1 回だけ変わってすぐ戻る）は確定しない */
static void test_home_glitch_ignored(void)
{
    reset_hw();
    j10_set_mode(J10_MODE_HOME);
    set_pins(true, true);
    j10_poll_1ms(false);
    j10_poll_1ms(false);
    set_pins(false, true);
    j10_poll_1ms(false);                        /* 1 サンプルだけ作動 */
    set_pins(true, true);
    j10_poll_1ms(false);
    j10_poll_1ms(false);
    CHECK(!j10_home1_active());
    /* 作動 → 非作動 → 作動の交互は確定しない */
    for (int k = 0; k < 6; k++) {
        set_pins((k & 1) == 0, true);
        j10_poll_1ms(false);
    }
    CHECK(!j10_home1_active());

    /* 作動中のグリッチも確定状態を変えない */
    set_pins(false, true);
    j10_poll_1ms(false);
    j10_poll_1ms(false);
    CHECK(j10_home1_active());
    set_pins(true, true);
    j10_poll_1ms(false);
    set_pins(false, true);
    j10_poll_1ms(false);
    j10_poll_1ms(false);
    CHECK(j10_home1_active());
}

/* 入力ごとに独立して確定する（HOME1 の揺れが HOME2 の確定を遅らせない） */
static void test_home_inputs_independent(void)
{
    reset_hw();
    j10_set_mode(J10_MODE_HOME);
    set_pins(true, true);
    j10_poll_1ms(false);
    j10_poll_1ms(false);
    set_pins(true, false);
    j10_poll_1ms(false);
    set_pins(false, false);                     /* HOME1 は 1 回目、HOME2 は 2 回目 */
    j10_poll_1ms(false);
    CHECK(!j10_home1_active() && j10_home2_active());
    j10_poll_1ms(false);
    CHECK(j10_home1_active() && j10_home2_active());
}

/* FLAGS bit4 = 1: アクティブ High（High が作動）。両入力共通 */
static void test_home_active_high(void)
{
    reset_hw();
    j10_set_mode(J10_MODE_HOME);
    set_pins(true, false);
    j10_poll_1ms(true);
    j10_poll_1ms(true);
    CHECK(j10_home1_active() && !j10_home2_active());
    set_pins(false, true);
    j10_poll_1ms(true);
    j10_poll_1ms(true);
    CHECK(!j10_home1_active() && j10_home2_active());
    set_pins(false, false);
    j10_poll_1ms(true);
    j10_poll_1ms(true);
    CHECK(!j10_home1_active() && !j10_home2_active());

    /* 論理を切り替えると、レベルが同じでも作動状態が反転する（2 回連続で確定） */
    set_pins(true, true);
    j10_poll_1ms(true);
    j10_poll_1ms(true);
    CHECK(j10_home1_active() && j10_home2_active());
    j10_poll_1ms(false);
    CHECK(j10_home1_active() && j10_home2_active());   /* 1 回目 */
    j10_poll_1ms(false);
    CHECK(!j10_home1_active() && !j10_home2_active());
}

/* ---- 切り替え ---- */

/* 切り替えるときは一度アナログ（プルなし）に戻し、TIM2 を止めてから新しい機能を設定する（FR-J10-02） */
static void test_switch_via_analog(void)
{
    reset_hw();
    j10_set_mode(J10_MODE_ENC);
    g_tim2.CNT = 777;

    /* ENC → HOME: TIM2 を止めてクロックを切り、AF・SMCR を残さない */
    j10_set_mode(J10_MODE_HOME);
    CHECK((g_tim2.CR1 & TIM_CR1_CEN) == 0u);
    CHECK(g_tim2.SMCR == 0u && g_tim2.CCER == 0u && g_tim2.CCMR1 == 0u);
    CHECK(g_tim2_clk == 0);
    CHECK(moder(A) == 0u && moder(B) == 0u && pupdr(A) == 1u && pupdr(B) == 1u);
    CHECK(afr(A) == 0u && afr(B) == 0u);
    CHECK(j10_enc_count() == 0);

    /* HOME → NONE */
    j10_set_mode(J10_MODE_NONE);
    CHECK(moder(A) == 3u && moder(B) == 3u && pupdr(A) == 0u && pupdr(B) == 0u);

    /* NONE → ENC: カウントは 0 から */
    j10_set_mode(J10_MODE_ENC);
    CHECK(g_tim2.CNT == 0u && j10_enc_count() == 0);
    CHECK(moder(A) == 2u && afr(A) == 1u && afr(B) == 1u);

    /* ENC → NONE */
    g_tim2.CNT = 55;
    j10_set_mode(J10_MODE_NONE);
    CHECK(moder(A) == 3u && moder(B) == 3u && pupdr(A) == 0u && pupdr(B) == 0u);
    CHECK(afr(A) == 0u && afr(B) == 0u);
    CHECK((g_tim2.CR1 & TIM_CR1_CEN) == 0u && g_tim2_clk == 0);
    CHECK(j10_enc_count() == 0);

    /* 再び ENC に入ると 0 から（以前の値を引き継がない） */
    g_tim2.CNT = 99;
    j10_set_mode(J10_MODE_ENC);
    CHECK(j10_enc_count() == 0);
}

/* 同じモードの再設定では何もしない（カウントを消さない。apply_settings が何度も呼ばれる） */
static void test_same_mode_noop(void)
{
    reset_hw();
    j10_set_mode(J10_MODE_ENC);
    g_tim2.CNT = 4321;
    j10_set_mode(J10_MODE_ENC);
    CHECK(j10_enc_count() == 4321);

    j10_set_mode(J10_MODE_HOME);
    set_pins(false, true);
    j10_poll_1ms(false);
    j10_poll_1ms(false);
    CHECK(j10_home1_active());
    j10_set_mode(J10_MODE_HOME);                /* 確定状態を消さない */
    CHECK(j10_home1_active());
}

/* モードを離れると HOME の確定状態は消える。範囲外のモードは無視 */
static void test_leave_home_clears_state_and_invalid_mode(void)
{
    reset_hw();
    j10_set_mode(J10_MODE_HOME);
    set_pins(false, false);
    j10_poll_1ms(false);
    j10_poll_1ms(false);
    CHECK(j10_home1_active() && j10_home2_active());
    j10_set_mode(3);
    CHECK(j10_mode() == J10_MODE_HOME && j10_home1_active());
    j10_set_mode(0xFF);
    CHECK(j10_mode() == J10_MODE_HOME);
    j10_set_mode(J10_MODE_NONE);
    CHECK(!j10_home1_active() && !j10_home2_active());
    /* HOME に戻っても、確定は 2 回のサンプルが要る */
    j10_set_mode(J10_MODE_HOME);
    CHECK(!j10_home1_active());
    j10_poll_1ms(false);
    CHECK(!j10_home1_active());
    j10_poll_1ms(false);
    CHECK(j10_home1_active());
}

/* 起動直後（j10_init 後）は、保存されたモードが 0 でも端子を設定する（リセット直後の JTDI を残さない） */
static void test_boot_applies_even_mode0(void)
{
    reset_hw();
    CHECK(moder(A) == 2u && pupdr(A) == 1u);    /* リセット直後 */
    j10_set_mode(J10_MODE_NONE);
    CHECK(moder(A) == 3u && pupdr(A) == 0u);
}

int main(void)
{
    printf("test_j10: j10.c register tests\n");
    RUN(test_mode0_analog);
    RUN(test_mode1_encoder);
    RUN(test_encoder_filter_value);
    RUN(test_enc_count_reads_tim2);
    RUN(test_mode2_gpio_input);
    RUN(test_home_debounce_active_low);
    RUN(test_home_glitch_ignored);
    RUN(test_home_inputs_independent);
    RUN(test_home_active_high);
    RUN(test_switch_via_analog);
    RUN(test_same_mode_noop);
    RUN(test_leave_home_clears_state_and_invalid_mode);
    RUN(test_boot_applies_even_mode0);

    printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
