/*
 * stepper.c / position.c をホストで単体テストするためのハードウェアスタブ（レジスタの実体）。
 */
#include "stm32l4xx_hal.h"
#include "i2c.h"
#include "board.h"

GPIO_TypeDef g_gpioa, g_gpiob, g_gpioc, g_gpioh;
TIM_TypeDef g_tim1;
I2C_TypeDef g_i2c1;
DWT_TypeDef g_dwt;
I2C_HandleTypeDef hi2c1;
uint32_t SystemCoreClock = 80000000u;
uint32_t g_primask;

void HAL_GPIO_Init(GPIO_TypeDef *port, GPIO_InitTypeDef *init)
{
    (void)port;
    (void)init;
}

void HAL_NVIC_SetPriority(int irq, uint32_t prio, uint32_t sub)
{
    (void)irq;
    (void)prio;
    (void)sub;
}

void HAL_NVIC_EnableIRQ(int irq)
{
    (void)irq;
}

HAL_StatusTypeDef HAL_I2C_Init(I2C_HandleTypeDef *h)
{
    (void)h;
    return HAL_OK;
}

HAL_StatusTypeDef HAL_I2CEx_ConfigAnalogFilter(I2C_HandleTypeDef *h, uint32_t filter)
{
    (void)h;
    (void)filter;
    return HAL_OK;
}

void board_delay_us(uint32_t us)
{
    (void)us;
}
