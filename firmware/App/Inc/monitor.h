/*
 * monitor.h — ADC1 による電源・温度計測（main ループから 10 ms 周期で呼ぶ）
 */
#ifndef MONITOR_H
#define MONITOR_H

#include <stdint.h>

typedef struct {
    uint32_t vdda_mv;        /* VREFINT から算出 */
    uint32_t vm_mv;          /* モータ電源 */
    int16_t  board_temp_dc;  /* TH1 [0.1 °C]、未接続/短絡は TEMP_INVALID */
    int16_t  mcu_temp_dc;    /* 内部センサ [0.1 °C] */
    uint32_t ad_in_mv;       /* J10.5 */
} monitor_values_t;

void monitor_init(void);
void monitor_sample(monitor_values_t *out);

#endif /* MONITOR_H */
