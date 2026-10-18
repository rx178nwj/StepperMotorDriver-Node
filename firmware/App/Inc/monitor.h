/*
 * monitor.h — ADC1 による電源・温度計測（main ループから 10 ms 周期で呼ぶ）
 */
#ifndef MONITOR_H
#define MONITOR_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint32_t vdda_mv;        /* VREFINT から算出 */
    uint32_t vm_mv;          /* モータ電源 */
    int16_t  board_temp_dc;  /* TH1 [0.1 °C]、未接続/短絡は TEMP_INVALID */
    int16_t  mcu_temp_dc;    /* 内部センサ [0.1 °C] */
    uint32_t ad_in_mv;       /* J10.5。計測しなかったとき（PA7_MODE≠0）は 0xFFFF */
} monitor_values_t;

void monitor_init(void);
/* measure_ad_in: PA7 がアナログ入力（PA7_MODE=0）のときだけ true。false のとき PA7 は変換しない */
void monitor_sample(monitor_values_t *out, bool measure_ad_in);

#endif /* MONITOR_H */
