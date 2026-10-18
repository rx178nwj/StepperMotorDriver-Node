/*
 * app.h — 状態遷移・保護・レジスタ処理
 */
#ifndef APP_H
#define APP_H

#include <stdint.h>

typedef enum {
    APP_NO_VM = 0,
    APP_DISABLED = 1,
    APP_ENABLED = 2,
    APP_FAULT = 3,
} app_state_t;

/* FAULT レジスタ（bit0〜2 は drv_fault_t と共通） */
#define FAULT_OPD      (1u << 0)
#define FAULT_ISD      (1u << 1)
#define FAULT_TSD      (1u << 2)
#define FAULT_VM_UV    (1u << 3)
#define FAULT_VM_OV    (1u << 4)
#define FAULT_OVERTEMP (1u << 5)

/* COMMAND レジスタ */
#define CMD_ENABLE            1u
#define CMD_DISABLE           2u
#define CMD_STOP              3u
#define CMD_ESTOP             4u
#define CMD_CLEAR_FAULT       5u
#define CMD_RESET_ELEC_ANGLE  6u
#define CMD_SAVE_SETTINGS     7u   /* 現在の設定を Flash へ保存（停止中のみ） */
#define CMD_LOAD_SETTINGS     8u   /* 保存済み設定へ戻す（停止中のみ） */
#define CMD_FACTORY_SETTINGS  9u   /* 工場出荷値へ戻す（RAM のみ。保存は SAVE で） */

/* CMD_RESULT レジスタ */
#define RESULT_OK             0u
#define RESULT_REJECTED       1u   /* 現在の状態では実行できない */
#define RESULT_INVALID        2u   /* 値が範囲外 */
#define RESULT_FAILED         3u   /* 実行失敗（Flash 書き込み失敗、保存設定なし） */

void app_init(void);
void app_tick_1ms(uint32_t now_ms);
void app_tick_10ms(uint32_t now_ms);

#endif /* APP_H */
