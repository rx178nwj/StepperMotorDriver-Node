#include "home.h"
#include "config.h"

static const home_out_t OUT_NONE = { HOME_ACT_NONE, 0 };

static inline home_out_t out_run(int32_t speed)
{
    home_out_t o = { HOME_ACT_RUN, speed };
    return o;
}

static inline home_out_t out_estop(void)
{
    home_out_t o = { HOME_ACT_ESTOP, 0 };
    return o;
}

static inline bool side_active(const home_t *h, const home_in_t *in)
{
    return h->side > 0 ? in->home1 : in->home2;
}

static inline bool opp_active(const home_t *h, const home_in_t *in)
{
    return h->side > 0 ? in->home2 : in->home1;
}

static uint32_t abs_moved(int32_t from, int32_t to)
{
    int64_t d = (int64_t)to - (int64_t)from;
    return (uint32_t)(d < 0 ? -d : d);
}

static int32_t clamp_speed(uint32_t v)
{
    if (v < 1u) v = 1u;
    if (v > STEP_RATE_MAX) v = STEP_RATE_MAX;   /* 設定の範囲内なら効かない保険 */
    return (int32_t)v;
}

void home_init(home_t *h)
{
    *h = (home_t){0};
    h->state = HOME_IDLE;
}

home_out_t home_start(home_t *h, int8_t side, const home_params_t *p, const home_in_t *in)
{
    h->side = side > 0 ? 1 : -1;
    /* 探索速度 = HOME_SPEED（最高速度を超えるなら最高速度）。戻り速度 = START_SPEED（探索速度を超えるなら探索速度） */
    uint32_t seek = p->home_speed < p->max_speed ? p->home_speed : p->max_speed;
    uint32_t back = p->start_speed < seek ? p->start_speed : seek;
    h->seek_speed = clamp_speed(seek);
    h->back_speed = clamp_speed(back);
    h->limit_steps = p->limit_steps;
    h->opp_prev = opp_active(h, in);

    if (side_active(h, in)) {
        if (opp_active(h, in)) {
            h->state = HOME_FAILED;   /* 両側が作動（配線の誤り・固着）。動かさない */
            return OUT_NONE;
        }
        h->state = HOME_BACK;         /* すでに指定側が作動中: (3) から */
        h->start_pos = in->position;
        return out_run(-(int32_t)h->side * h->back_speed);
    }
    h->state = HOME_SEEK;
    return out_run((int32_t)h->side * h->seek_speed);
}

home_out_t home_step(home_t *h, const home_in_t *in)
{
    switch (h->state) {
    case HOME_SEEK: {
        if (!in->moving) {
            h->state = HOME_FAILED;   /* 予期せず止まった */
            return OUT_NONE;
        }
        bool opp = opp_active(h, in);
        if (opp && !h->opp_prev) {
            h->state = HOME_FAILED;   /* スイッチの取り違え（新たに反対側が作動） */
            return out_estop();
        }
        h->opp_prev = opp;
        if (side_active(h, in)) {
            h->state = HOME_SEEK_STOP;   /* (2) 即停止 */
            return out_estop();
        }
        return OUT_NONE;
    }
    case HOME_SEEK_STOP:
        if (in->moving) {
            return OUT_NONE;   /* パルスの停止を待つ */
        }
        h->state = HOME_BACK;   /* (3) 反対方向へ */
        h->start_pos = in->position;
        return out_run(-(int32_t)h->side * h->back_speed);
    case HOME_BACK:
        if (!in->moving) {
            h->state = HOME_FAILED;
            return OUT_NONE;
        }
        if (!side_active(h, in)) {
            h->state = HOME_BACK_STOP;   /* 非作動の確定で即停止 */
            return out_estop();
        }
        if (opp_active(h, in) || abs_moved(h->start_pos, in->position) >= h->limit_steps) {
            h->state = HOME_FAILED;   /* 反対側のリミット到達、または 1 回転分動いても非作動にならない */
            return out_estop();
        }
        return OUT_NONE;
    case HOME_BACK_STOP:
        if (in->moving) {
            return OUT_NONE;
        }
        h->state = HOME_DONE;   /* (4) POSITION を 0 にして完了 */
        {
            home_out_t o = { HOME_ACT_SET_ZERO, 0 };
            return o;
        }
    default:
        return OUT_NONE;
    }
}

void home_abort(home_t *h)
{
    if (home_is_active(h)) {
        h->state = HOME_FAILED;
    }
}

bool home_is_active(const home_t *h)
{
    return h->state >= HOME_SEEK && h->state <= HOME_BACK_STOP;
}

bool home_is_done(const home_t *h)
{
    return h->state == HOME_DONE;
}

bool home_is_failed(const home_t *h)
{
    return h->state == HOME_FAILED;
}
