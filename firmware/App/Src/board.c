#include "board.h"

static void gpio_cfg(GPIO_TypeDef *port, uint32_t pins, uint32_t mode, uint32_t pull,
                     uint32_t speed, uint32_t alt)
{
    GPIO_InitTypeDef g = {0};
    g.Pin = pins;
    g.Mode = mode;
    g.Pull = pull;
    g.Speed = speed;
    g.Alternate = alt;
    HAL_GPIO_Init(port, &g);
}

void board_init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOC_CLK_ENABLE();
    __HAL_RCC_GPIOH_CLK_ENABLE();

    /* 出力は Low を先に書いてからモード設定（安全状態: EN=L, DMODE=000, RESET=L, AGC=00） */
    DRV_DIR_PORT->BRR = DRV_DIR_PIN | DRV_EN_PIN | LED_RUN_PIN;
    CAN_S_PORT->BRR = CAN_S_PIN;   /* CAN_S = Low（ノーマルモード） */
    GPIOB->BRR = DRV_RESET_PIN | DRV_DMODE0_PIN | DRV_DMODE1_PIN | DRV_DMODE2_PIN |
                 DRV_AGC0_PIN | DRV_AGC1_PIN | LED_MCU_PIN | LED_COMM_PIN | LED_FAULT_PIN;

    gpio_cfg(GPIOA, DRV_DIR_PIN | DRV_EN_PIN | LED_RUN_PIN,
             GPIO_MODE_OUTPUT_PP, GPIO_NOPULL, GPIO_SPEED_FREQ_LOW, 0);
    gpio_cfg(GPIOB, DRV_RESET_PIN | DRV_DMODE0_PIN | DRV_DMODE1_PIN | DRV_DMODE2_PIN |
                    DRV_AGC0_PIN | DRV_AGC1_PIN | LED_MCU_PIN | LED_COMM_PIN | LED_FAULT_PIN,
             GPIO_MODE_OUTPUT_PP, GPIO_NOPULL, GPIO_SPEED_FREQ_LOW, 0);

    /* CAN_S: PC15（RTC ドメインで駆動能力は小さいが、S は静的入力なので問題ない） */
    gpio_cfg(CAN_S_PORT, CAN_S_PIN, GPIO_MODE_OUTPUT_PP, GPIO_NOPULL, GPIO_SPEED_FREQ_LOW, 0);

    /* CLK: TIM1_CH1。タイマ側で Low 出力に初期化される */
    gpio_cfg(DRV_CLK_PORT, DRV_CLK_PIN, GPIO_MODE_AF_PP, GPIO_NOPULL,
             GPIO_SPEED_FREQ_HIGH, GPIO_AF1_TIM1);

    /* LO1/LO2: 立ち下がりで異常割り込み */
    gpio_cfg(DRV_LO_PORT, DRV_LO1_PIN | DRV_LO2_PIN, GPIO_MODE_IT_FALLING, GPIO_NOPULL,
             GPIO_SPEED_FREQ_LOW, 0);

    /* 入力（外付けプル抵抗あり） */
    gpio_cfg(GPIOC, PWR_ST_PIN | ADDR2_PIN, GPIO_MODE_INPUT, GPIO_NOPULL,
             GPIO_SPEED_FREQ_LOW, 0);
    gpio_cfg(GPIOH, ADDR0_PIN | ADDR1_PIN, GPIO_MODE_INPUT, GPIO_NOPULL, GPIO_SPEED_FREQ_LOW, 0);
    gpio_cfg(GPIOB, SW1_PIN | SW2_PIN, GPIO_MODE_INPUT, GPIO_NOPULL, GPIO_SPEED_FREQ_LOW, 0);

    /* アナログ */
    gpio_cfg(GPIOA, VM_SENSE_PIN | TEMP_PIN | AD_IN_PIN | DAC_VREF_PIN, GPIO_MODE_ANALOG,
             GPIO_NOPULL, GPIO_SPEED_FREQ_LOW, 0);

    /* I2C2 */
    gpio_cfg(I2C_PC_PORT, I2C_PC_SCL_PIN | I2C_PC_SDA_PIN, GPIO_MODE_AF_OD, GPIO_NOPULL,
             GPIO_SPEED_FREQ_HIGH, GPIO_AF4_I2C2);

    /* µs 遅延用サイクルカウンタ */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

    /* デバッガ停止中はパルス生成と IWDG を止める */
    DBGMCU->APB2FZ |= DBGMCU_APB2FZ_DBG_TIM1_STOP;
    DBGMCU->APB1FZR1 |= DBGMCU_APB1FZR1_DBG_IWDG_STOP;
}

void board_delay_us(uint32_t us)
{
    uint32_t start = DWT->CYCCNT;
    uint32_t cycles = us * (SystemCoreClock / 1000000U);
    while ((DWT->CYCCNT - start) < cycles) {
    }
}

uint8_t board_read_address_id(void)
{
    uint8_t id = 0;
    if (board_pin_read(ADDR0_PORT, ADDR0_PIN)) id |= 1u;
    if (board_pin_read(ADDR1_PORT, ADDR1_PIN)) id |= 2u;
    if (board_pin_read(ADDR2_PORT, ADDR2_PIN)) id |= 4u;
    return id;
}

bool board_pwr_external(void)
{
    return board_pin_read(PWR_ST_PORT, PWR_ST_PIN);
}
