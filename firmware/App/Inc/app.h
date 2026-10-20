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
#define CMD_SYNC_POSITION     10u  /* 位置センサの角度から POSITION を設定する（停止中のみ） */
#define CMD_HOME_NEG          11u  /* − 側（HOME2）の原点復帰（ENABLED・停止中・J10_MODE=2 のみ） */
#define CMD_HOME_POS          12u  /* ＋ 側（HOME1）の原点復帰 */

/* CMD_RESULT レジスタ */
#define RESULT_OK             0u
#define RESULT_REJECTED       1u   /* 現在の状態では実行できない */
#define RESULT_INVALID        2u   /* 値が範囲外 */
#define RESULT_FAILED         3u   /* 実行失敗（Flash 書き込み失敗、保存設定なし、位置センサ無効） */

/*
 * レジスタアクセス（通信トランスポートに依存しない。main ループから呼ぶ）
 * app_reg_write: reg から len バイトを書く（len 1〜6）。戻り値は CMD_RESULT（CMD_RESULT レジスタにも反映）。
 *   32bit レジスタは全バイトが含まれる場合だけ反映し、部分書き込み・書き込み不可のバイトを含む場合は
 *   RESULT_INVALID。複数レジスタはアドレス昇順で反映する。
 * app_reg_read: reg から len バイトのスナップショットを out へ。マップ外は 0xFF。
 * app_broadcast_command: ブロードキャスト指令（DISABLE/STOP/ESTOP）。CMD_RESULT は更新しない。
 */
uint8_t app_reg_write(uint8_t reg, const uint8_t *data, uint8_t len);
void app_reg_read(uint8_t reg, uint8_t len, uint8_t *out);
void app_broadcast_command(uint8_t cmd);

/* 現在の状態（USB の ENTER_BOOTLOADER が ENABLED 中かを調べる、FR-BL-02） */
app_state_t app_get_state(void);
/* ブートローダ移行の直前: ドライバを安全状態にする（EN=Low、DMODE=000 スタンバイ、DAC 最小。FR-BL-03） */
void app_enter_safe_state(void);

void app_init(void);
void app_tick_1ms(uint32_t now_ms);
void app_tick_10ms(uint32_t now_ms);

#endif /* APP_H */
