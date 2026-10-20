/*
 * drv.h — TB67S249FTG 制御（DMODE / ENABLE / RESET / VREF(DAC) / LO1・LO2）
 */
#ifndef DRV_H
#define DRV_H

#include <stdbool.h>
#include <stdint.h>

/* LO1/LO2 から判定した異常（FAULT レジスタの bit 位置と一致） */
typedef enum {
    DRV_FAULT_NONE = 0,
    DRV_FAULT_OPD = 1u << 0,  /* LO1=H, LO2=L 負荷オープン */
    DRV_FAULT_ISD = 1u << 1,  /* LO1=L, LO2=H 過電流 */
    DRV_FAULT_TSD = 1u << 2,  /* LO1=L, LO2=L 過熱 */
} drv_fault_t;

void drv_init(void);

bool drv_microstep_valid(uint8_t microstep);
/* スタンバイ解除して分割を設定（DRV_WAKE_US 待つ）。awake 中に呼べば分割変更のみ */
void drv_wake(uint8_t microstep);
void drv_standby(void);
bool drv_is_awake(void);

void drv_enable(bool on);
bool drv_is_enabled(void);

/* AGC0/AGC1 を同じレベルで出す（ON=両方 High、OFF=両方 Low。(High, Low) は使わない）。
 * 呼ぶのは EN=Low の間だけ（動作中に AGC0 を切り替えない）。drv_get_agc は出力レベルが両方 High なら true */
void drv_set_agc(bool on);
bool drv_get_agc(void);

/* 電流 [mA]（CURRENT_MIN_MA〜CURRENT_LIMIT_MA に制限）。VDDA [mV] で DAC コードを補正する */
void drv_set_current_ma(uint16_t ma, uint32_t vdda_mv);
/* DAC 最小出力（約 0.2 V）。電流を 0 にするには ENABLE を Low にする */
void drv_set_current_min(void);

void drv_pulse_reset(void);
drv_fault_t drv_read_fault(void);

/* LO 割り込みの有効化（VM 正常かつスタンバイ解除後のみ有効にする） */
void drv_fault_irq_arm(bool armed);

/* LO1/LO2 割り込みで呼ばれる（app 側で実装） */
void app_on_driver_fault_isr(drv_fault_t fault);

#endif /* DRV_H */
