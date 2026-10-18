/*
 * 電源投入時の安全状態試験モード。FW_TEST_POWERON=ON のときだけビルドする。
 *
 * - 起動シーケンスの区切りで LED_MCU(PB0) にパルスを出し、ロジアナで各段階の時刻を取れるようにする。
 * - 初期化後は app_init() を呼ばず（通信・ジョグで励磁しない）、安全状態を監視し続ける。
 * - FW_TEST_POWERON_PREINIT_MS > 0 なら、ドライバ制御ピンがリセット状態のまま待つ
 *   （外付け/内蔵プルダウンだけで決まる状態をテスタで測るため）。
 */
#include "test_poweron.h"
#include "board.h"

#ifndef FW_TEST_POWERON_PREINIT_MS
#define FW_TEST_POWERON_PREINIT_MS  0u
#endif

#define MARK_PORT       LED_MCU_PORT
#define MARK_PIN        LED_MCU_PIN
#define MARK_PULSE_US   5u

/* 不一致ビット（test_poweron_result.fail_mask） */
#define FAIL_EN         (1u << 0)   /* PA10 ODR/IDR が High */
#define FAIL_DMODE      (1u << 1)   /* PB12-14 のどれかが High */
#define FAIL_RESET      (1u << 2)   /* PB15 が High */
#define FAIL_AGC        (1u << 3)   /* PB10/PB11 のどれかが High */
#define FAIL_CLK        (1u << 4)   /* PA8 のピンレベルが High */
#define FAIL_DAC_CODE   (1u << 5)   /* DAC1 DOR1 ≠ 0 */
#define FAIL_DAC_OFF    (1u << 6)   /* DAC1 CH1 が停止（VREF が浮く） */
#define FAIL_VREF_PIN   (1u << 7)   /* PA4 がアナログモードでない */

#define RESULT_MAGIC    0x4E4F5750u /* "PWON" */

/* SWD（HOTPLUG）で読み出す。アドレスは build/<preset>/driver_fw.map で探す */
typedef struct {
    uint32_t magic;
    uint32_t checks;            /* 判定回数 */
    uint32_t fails;             /* 不一致の回数 */
    uint32_t first_fail_mask;   /* 最初の不一致のビット */
    uint32_t fail_mask;         /* 不一致ビットの累積 */
    uint32_t dac_dor;           /* 最後に読んだ DAC1 DOR1 */
} test_poweron_result_t;

volatile test_poweron_result_t test_poweron_result;

void test_poweron_preinit(void)
{
    /* board_delay_us 用（board_init でも同じ設定をする） */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

    /* マーカの PB0 だけ先に出力にする。ドライバ制御ピンには触らない */
    __HAL_RCC_GPIOB_CLK_ENABLE();
    MARK_PORT->BRR = MARK_PIN;
    GPIO_InitTypeDef g = {0};
    g.Pin = MARK_PIN;
    g.Mode = GPIO_MODE_OUTPUT_PP;
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(MARK_PORT, &g);

    test_poweron_mark(1);

#if FW_TEST_POWERON_PREINIT_MS > 0
    /* LED_MCU 点灯中はドライバ制御ピンがリセット状態（アナログ入力）のまま */
    MARK_PORT->BSRR = MARK_PIN;
    HAL_Delay(FW_TEST_POWERON_PREINIT_MS);
    MARK_PORT->BRR = MARK_PIN;
    board_delay_us(MARK_PULSE_US);
#endif
}

void test_poweron_mark(uint32_t n)
{
    for (uint32_t i = 0; i < n; i++) {
        MARK_PORT->BSRR = MARK_PIN;
        board_delay_us(MARK_PULSE_US);
        MARK_PORT->BRR = MARK_PIN;
        board_delay_us(MARK_PULSE_US);
    }
}

static uint32_t check_safe_state(void)
{
    const uint32_t dmode = DRV_DMODE0_PIN | DRV_DMODE1_PIN | DRV_DMODE2_PIN;
    const uint32_t agc = DRV_AGC0_PIN | DRV_AGC1_PIN;
    /* ODR（書いた値）と IDR（ピンの実レベル）の両方を見る */
    uint32_t en = DRV_EN_PORT->ODR | DRV_EN_PORT->IDR;
    uint32_t pb = GPIOB->ODR | GPIOB->IDR;
    uint32_t m = 0;

    if (en & DRV_EN_PIN)                    m |= FAIL_EN;
    if (pb & dmode)                         m |= FAIL_DMODE;
    if (pb & DRV_RESET_PIN)                 m |= FAIL_RESET;
    if (pb & agc)                           m |= FAIL_AGC;
    if (DRV_CLK_PORT->IDR & DRV_CLK_PIN)    m |= FAIL_CLK;
    if (DAC1->DOR1 != 0u)                   m |= FAIL_DAC_CODE;
    if ((DAC1->CR & DAC_CR_EN1) == 0u)      m |= FAIL_DAC_OFF;
    if ((DAC_VREF_PORT->MODER & GPIO_MODER_MODE4) != GPIO_MODER_MODE4) m |= FAIL_VREF_PIN;
    return m;
}

void test_poweron_hold(void)
{
    test_poweron_result.magic = RESULT_MAGIC;
    uint32_t last_blink = HAL_GetTick();

    for (;;) {
        uint32_t m = check_safe_state();
        test_poweron_result.checks++;
        test_poweron_result.dac_dor = DAC1->DOR1;
        if (m != 0u) {
            if (test_poweron_result.fails == 0u) {
                test_poweron_result.first_fail_mask = m;
            }
            test_poweron_result.fails++;
            test_poweron_result.fail_mask |= m;
        }

        /* LED_COMM 点灯 = ここまで全て OK、LED_FAULT 点灯 = 不一致あり（ラッチ） */
        bool ng = test_poweron_result.fail_mask != 0u;
        board_pin_write(LED_FAULT_PORT, LED_FAULT_PIN, ng);
        board_pin_write(LED_COMM_PORT, LED_COMM_PIN, !ng);

        /* LED_MCU 1 Hz 点滅 = 試験モードで動作中 */
        uint32_t t = HAL_GetTick();
        if (t - last_blink >= 500u) {
            last_blink = t;
            board_pin_write(MARK_PORT, MARK_PIN, (MARK_PORT->ODR & MARK_PIN) == 0u);
        }
    }
}
