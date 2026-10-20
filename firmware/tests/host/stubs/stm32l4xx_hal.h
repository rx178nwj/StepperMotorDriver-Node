/*
 * ホスト単体テスト用の最小スタブ。実機の stm32l4xx_hal.h（HAL・CMSIS）の代わりに
 * stepper.c / position.c / board.h が使う分だけを宣言する。実体は ../stubs_hw.c。
 * ペリフェラルのレジスタは構造体の実体で、テストが直接読み書きする。
 */
#ifndef STM32L4XX_HAL_H
#define STM32L4XX_HAL_H

#include <stdbool.h>
#include <stdint.h>

/* ---- GPIO ---- */
typedef struct {
    uint32_t BSRR;   /* 最後に書いた値（テストが読む） */
    uint32_t IDR;    /* テストが設定する入力レベル */
} GPIO_TypeDef;

typedef struct {
    uint32_t Pin, Mode, Pull, Speed, Alternate;
} GPIO_InitTypeDef;

extern GPIO_TypeDef g_gpioa, g_gpiob, g_gpioc, g_gpioh;
#define GPIOA (&g_gpioa)
#define GPIOB (&g_gpiob)
#define GPIOC (&g_gpioc)
#define GPIOH (&g_gpioh)

#define GPIO_PIN_0   0x0001u
#define GPIO_PIN_1   0x0002u
#define GPIO_PIN_2   0x0004u
#define GPIO_PIN_3   0x0008u
#define GPIO_PIN_4   0x0010u
#define GPIO_PIN_5   0x0020u
#define GPIO_PIN_6   0x0040u
#define GPIO_PIN_7   0x0080u
#define GPIO_PIN_8   0x0100u
#define GPIO_PIN_9   0x0200u
#define GPIO_PIN_10  0x0400u
#define GPIO_PIN_11  0x0800u
#define GPIO_PIN_12  0x1000u
#define GPIO_PIN_13  0x2000u
#define GPIO_PIN_14  0x4000u
#define GPIO_PIN_15  0x8000u

#define GPIO_MODE_INPUT       0u
#define GPIO_MODE_OUTPUT_PP   1u
#define GPIO_MODE_OUTPUT_OD   2u
#define GPIO_MODE_AF_OD       3u
#define GPIO_MODE_ANALOG      4u
#define GPIO_NOPULL           0u
#define GPIO_SPEED_FREQ_LOW        0u
#define GPIO_SPEED_FREQ_VERY_HIGH  3u
#define GPIO_AF4_I2C1         4u

void HAL_GPIO_Init(GPIO_TypeDef *port, GPIO_InitTypeDef *init);

/* ---- TIM1 ---- */
typedef struct {
    uint32_t CR1, CR2, DIER, SR, EGR, CCMR1, CCER, CNT, PSC, ARR, RCR, CCR1, BDTR;
} TIM_TypeDef;

extern TIM_TypeDef g_tim1;
#define TIM1 (&g_tim1)

#define TIM_CR1_CEN        (1u << 0)
#define TIM_CR1_URS        (1u << 2)
#define TIM_CR1_OPM        (1u << 3)
#define TIM_CR1_ARPE       (1u << 7)
#define TIM_DIER_UIE       (1u << 0)
#define TIM_SR_UIF         (1u << 0)
#define TIM_EGR_UG         (1u << 0)
#define TIM_CCMR1_OC1PE    (1u << 3)
#define TIM_CCMR1_OC1M_Pos 4u
#define TIM_CCMR1_OC1M     (7u << TIM_CCMR1_OC1M_Pos)
#define TIM_CCER_CC1E      (1u << 0)
#define TIM_BDTR_MOE       (1u << 15)

#define TIM1_UP_TIM16_IRQn 25
#define __HAL_RCC_TIM1_CLK_ENABLE()  ((void)0)
void HAL_NVIC_SetPriority(int irq, uint32_t prio, uint32_t sub);
void HAL_NVIC_EnableIRQ(int irq);

/* ---- CMSIS: PRIMASK ---- */
extern uint32_t g_primask;
static inline uint32_t __get_PRIMASK(void) { return g_primask; }
static inline void __set_PRIMASK(uint32_t v) { g_primask = v; }
static inline void __disable_irq(void) { g_primask = 1u; }

/* ---- I2C1（position.c） ---- */
typedef struct {
    uint32_t CR1, CR2, ISR, ICR, RXDR, TXDR;
} I2C_TypeDef;

extern I2C_TypeDef g_i2c1;
#define I2C1 (&g_i2c1)
#define I2C_CR1_PE        (1u << 0)
#define I2C_CR2_NBYTES_Pos 16u
#define I2C_CR2_START     (1u << 13)
#define I2C_CR2_AUTOEND   (1u << 25)
#define I2C_CR2_RD_WRN    (1u << 10)
#define I2C_ISR_TXIS      (1u << 1)
#define I2C_ISR_RXNE      (1u << 2)
#define I2C_ISR_NACKF     (1u << 4)
#define I2C_ISR_STOPF     (1u << 5)
#define I2C_ISR_TC        (1u << 6)
#define I2C_ISR_BERR      (1u << 8)
#define I2C_ISR_ARLO      (1u << 9)
#define I2C_ISR_BUSY      (1u << 15)
#define I2C_ICR_NACKCF    (1u << 4)
#define I2C_ICR_STOPCF    (1u << 5)
#define I2C_ICR_BERRCF    (1u << 8)
#define I2C_ICR_ARLOCF    (1u << 9)

#define I2C_ADDRESSINGMODE_7BIT   1u
#define I2C_DUALADDRESS_DISABLE   0u
#define I2C_OA2_NOMASK            0u
#define I2C_GENERALCALL_DISABLE   0u
#define I2C_NOSTRETCH_DISABLE     0u
#define I2C_ANALOGFILTER_ENABLE   0u

typedef struct {
    uint32_t Timing, OwnAddress1, AddressingMode, DualAddressMode, OwnAddress2;
    uint32_t OwnAddress2Masks, GeneralCallMode, NoStretchMode;
} I2C_InitTypeDef;

typedef struct {
    I2C_TypeDef *Instance;
    I2C_InitTypeDef Init;
} I2C_HandleTypeDef;

typedef enum { HAL_OK = 0, HAL_ERROR = 1 } HAL_StatusTypeDef;
HAL_StatusTypeDef HAL_I2C_Init(I2C_HandleTypeDef *h);
HAL_StatusTypeDef HAL_I2CEx_ConfigAnalogFilter(I2C_HandleTypeDef *h, uint32_t filter);

/* ---- DWT・クロック ---- */
typedef struct { uint32_t CYCCNT; } DWT_TypeDef;
extern DWT_TypeDef g_dwt;
#define DWT (&g_dwt)
extern uint32_t SystemCoreClock;

#endif /* STM32L4XX_HAL_H */
