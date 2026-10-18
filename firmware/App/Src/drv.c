#include "drv.h"
#include "board.h"
#include "config.h"

static DAC_HandleTypeDef hdac;
static volatile bool awake;
static volatile bool fault_armed;

/* DMODE0/1/2 の組み合わせ（データシート「DMODE のファンクション」）。1/2 は (a) 型 */
static uint32_t dmode_bits(uint8_t microstep)
{
    switch (microstep) {
    case 1:  return DRV_DMODE2_PIN;
    case 2:  return DRV_DMODE1_PIN;
    case 4:  return DRV_DMODE1_PIN | DRV_DMODE2_PIN;
    case 8:  return DRV_DMODE0_PIN | DRV_DMODE2_PIN;
    case 16: return DRV_DMODE0_PIN | DRV_DMODE1_PIN;
    case 32: return DRV_DMODE0_PIN | DRV_DMODE1_PIN | DRV_DMODE2_PIN;
    default: return 0;
    }
}

bool drv_microstep_valid(uint8_t microstep)
{
    return dmode_bits(microstep) != 0;
}

/* 3 ビットを 1 回の BSRR 書き込みで変える（途中で 000=スタンバイを経由しない） */
static void dmode_write(uint32_t bits)
{
    const uint32_t all = DRV_DMODE0_PIN | DRV_DMODE1_PIN | DRV_DMODE2_PIN;
    DRV_DMODE_PORT->BSRR = bits | ((all & ~bits) << 16);
}

void drv_init(void)
{
    __HAL_RCC_DAC1_CLK_ENABLE();
    hdac.Instance = DAC1;
    HAL_DAC_Init(&hdac);

    DAC_ChannelConfTypeDef ch = {0};
    ch.DAC_SampleAndHold = DAC_SAMPLEANDHOLD_DISABLE;
    ch.DAC_Trigger = DAC_TRIGGER_NONE;
    ch.DAC_OutputBuffer = DAC_OUTPUTBUFFER_ENABLE;
    ch.DAC_ConnectOnChipPeripheral = DAC_CHIPCONNECT_DISABLE;
    ch.DAC_UserTrimming = DAC_TRIMMING_FACTORY;
    HAL_DAC_ConfigChannel(&hdac, &ch, DAC_CHANNEL_1);
    /* VREF のプルダウン(R32)がないため、DAC は止めずに最小出力で駆動し続ける */
    HAL_DAC_SetValue(&hdac, DAC_CHANNEL_1, DAC_ALIGN_12B_R, 0);
    HAL_DAC_Start(&hdac, DAC_CHANNEL_1);

    drv_enable(false);
    drv_standby();

    HAL_NVIC_SetPriority(EXTI2_IRQn, IRQ_PRIO_FAULT, 0);
    HAL_NVIC_SetPriority(EXTI3_IRQn, IRQ_PRIO_FAULT, 0);
    __HAL_GPIO_EXTI_CLEAR_IT(DRV_LO1_PIN | DRV_LO2_PIN);
    HAL_NVIC_EnableIRQ(EXTI2_IRQn);
    HAL_NVIC_EnableIRQ(EXTI3_IRQn);
}

void drv_wake(uint8_t microstep)
{
    uint32_t bits = dmode_bits(microstep);
    if (bits == 0) {
        return;
    }
    dmode_write(bits);
    if (!awake) {
        board_delay_us(DRV_WAKE_US);
        awake = true;
    }
}

void drv_standby(void)
{
    drv_fault_irq_arm(false);
    awake = false;
    dmode_write(0);
}

bool drv_is_awake(void)
{
    return awake;
}

void drv_enable(bool on)
{
    board_pin_write(DRV_EN_PORT, DRV_EN_PIN, on);
}

bool drv_is_enabled(void)
{
    return (DRV_EN_PORT->ODR & DRV_EN_PIN) != 0;
}

void drv_set_current_ma(uint16_t ma, uint32_t vdda_mv)
{
    if (ma < CURRENT_MIN_MA) ma = CURRENT_MIN_MA;
    if (ma > CURRENT_LIMIT_MA) ma = CURRENT_LIMIT_MA;
    if (vdda_mv < 1800u) {
        vdda_mv = 3300u;
    }
    /* V_DAC = I / gain / 分圧比 */
    float vdac_mv = (float)ma / DRV_VREF_GAIN_A_PER_V / VREF_DIV_RATIO;
    float code = vdac_mv / (float)vdda_mv * 4095.0f + 0.5f;
    uint32_t c = code > 4095.0f ? 4095u : (uint32_t)code;
    HAL_DAC_SetValue(&hdac, DAC_CHANNEL_1, DAC_ALIGN_12B_R, c);
}

void drv_set_current_min(void)
{
    HAL_DAC_SetValue(&hdac, DAC_CHANNEL_1, DAC_ALIGN_12B_R, 0);
}

void drv_pulse_reset(void)
{
    board_pin_write(DRV_RESET_PORT, DRV_RESET_PIN, true);
    board_delay_us(10);  /* 内部フィルタ 0.625 µs */
    board_pin_write(DRV_RESET_PORT, DRV_RESET_PIN, false);
}

drv_fault_t drv_read_fault(void)
{
    bool lo1 = board_pin_read(DRV_LO_PORT, DRV_LO1_PIN);
    bool lo2 = board_pin_read(DRV_LO_PORT, DRV_LO2_PIN);
    if (!lo1 && !lo2) return DRV_FAULT_TSD;
    if (!lo1)         return DRV_FAULT_ISD;
    if (!lo2)         return DRV_FAULT_OPD;
    return DRV_FAULT_NONE;
}

void drv_fault_irq_arm(bool armed)
{
    __HAL_GPIO_EXTI_CLEAR_IT(DRV_LO1_PIN | DRV_LO2_PIN);
    fault_armed = armed;
}

/* EXTI2/EXTI3 から呼ばれる */
void HAL_GPIO_EXTI_Callback(uint16_t pin)
{
    if (!fault_armed || !awake) {
        return;
    }
    if (pin == DRV_LO1_PIN || pin == DRV_LO2_PIN) {
        /* もう一方の LO が遅れて落ちる場合に備え、短く待ってから両方を読む */
        board_delay_us(2);
        drv_fault_t f = drv_read_fault();
        if (f != DRV_FAULT_NONE) {
            app_on_driver_fault_isr(f);
        }
    }
}
