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
    uint32_t MODER;  /* j10.c が直接設定する（00 入力、01 出力、10 AF、11 アナログ） */
    uint32_t PUPDR;  /* 00 なし、01 プルアップ、10 プルダウン */
    uint32_t AFR[2]; /* 代替機能（1 ピン 4 ビット） */
} GPIO_TypeDef;

typedef struct {
    uint32_t Pin, Mode, Pull, Speed, Alternate;
} GPIO_InitTypeDef;

extern volatile uint32_t uwTick;   /* diag.c が名前表に登録する。実体は試験側 */

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
    uint32_t CR1, CR2, SMCR, DIER, SR, EGR, CCMR1, CCER, CNT, PSC, ARR, RCR, CCR1, BDTR;
} TIM_TypeDef;

extern TIM_TypeDef g_tim1;
#define TIM1 (&g_tim1)

/* ---- TIM2（j10.c のエンコーダモード）。クロックの有効・無効は g_tim2_clk に記録する ---- */
extern TIM_TypeDef g_tim2;
extern int g_tim2_clk;
#define TIM2 (&g_tim2)
#define __HAL_RCC_TIM2_CLK_ENABLE()   (g_tim2_clk = 1)
#define __HAL_RCC_TIM2_CLK_DISABLE()  (g_tim2_clk = 0)
#define TIM_SMCR_SMS_Pos   0u
#define TIM_CCMR1_CC1S_Pos 0u
#define TIM_CCMR1_IC1F_Pos 4u
#define TIM_CCMR1_CC2S_Pos 8u
#define TIM_CCMR1_IC2F_Pos 12u
#define TIM_CCER_CC2E      (1u << 4)

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

/* ---- CAN1（comm.c）。実体は test_comm.c（送信メールボックス・受信 FIFO を模擬する） ---- */
#define ENABLE   1u
#define DISABLE  0u

typedef struct {
    uint32_t ESR;
} CAN_TypeDef;

extern CAN_TypeDef g_can1;
#define CAN1 (&g_can1)
#define CAN_ESR_EPVF  (1u << 1)
#define CAN_ESR_BOFF  (1u << 2)
#define __HAL_RCC_CAN1_CLK_ENABLE()  ((void)0)
#define CAN1_RX0_IRQn  20
#define CAN1_SCE_IRQn  22

#define CAN_MODE_NORMAL        0u
#define CAN_SJW_1TQ            0u
#define CAN_BS1_13TQ           12u
#define CAN_BS2_2TQ            1u
#define CAN_ID_STD             0u
#define CAN_ID_EXT             4u
#define CAN_RTR_DATA           0u
#define CAN_RTR_REMOTE         2u
#define CAN_RX_FIFO0           0u
#define CAN_FILTERMODE_IDMASK  0u
#define CAN_FILTERSCALE_16BIT  0u
#define CAN_FILTER_FIFO0       0u
#define CAN_IT_RX_FIFO0_MSG_PENDING  (1u << 1)
#define CAN_IT_RX_FIFO0_OVERRUN      (1u << 3)
#define CAN_IT_ERROR_PASSIVE         (1u << 5)
#define CAN_IT_BUSOFF                (1u << 6)
#define CAN_IT_ERROR                 (1u << 15)
#define HAL_CAN_ERROR_NONE     0u
#define HAL_CAN_ERROR_EPV      (1u << 1)
#define HAL_CAN_ERROR_BOF      (1u << 2)
#define HAL_CAN_ERROR_RX_FOV0  (1u << 9)

typedef struct {
    uint32_t Prescaler, Mode, SyncJumpWidth, TimeSeg1, TimeSeg2;
    uint32_t TimeTriggeredMode, AutoBusOff, AutoWakeUp, AutoRetransmission;
    uint32_t ReceiveFifoLocked, TransmitFifoPriority;
} CAN_InitTypeDef;

typedef struct {
    CAN_TypeDef *Instance;
    CAN_InitTypeDef Init;
    uint32_t ErrorCode;
} CAN_HandleTypeDef;

typedef struct {
    uint32_t FilterIdHigh, FilterIdLow, FilterMaskIdHigh, FilterMaskIdLow;
    uint32_t FilterFIFOAssignment, FilterBank, FilterMode, FilterScale;
    uint32_t FilterActivation, SlaveStartFilterBank;
} CAN_FilterTypeDef;

typedef struct {
    uint32_t StdId, ExtId, IDE, RTR, DLC, TransmitGlobalTime;
} CAN_TxHeaderTypeDef;

typedef struct {
    uint32_t StdId, ExtId, IDE, RTR, DLC, Timestamp, FilterMatchIndex;
} CAN_RxHeaderTypeDef;

uint32_t HAL_GetTick(void);
HAL_StatusTypeDef HAL_CAN_Init(CAN_HandleTypeDef *h);
HAL_StatusTypeDef HAL_CAN_ConfigFilter(CAN_HandleTypeDef *h, CAN_FilterTypeDef *f);
HAL_StatusTypeDef HAL_CAN_ActivateNotification(CAN_HandleTypeDef *h, uint32_t it);
HAL_StatusTypeDef HAL_CAN_Start(CAN_HandleTypeDef *h);
uint32_t HAL_CAN_GetTxMailboxesFreeLevel(CAN_HandleTypeDef *h);
HAL_StatusTypeDef HAL_CAN_AddTxMessage(CAN_HandleTypeDef *h, CAN_TxHeaderTypeDef *hd,
                                       const uint8_t *data, uint32_t *mailbox);
uint32_t HAL_CAN_GetRxFifoFillLevel(CAN_HandleTypeDef *h, uint32_t fifo);
HAL_StatusTypeDef HAL_CAN_GetRxMessage(CAN_HandleTypeDef *h, uint32_t fifo,
                                       CAN_RxHeaderTypeDef *hd, uint8_t *data);
uint32_t HAL_CAN_GetError(CAN_HandleTypeDef *h);
void HAL_CAN_IRQHandler(CAN_HandleTypeDef *h);
void HAL_CAN_RxFifo0MsgPendingCallback(CAN_HandleTypeDef *h);
void HAL_CAN_ErrorCallback(CAN_HandleTypeDef *h);

/* ---- Flash（settings.c）。実体は test_settings.c（RAM 上の配列で模擬する） ---- */
typedef struct {
    uint32_t ECCR;
} FLASH_TypeDef;

extern FLASH_TypeDef g_flash_regs;
#define FLASH (&g_flash_regs)
#define FLASH_ECCR_ECCD  (1u << 31)

#define FLASH_PAGE_SIZE               2048u
#define FLASH_TYPEERASE_PAGES         0u
#define FLASH_BANK_1                  1u
#define FLASH_TYPEPROGRAM_DOUBLEWORD  0u
#define FLASH_FLAG_ALL_ERRORS         0x0000C3FAu
#define __HAL_FLASH_CLEAR_FLAG(f)     ((void)(f))

typedef struct {
    uint32_t TypeErase, Banks, Page, NbPages;
} FLASH_EraseInitTypeDef;

void test_nv_memcpy(void *dst, uint32_t addr, uint32_t n);   /* settings.c の Flash 読み出し（NV_MEMCPY）の差し替え先 */
HAL_StatusTypeDef HAL_FLASH_Unlock(void);
HAL_StatusTypeDef HAL_FLASH_Lock(void);
HAL_StatusTypeDef HAL_FLASH_Program(uint32_t type, uint32_t address, uint64_t data);
HAL_StatusTypeDef HAL_FLASHEx_Erase(FLASH_EraseInitTypeDef *init, uint32_t *page_error);

#endif /* STM32L4XX_HAL_H */
