/*
 * app_main.h — CubeMX 生成の main.c から呼ぶアプリの入口
 *
 * main.c（CubeMX）が HAL_Init・SystemClock_Config・MX_USB_DEVICE_Init を行った後、
 * USER CODE 2 で app_main_init()、while ループ内（USER CODE 3）で app_main_loop() を呼ぶ。
 */
#ifndef APP_MAIN_H
#define APP_MAIN_H

#include <stdbool.h>

void app_main_init(void);
void app_main_loop(void);

/* 割り込みハンドラ（stm32l4xx_it.c の USER CODE から呼ぶ） */
bool app_nmi_handled(void);        /* Flash ECC 由来なら true（処理済み） */
void app_fault_safe_state(void);   /* HardFault 時にドライバ出力を止める */

#endif /* APP_MAIN_H */
