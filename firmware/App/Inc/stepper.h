/*
 * stepper.h — TIM1_CH1 による CLK 生成と台形加減速
 *
 * 1 周期 = 1 ステップ。パルスは周期の終わりに出し（PWM モード 2）、直後の更新割り込みで
 * 位置を数える。ARR/PSC/CCR1 はプリロードなので、割り込みでは「次の次」の周期を設定する。
 */
#ifndef STEPPER_H
#define STEPPER_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    float max_speed;    /* step/s */
    float accel;        /* step/s^2 */
    float decel;        /* step/s^2 */
    float start_speed;  /* step/s */
    bool  invert_dir;
} stepper_params_t;

void stepper_init(void);
void stepper_set_params(const stepper_params_t *p);

/* 動作指令。実際の起動は stepper_service() が行う */
void stepper_move_to(int32_t target);
void stepper_move_by(int32_t delta);
void stepper_run(int32_t speed);   /* 符号付き step/s。0 で減速停止 */
void stepper_stop(void);           /* 減速停止 */
void stepper_estop(void);          /* 即時停止（割り込みからも可） */

/* 停止中で、未達の指令があれば起動する。起動したら true */
bool stepper_service(void);
/* 起動すべき指令が残っているか（起動前の電流切替判断用） */
bool stepper_has_pending(void);

bool stepper_is_running(void);
int32_t stepper_position(void);
bool stepper_set_position(int32_t pos);   /* 停止中のみ */
int32_t stepper_speed(void);              /* 符号付き step/s */
bool stepper_at_target(void);
/* パルス出力中の進行方向: POSITION が増える = +1、減る = -1、停止中 = 0（FLAGS bit0 の方向反転とは無関係。FR-HOME-03） */
int8_t stepper_motion_dir(void);

/* 起動後に一度でもパルス出力を開始した、または stepper_set_position で位置を再定義したら true
 * （位置センサの自動同期の可否判断用。FR-POS-09） */
bool stepper_touched(void);

void stepper_timer_isr(void);

#endif /* STEPPER_H */
