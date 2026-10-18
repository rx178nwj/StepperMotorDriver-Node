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
} settings_t;

#define SETTINGS_FLAG_INVERT_DIR  (1u << 0)
#define SETTINGS_FLAG_LOCAL_JOG   (1u << 1)

void settings_defaults(settings_t *s);
/* 範囲外の値を丸める。丸めた項目があれば true */
bool settings_sanitize(settings_t *s);

/* 最新の有効レコードを読む。なければ false（*s は変更しない） */
bool settings_load(settings_t *s);
/* 追記保存。モータ停止中に呼ぶこと（消去中は CPU が最大約 25 ms 停止する） */
bool settings_save(const settings_t *s);

#endif /* SETTINGS_H */
