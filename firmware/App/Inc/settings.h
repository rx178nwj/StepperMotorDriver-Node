/*
 * settings.h — モータ設定と不揮発保存
 *
 * STM32L433 には EEPROM がないため、内蔵 Flash 末尾の 2 ページ（各 2KB）を
 * EEPROM の代わりに使う。64 バイトのレコードを追記し、起動時に最新の有効レコードを読む。
 */
#ifndef SETTINGS_H
#define SETTINGS_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint16_t run_current_ma;   /* 運転電流 */
    uint16_t hold_current_ma;  /* 保持電流 */
    uint8_t  microstep;        /* 1/2/4/8/16/32 */
    uint8_t  flags;            /* bit0 方向反転、bit1 ローカルジョグ */
    uint16_t comm_timeout_ms;  /* 0 = 無効 */
    uint32_t max_speed;        /* step/s */
    uint32_t accel;            /* step/s^2 */
    uint32_t decel;            /* step/s^2 */
    uint32_t start_speed;      /* step/s */
    uint32_t jog_speed;        /* step/s */
    uint16_t hold_delay_ms;
    uint16_t steps_per_rev;    /* モータのフルステップ数/回転（上位側の換算用） */
    /* ---- 以下はレコード版 2 で追加（位置センサ、3.10 節）。版 1 のレコードは既定値で補う ---- */
    uint16_t gear_ratio;       /* モータ軸 : 出力軸、1〜1000 */
    uint16_t sensor_offset;    /* 出力軸の原点に対する RAW ANGLE、0〜4095 */
    uint8_t  pa7_mode;         /* 0 AD_IN、1 DIR、2 Z */
    /* ---- 以下はレコード版 3 で追加（J10、3.12 節）。版 1・2 のレコードは既定値で補う ---- */
    uint8_t  j10_mode;         /* 0 未使用、1 ENC、2 HOME */
    uint16_t home_speed;       /* 原点復帰の探索速度 [step/s]、1〜65535 */
} settings_t;                  /* パディングなし（メモリ比較・保存でパディングの不定値を避ける） */

#define SETTINGS_FLAG_INVERT_DIR  (1u << 0)
#define SETTINGS_FLAG_LOCAL_JOG   (1u << 1)
#define SETTINGS_FLAG_SENSOR_DIR  (1u << 2)   /* PA7_MODE=1 のとき AS5600 の DIR ピンの出力レベル */
#define SETTINGS_FLAG_AGC         (1u << 3)   /* AGC 有効（3.11 節） */
#define SETTINGS_FLAG_HOME_ACTIVE_HIGH (1u << 4)   /* HOME 入力の論理: 1 = アクティブ High（3.12 節） */

void settings_defaults(settings_t *s);
/* 範囲外の値を丸める。丸めた項目があれば true */
bool settings_sanitize(settings_t *s);

/* 最新の有効レコードを読む。なければ false（*s は変更しない） */
bool settings_load(settings_t *s);
/* 追記保存。モータ停止中に呼ぶこと（消去中は CPU が最大約 25 ms 停止する） */
bool settings_save(const settings_t *s);

/* Flash 読み出しで ECC エラー（NMI）が起きると立つ。読む側が false にしてから読み、読み終えて確認する
 * （設定レコードの読み出しと USB のメモリ読み出しで共用。NMI_Handler は app_nmi_handled で立てる） */
extern volatile bool nv_ecc_error;

#endif /* SETTINGS_H */
