/*
 * アプリが直接使う割り込みハンドラ。
 * これらの割り込みは .ioc で NVIC を有効にしていないため CubeMX は生成しない
 * （優先度と有効化は各モジュールの init で行う）。
 */
#include "board.h"
#include "comm.h"
#include "stepper.h"

void TIM1_UP_TIM16_IRQHandler(void)
{
    stepper_timer_isr();
}

void EXTI2_IRQHandler(void)
{
    HAL_GPIO_EXTI_IRQHandler(DRV_LO1_PIN);
}

void EXTI3_IRQHandler(void)
{
    HAL_GPIO_EXTI_IRQHandler(DRV_LO2_PIN);
}

/* CAN1: 受信（FIFO0）とエラー。送信割り込みは使わない（メールボックスの空きを見て投入する） */
void CAN1_RX0_IRQHandler(void)
{
    comm_can_rx0_isr();
}

void CAN1_SCE_IRQHandler(void)
{
    comm_can_sce_isr();
}
