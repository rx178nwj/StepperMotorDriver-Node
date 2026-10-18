/*
 * board.h — ピン割り当て（schematic.kicad_sch 2026-10-11 版、IC2 = STM32L433CCU6）
 * 根拠は docs/requirements.md 2章。
 */
#ifndef BOARD_H
#define BOARD_H

#include "stm32l4xx_hal.h"
#include <stdbool.h>
#include <stdint.h>

/* ---- TB67S249FTG 制御 ---- */
#define DRV_CLK_PORT     GPIOA   /* PA8  TIM1_CH1 (AF1) */
#define DRV_CLK_PIN      GPIO_PIN_8
#define DRV_DIR_PORT     GPIOA   /* PA9  CW/CCW: High = CW */
#define DRV_DIR_PIN      GPIO_PIN_9
#define DRV_EN_PORT      GPIOA   /* PA10 ENABLE: High = 出力ON (R28 プルダウン) */
#define DRV_EN_PIN       GPIO_PIN_10
#define DRV_RESET_PORT   GPIOB   /* PB15 RESET: High = 電気角初期化 */
#define DRV_RESET_PIN    GPIO_PIN_15
#define DRV_DMODE_PORT   GPIOB   /* PB12/13/14 = DMODE0/1/2（同一ポートで同時書き込み） */
#define DRV_DMODE0_PIN   GPIO_PIN_12
#define DRV_DMODE1_PIN   GPIO_PIN_13
#define DRV_DMODE2_PIN   GPIO_PIN_14
#define DRV_AGC_PORT     GPIOB   /* PB10/PB11 = AGC0/AGC1（0.3 で PB8/PB9 から変更） */
#define DRV_AGC0_PIN     GPIO_PIN_10
#define DRV_AGC1_PIN     GPIO_PIN_11
#define DRV_LO_PORT      GPIOA   /* PA2 = LO1, PA3 = LO2（OD, R30/R33 → +3V3） */
#define DRV_LO1_PIN      GPIO_PIN_2
#define DRV_LO2_PIN      GPIO_PIN_3
#define CAN_S_PORT       GPIOC   /* PC15 = CAN_S（TJA1441AT の S。Low＝ノーマル、High＝サイレント） */
#define CAN_S_PIN        GPIO_PIN_15
#define DAC_VREF_PORT    GPIOA   /* PA4 = DAC1_OUT1 */
#define DAC_VREF_PIN     GPIO_PIN_4

/* ---- アナログ入力 ---- */
#define VM_SENSE_PORT    GPIOA   /* PA0 = ADC1_IN5 */
#define VM_SENSE_PIN     GPIO_PIN_0
#define TEMP_PORT        GPIOA   /* PA6 = ADC1_IN11 */
#define TEMP_PIN         GPIO_PIN_6
#define AD_IN_PORT       GPIOA   /* PA7 = ADC1_IN12 (J10.5) */
#define AD_IN_PIN        GPIO_PIN_7

/* ---- CAN1（上位通信、TJA1441AT 経由） ---- */
#define CAN_PORT         GPIOB   /* PB8 = CAN1_RX, PB9 = CAN1_TX, AF9 */
#define CAN_RX_PIN       GPIO_PIN_8
#define CAN_TX_PIN       GPIO_PIN_9

/* ---- アドレス DIP（ON = 1、外付けプルダウン） ---- */
#define ADDR0_PORT       GPIOH   /* PH0 */
#define ADDR0_PIN        GPIO_PIN_0
#define ADDR1_PORT       GPIOH   /* PH1 */
#define ADDR1_PIN        GPIO_PIN_1
#define ADDR2_PORT       GPIOC   /* PC13 */
#define ADDR2_PIN        GPIO_PIN_13

/* ---- スイッチ（アクティブ Low、外付けプルアップ + RC） ---- */
#define SW1_PORT         GPIOB   /* PB4 SWITCH1 */
#define SW1_PIN          GPIO_PIN_4
#define SW2_PORT         GPIOB   /* PB5 SWITCH2 */
#define SW2_PIN          GPIO_PIN_5

/* ---- 電源状態 ---- */
#define PWR_ST_PORT      GPIOC   /* PC14: High = 外部5V, Low = USB */
#define PWR_ST_PIN       GPIO_PIN_14

/* ---- LED（High 点灯） ---- */
#define LED_MCU_PORT     GPIOB   /* PB0 */
#define LED_MCU_PIN      GPIO_PIN_0
#define LED_COMM_PORT    GPIOB   /* PB1 */
#define LED_COMM_PIN     GPIO_PIN_1
#define LED_RUN_PORT     GPIOA   /* PA5 */
#define LED_RUN_PIN      GPIO_PIN_5
#define LED_FAULT_PORT   GPIOB   /* PB2 */
#define LED_FAULT_PIN    GPIO_PIN_2

/* ---- 割り込み優先度（数値が小さいほど高い） ---- */
#define IRQ_PRIO_STEP    0   /* TIM1 更新: パルス生成 */
#define IRQ_PRIO_FAULT   1   /* EXTI2/3: LO1/LO2 */
#define IRQ_PRIO_CAN     2   /* CAN1 RX0/TX/SCE（USB は 3） */

void board_init(void);
void board_delay_us(uint32_t us);
uint8_t board_read_address_id(void);
bool board_pwr_external(void);

static inline void board_pin_write(GPIO_TypeDef *port, uint16_t pin, bool on)
{
    port->BSRR = on ? pin : ((uint32_t)pin << 16);
}

static inline bool board_pin_read(GPIO_TypeDef *port, uint16_t pin)
{
    return (port->IDR & pin) != 0;
}

#endif /* BOARD_H */
