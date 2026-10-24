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
#include "bootjump.h"
#include "config.h"
#include "diag.h"
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
    diag_init(SystemCoreClock / 1000000u);
    watchdog_init();

    last_1ms = HAL_GetTick();
    last_10ms = last_1ms;
}

/*
 * ENTER_BOOTLOADER の実行（FR-BL-03）。戻らない。
 * ACK の送信完了を最大 BOOT_TX_WAIT_MS 待つ（その間も IWDG を更新）→ ドライバを安全状態にする →
 * 移行フラグを書いてソフトウェアリセット。リセット後は main() の先頭で bootjump_check が ROM へ飛ぶ。
 * 待ち中は新しい要求（usb_proto_poll）も app_tick も動かさないので、ENABLE などは割り込めない
 */
static void bootloader_enter(void)
{
    uint32_t t0 = HAL_GetTick();
    while (!usb_proto_tx_idle() && (HAL_GetTick() - t0) < BOOT_TX_WAIT_MS) {
        HAL_IWDG_Refresh(&app_iwdg);
    }
    app_enter_safe_state();
    __disable_irq();
    bootjump_request();
    NVIC_SystemReset();
}

void app_main_loop(void)
{
    uint32_t t = HAL_GetTick();
    diag_loop_mark(DWT->CYCCNT, t);
    if (t != last_1ms) {
        last_1ms = t;
        diag_stack_scan(64);
        app_tick_1ms(t);
    }
    if (t - last_10ms >= 10u) {
        last_10ms = t;
        app_tick_10ms(t);
    }
    usb_proto_poll();   /* USB の受信解析・応答・イベント送信（ブロックしない） */
    if (usb_proto_take_bootloader_request()) {
        bootloader_enter();
    }
    HAL_IWDG_Refresh(&app_iwdg);
}

void app_fault_safe_state(void)
{
    DRV_EN_PORT->BRR = DRV_EN_PIN;
    TIM1->CR1 &= ~TIM_CR1_CEN;
}
