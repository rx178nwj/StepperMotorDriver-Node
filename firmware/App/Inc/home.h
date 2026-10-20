/*
 * home.h — 原点復帰の状態機械（ハードウェアに触れない純粋な関数）
 *
 * 要求仕様 3.12.3 節（FR-HOME-05〜07）。app.c が 1 ms 周期で home_step を呼び、返された動作を stepper へ適用する。
 *
 * 状態と遷移:
 *   IDLE        原点復帰を一度も始めていない
 *   SEEK        (1) 指定側へ探索速度で移動。指定側が作動 → ESTOP、SEEK_STOP へ。反対側が新たに作動 → ESTOP、FAILED
 *   SEEK_STOP   (2) パルスの停止を待つ。止まったら (3) を始める（反対方向へ戻り速度の RUN）、BACK へ
 *   BACK        (3) 指定側が非作動に確定 → ESTOP、BACK_STOP へ。1 回転分動いても作動のまま、または反対側が作動 → ESTOP、FAILED
 *   BACK_STOP   パルスの停止を待つ。止まったら (4) POSITION を 0 に（SET_ZERO）、DONE へ
 *   DONE        原点確定済み（INPUT_STATUS bit3）
 *   FAILED      失敗（INPUT_STATUS bit4）
 *   SEEK・BACK で予期せずパルスが止まったとき（リミット停止・外部の停止など）も FAILED。
 *   STOP・ESTOP・DISABLE・通信タイムアウト・FAULT は、各経路が自分の停止を行ったうえで home_abort を呼ぶ（FAILED）。
 */
#ifndef HOME_H
#define HOME_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    HOME_IDLE = 0,
    HOME_SEEK,
    HOME_SEEK_STOP,
    HOME_BACK,
    HOME_BACK_STOP,
    HOME_DONE,
    HOME_FAILED,
} home_state_t;

typedef enum {
    HOME_ACT_NONE = 0,
    HOME_ACT_RUN,        /* stepper_run(speed)。速度の符号が POSITION の増減方向 */
    HOME_ACT_ESTOP,      /* stepper_estop() */
    HOME_ACT_SET_ZERO,   /* stepper_set_position(0) */
} home_action_t;

typedef struct {
    home_action_t action;
    int32_t speed;       /* HOME_ACT_RUN のとき [step/s]（符号付き） */
} home_out_t;

/* 開始時に決まる値 */
typedef struct {
    uint32_t home_speed;     /* HOME_SPEED（0x5A） */
    uint32_t max_speed;      /* MAX_SPEED。探索速度は home_speed をこれに制限する */
    uint32_t start_speed;    /* START_SPEED。戻り速度は start_speed を探索速度（制限後の home_speed）に制限する */
    uint32_t limit_steps;    /* 戻りで動いてよい上限 = STEPS_PER_REV × MICROSTEP */
} home_params_t;

/* 毎回の入力 */
typedef struct {
    bool home1;              /* HOME1（＋ 側）の確定した作動状態 */
    bool home2;              /* HOME2（− 側）の確定した作動状態 */
    bool moving;             /* パルス出力中、または起動待ち（stepper_is_running() || stepper_has_pending()） */
    int32_t position;        /* stepper_position() */
} home_in_t;

typedef struct {
    home_state_t state;
    int8_t  side;            /* +1 = HOME1（＋ 側、HOME_POS）、-1 = HOME2（− 側、HOME_NEG） */
    int32_t seek_speed;      /* 探索速度（正） */
    int32_t back_speed;      /* 戻り速度（正） */
    uint32_t limit_steps;
    int32_t start_pos;       /* 戻りを始めた位置 */
    bool    opp_prev;        /* 前回の反対側の入力（SEEK で新たに作動したことの検出用） */
} home_t;

void home_init(home_t *h);

/* 原点復帰を始める。side: +1（HOME_POS）または -1（HOME_NEG）。受け付け条件（ENABLED・停止中・J10_MODE=2）は呼び出し側で確かめる。
 * 戻り値の動作を適用すること。開始時に指定側が作動していれば (3) から始める */
home_out_t home_start(home_t *h, int8_t side, const home_params_t *p, const home_in_t *in);

/* 1 ms 周期で呼ぶ。返された動作を適用すること */
home_out_t home_step(home_t *h, const home_in_t *in);

/* 外部の中止（STOP・ESTOP・DISABLE・通信タイムアウト・FAULT）。原点復帰中なら FAILED にする。停止はしない（呼び出し側の経路が行う） */
void home_abort(home_t *h);

bool home_is_active(const home_t *h);   /* SEEK〜BACK_STOP（INPUT_STATUS bit2、STATUS bit0 に含める） */
bool home_is_done(const home_t *h);     /* INPUT_STATUS bit3 */
bool home_is_failed(const home_t *h);   /* INPUT_STATUS bit4 */

#endif /* HOME_H */
