/*
 * NEMA23 ステッピングモータドライバ ファームウェア（P1 基本機能）
 * STM32L433CCU6 + TB67S249FTG。仕様は firmware/docs/requirements.md。
 *
 * クロック（HSI16→PLL 80 MHz、HSI48+CRS）と USB CDC は CubeMX 生成コードが初期化する。
 * それ以外の周辺回路はここから初期化する。
 */
#include "app_main.h"
#include "app.h"
#include "board.h"
#include "drv.h"
#include "monitor.h"
#include "stepper.h"
#include "test_poweron.h"
#include "usb_proto.h"

static IWDG_HandleTypeDef app_iwdg;
static uint32_t last_1ms;
static uint32_t last_10ms;

static void watchdog_init(void)
{
    /* LSI 32 kHz / 32 = 1 kHz、500 カウントで約 500 ms */
    app_iwdg.Instance = IWDG;
    app_iwdg.Init.Prescaler = IWDG_PRESCALER_32;
    app_iwdg.Init.Window = IWDG_WINDOW_DISABLE;
    app_iwdg.Init.Reload = 500;
    HAL_IWDG_Init(&app_iwdg);
}

void app_main_init(void)
{
    /* test_poweron_* は FW_TEST_POWERON ビルドのみ有効（通常ビルドでは空） */
    test_poweron_preinit();
    board_init();          /* ドライバ制御ピンを安全状態へ（それまではリセット時のアナログ状態） */
    test_poweron_mark(2);
    drv_init();
    test_poweron_mark(3);
    stepper_init();
    test_poweron_mark(4);
    monitor_init();
    test_poweron_mark(5);
    test_poweron_hold();
    app_init();
    watchdog_init();

    last_1ms = HAL_GetTick();
    last_10ms = last_1ms;
}

void app_main_loop(void)
{
    uint32_t t = HAL_GetTick();
    if (t != last_1ms) {
        last_1ms = t;
        app_tick_1ms(t);
    }
    if (t - last_10ms >= 10u) {
        last_10ms = t;
        app_tick_10ms(t);
    }
    usb_proto_poll();   /* USB の受信解析・応答・イベント送信（ブロックしない） */
    HAL_IWDG_Refresh(&app_iwdg);
}

void app_fault_safe_state(void)
{
    DRV_EN_PORT->BRR = DRV_EN_PIN;
    TIM1->CR1 &= ~TIM_CR1_CEN;
}
