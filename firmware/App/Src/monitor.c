#include "monitor.h"
#include "board.h"
#include "config.h"
#include <math.h>

static ADC_HandleTypeDef hadc;

void monitor_init(void)
{
    __HAL_RCC_ADC_CLK_ENABLE();

    hadc.Instance = ADC1;
    hadc.Init.ClockPrescaler = ADC_CLOCK_SYNC_PCLK_DIV4;   /* 20 MHz */
    hadc.Init.Resolution = ADC_RESOLUTION_12B;
    hadc.Init.DataAlign = ADC_DATAALIGN_RIGHT;
    hadc.Init.ScanConvMode = ADC_SCAN_DISABLE;
    hadc.Init.EOCSelection = ADC_EOC_SINGLE_CONV;
    hadc.Init.LowPowerAutoWait = DISABLE;
    hadc.Init.ContinuousConvMode = DISABLE;
    hadc.Init.NbrOfConversion = 1;
    hadc.Init.DiscontinuousConvMode = DISABLE;
    hadc.Init.ExternalTrigConv = ADC_SOFTWARE_START;
    hadc.Init.ExternalTrigConvEdge = ADC_EXTERNALTRIGCONVEDGE_NONE;
    hadc.Init.DMAContinuousRequests = DISABLE;
    hadc.Init.Overrun = ADC_OVR_DATA_OVERWRITTEN;
    hadc.Init.OversamplingMode = DISABLE;
    HAL_ADC_Init(&hadc);
    HAL_ADCEx_Calibration_Start(&hadc, ADC_SINGLE_ENDED);
}

static uint32_t read_channel(uint32_t channel)
{
    ADC_ChannelConfTypeDef c = {0};
    c.Channel = channel;
    c.Rank = ADC_REGULAR_RANK_1;
    c.SamplingTime = ADC_SAMPLETIME_247CYCLES_5;
    c.SingleDiff = ADC_SINGLE_ENDED;
    c.OffsetNumber = ADC_OFFSET_NONE;
    c.Offset = 0;
    if (HAL_ADC_ConfigChannel(&hadc, &c) != HAL_OK) {
        return 0;
    }
    HAL_ADC_Start(&hadc);
    uint32_t v = 0;
    if (HAL_ADC_PollForConversion(&hadc, 2) == HAL_OK) {
        v = HAL_ADC_GetValue(&hadc);
    }
    HAL_ADC_Stop(&hadc);
    return v;
}

/* 分圧比だけで決まるので VDDA に依存しない */
static int16_t ntc_temp_dc(uint32_t raw)
{
    if (raw < 20u || raw > 4075u) {
        return TEMP_INVALID;   /* 短絡 / 未実装・断線 */
    }
    float r = NTC_PULLUP_OHM * (float)raw / (float)(4095u - raw);
    float inv_t = 1.0f / 298.15f + logf(r / NTC_R25_OHM) / NTC_BETA;
    float t = 1.0f / inv_t - 273.15f;
    return (int16_t)lrintf(t * 10.0f);
}

static int16_t mcu_temp_dc(uint32_t raw, uint32_t vdda_mv)
{
    /* 校正値は VREF+ = 3.0 V で取得されている */
    float raw_cal = (float)raw * (float)vdda_mv / (float)TEMPSENSOR_CAL_VREFANALOG;
    float c1 = (float)*TEMPSENSOR_CAL1_ADDR;
    float c2 = (float)*TEMPSENSOR_CAL2_ADDR;
    float t = (float)(TEMPSENSOR_CAL2_TEMP - TEMPSENSOR_CAL1_TEMP) * (raw_cal - c1) / (c2 - c1) +
              (float)TEMPSENSOR_CAL1_TEMP;
    return (int16_t)lrintf(t * 10.0f);
}

void monitor_sample(monitor_values_t *out)
{
    uint32_t vref_raw = read_channel(ADC_CHANNEL_VREFINT);
    uint32_t vdda = vref_raw ? (VREFINT_CAL_VREF * (uint32_t)*VREFINT_CAL_ADDR) / vref_raw : 3300u;

    uint32_t vm_raw = read_channel(ADC_CHANNEL_5);     /* PA0 */
    uint32_t temp_raw = read_channel(ADC_CHANNEL_11);  /* PA6 */
    uint32_t ad_raw = read_channel(ADC_CHANNEL_12);    /* PA7 */
    uint32_t ts_raw = read_channel(ADC_CHANNEL_TEMPSENSOR);

    out->vdda_mv = vdda;
    out->vm_mv = (uint32_t)((float)vm_raw * (float)vdda / 4095.0f * VM_DIVIDER_RATIO + 0.5f);
    out->board_temp_dc = ntc_temp_dc(temp_raw);
    out->mcu_temp_dc = mcu_temp_dc(ts_raw, vdda);
    out->ad_in_mv = ad_raw * vdda / 4095u;
}
