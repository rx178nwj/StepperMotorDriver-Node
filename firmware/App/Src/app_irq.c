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

void I2C2_EV_IRQHandler(void)
{
    comm_ev_isr();
}

void I2C2_ER_IRQHandler(void)
{
    comm_er_isr();
}
