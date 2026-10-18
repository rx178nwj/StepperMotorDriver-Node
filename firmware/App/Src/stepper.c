#include "stepper.h"
#include "board.h"
#include "config.h"
#include "symtab.h"
#include <math.h>

#define OC1M_FORCE_INACTIVE  (4u << TIM_CCMR1_OC1M_Pos)
#define OC1M_PWM2            (7u << TIM_CCMR1_OC1M_Pos)  /* CNT >= CCR1 で High */

typedef enum { MODE_IDLE, MODE_POS, MODE_VEL } motion_mode_t;

typedef struct {
    uint16_t psc;
    uint16_t arr;
    uint16_t ccr;
} period_t;

static stepper_params_t prm = {
    .max_speed = DEF_MAX_SPEED,
    .accel = DEF_ACCEL,
    .decel = DEF_DECEL,
    .start_speed = DEF_START_SPEED,
    .invert_dir = false,
};

/* 指令（main から書き、ISR が読む） */
static volatile motion_mode_t mode;
static volatile int32_t target_pos;
static volatile float target_vel;

/* 実行状態 */
static volatile int32_t position;    /* 出力済みパルスで数えた位置 */
static volatile bool running;
static volatile int8_t dir = 1;
static volatile float cur_speed;     /* 報告用（符号付き） */
static volatile bool touched;        /* パルス出力を開始した、または位置を再定義した */

/* プランナ（パルス出力より 1 ステップ先行する） */
static float v;                      /* 直前に確定したステップの速度 */
static int32_t commit_pos;           /* 確定済みステップを全て出した後の位置 */

static void make_period(float speed, period_t *p)
{
    if (speed < 1.0f) speed = 1.0f;
    if (speed > (float)STEP_RATE_MAX) speed = (float)STEP_RATE_MAX;

    uint32_t ticks = (uint32_t)((float)STEP_TIMER_HZ / speed + 0.5f);
    uint32_t div = ticks / 65536u + 1u;          /* ticks/div < 65536 */
    uint32_t arr = ticks / div - 1u;
    uint32_t pulse_base = (STEP_TIMER_HZ / 1000000u) * STEP_PULSE_NS / 1000u;
    uint32_t pulse = (pulse_base + div - 1u) / div;
    if (pulse < 1u) pulse = 1u;

    p->psc = (uint16_t)(div - 1u);
    p->arr = (uint16_t)arr;
    p->ccr = (uint16_t)(arr + 1u - pulse);
}

static inline void load_preload(const period_t *p)
{
    TIM1->PSC = p->psc;
    TIM1->ARR = p->arr;
    TIM1->CCR1 = p->ccr;
}

/* 次のステップの速度を決めて確定する。0 を返したら停止 */
static float plan_next(void)
{
    const float vs = prm.start_speed;
    float vt;
    int64_t remaining = INT64_MAX;

    if (mode == MODE_POS) {
        remaining = ((int64_t)target_pos - commit_pos) * dir;
        if (remaining == 0) {
            v = 0.0f;
            return 0.0f;
        }
        vt = remaining > 0 ? prm.max_speed : 0.0f;   /* 目標が後ろ側: 減速して停止 */
    } else if (mode == MODE_VEL) {
        float tv = target_vel * (float)dir;
        vt = tv > 0.0f ? tv : 0.0f;
        if (vt > prm.max_speed) vt = prm.max_speed;
    } else {
        vt = 0.0f;
    }

    float vn;
    if (vt <= 0.0f) {
        float v2 = v * v - 2.0f * prm.decel;
        if (v <= vs || v2 < vs * vs) {
            v = 0.0f;
            return 0.0f;
        }
        vn = sqrtf(v2);
    } else if (v <= 0.0f) {
        vn = vs < vt ? vs : vt;
    } else {
        float stop_steps = (v * v - vs * vs) / (2.0f * prm.decel);
        if ((float)remaining <= stop_steps + 1.0f) {
            float v2 = v * v - 2.0f * prm.decel;
            vn = v2 > vs * vs ? sqrtf(v2) : vs;
        } else if (v < vt) {
            vn = sqrtf(v * v + 2.0f * prm.accel);
            if (vn > vt) vn = vt;
        } else if (v > vt) {
            float v2 = v * v - 2.0f * prm.decel;
            vn = v2 > vt * vt ? sqrtf(v2) : vt;
        } else {
            vn = v;
        }
    }

    v = vn;
    commit_pos += dir;
    return vn;
}

void stepper_init(void)
{
    __HAL_RCC_TIM1_CLK_ENABLE();

    TIM1->CR1 = TIM_CR1_ARPE | TIM_CR1_URS;   /* UG では割り込みを出さない */
    TIM1->CR2 = 0;
    TIM1->PSC = 0;
    TIM1->ARR = 0xFFFF;
    TIM1->CCR1 = 0xFFFF;
    TIM1->RCR = 0;
    TIM1->CCMR1 = OC1M_FORCE_INACTIVE | TIM_CCMR1_OC1PE;
    TIM1->CCER = TIM_CCER_CC1E;               /* アクティブ High */
    TIM1->BDTR = TIM_BDTR_MOE;
    TIM1->EGR = TIM_EGR_UG;
    TIM1->SR = 0;
    TIM1->DIER = TIM_DIER_UIE;

    HAL_NVIC_SetPriority(TIM1_UP_TIM16_IRQn, IRQ_PRIO_STEP, 0);
    HAL_NVIC_EnableIRQ(TIM1_UP_TIM16_IRQn);
}

void stepper_set_params(const stepper_params_t *p)
{
    stepper_params_t n = *p;
    if (n.max_speed > (float)STEP_RATE_MAX) n.max_speed = (float)STEP_RATE_MAX;
    if (n.max_speed < 1.0f) n.max_speed = 1.0f;
    if (n.start_speed < 1.0f) n.start_speed = 1.0f;
    if (n.start_speed > n.max_speed) n.start_speed = n.max_speed;
    if (n.accel < 1.0f) n.accel = 1.0f;
    if (n.decel < 1.0f) n.decel = 1.0f;

    uint32_t pm = __get_PRIMASK();
    __disable_irq();
    prm = n;
    __set_PRIMASK(pm);
}

void stepper_move_to(int32_t target)
{
    uint32_t pm = __get_PRIMASK();
    __disable_irq();
    target_pos = target;
    mode = MODE_POS;
    __set_PRIMASK(pm);
}

void stepper_move_by(int32_t delta)
{
    uint32_t pm = __get_PRIMASK();
    __disable_irq();
    /* 動作中の位置決めは目標からの相対、それ以外は現在位置からの相対 */
    int32_t base = (mode == MODE_POS) ? target_pos : position;
    target_pos = base + delta;
    mode = MODE_POS;
    __set_PRIMASK(pm);
}

void stepper_run(int32_t speed)
{
    uint32_t pm = __get_PRIMASK();
    __disable_irq();
    target_vel = (float)speed;
    mode = MODE_VEL;
    __set_PRIMASK(pm);
}

void stepper_stop(void)
{
    stepper_run(0);
}

void stepper_estop(void)
{
    uint32_t pm = __get_PRIMASK();
    __disable_irq();
    TIM1->CR1 &= ~TIM_CR1_CEN;
    TIM1->CCMR1 = (TIM1->CCMR1 & ~TIM_CCMR1_OC1M) | OC1M_FORCE_INACTIVE;
    if (TIM1->SR & TIM_SR_UIF) {
        position += dir;   /* 出し終えたが未カウントのパルス */
    }
    TIM1->SR = 0;
    TIM1->CNT = 0;
    running = false;
    cur_speed = 0.0f;
    v = 0.0f;
    target_vel = 0.0f;
    mode = MODE_IDLE;
    __set_PRIMASK(pm);
}

bool stepper_has_pending(void)
{
    if (running) return false;
    if (mode == MODE_POS) return target_pos != position;
    if (mode == MODE_VEL) return target_vel != 0.0f;
    return false;
}

bool stepper_service(void)
{
    uint32_t pm = __get_PRIMASK();
    __disable_irq();
    if (!stepper_has_pending()) {
        __set_PRIMASK(pm);
        return false;
    }

    int8_t d;
    if (mode == MODE_POS) {
        d = target_pos > position ? 1 : -1;
    } else {
        d = target_vel > 0.0f ? 1 : -1;
    }
    /* DIR はパルス停止中にだけ変える */
    board_pin_write(DRV_DIR_PORT, DRV_DIR_PIN, (d > 0) != prm.invert_dir);
    dir = d;
    v = 0.0f;
    commit_pos = position;

    float s1 = plan_next();
    if (s1 <= 0.0f) {
        __set_PRIMASK(pm);
        return false;
    }
    period_t p;
    make_period(s1, &p);
    TIM1->CR1 &= ~TIM_CR1_OPM;
    TIM1->CNT = 0;
    TIM1->CCMR1 = (TIM1->CCMR1 & ~TIM_CCMR1_OC1M) | OC1M_PWM2;
    load_preload(&p);
    TIM1->EGR = TIM_EGR_UG;     /* 1 周期目をシャドウへ */
    TIM1->SR = 0;

    float s2 = plan_next();
    if (s2 <= 0.0f) {
        TIM1->CR1 |= TIM_CR1_OPM;   /* 1 パルスで終了 */
    } else {
        make_period(s2, &p);
        load_preload(&p);           /* 2 周期目をプリロードへ */
    }
    cur_speed = s1 * (float)d;
    running = true;
    touched = true;
    /* 最初のパルスは 1 周期後（≥10 µs）なので DIR のセットアップ時間は確保される */
    TIM1->CR1 |= TIM_CR1_CEN;
    __set_PRIMASK(pm);
    return true;
}

void stepper_timer_isr(void)
{
    if (!(TIM1->SR & TIM_SR_UIF)) {
        return;
    }
    TIM1->SR = ~TIM_SR_UIF;
    position += dir;

    if (!(TIM1->CR1 & TIM_CR1_CEN)) {   /* OPM で停止した */
        running = false;
        cur_speed = 0.0f;
        return;
    }
    float s = plan_next();
    if (s <= 0.0f) {
        TIM1->CR1 |= TIM_CR1_OPM;       /* 今の周期のパルスで終わり */
    } else {
        period_t p;
        make_period(s, &p);
        load_preload(&p);
        cur_speed = s * (float)dir;
    }
}

bool stepper_is_running(void)
{
    return running;
}

int32_t stepper_position(void)
{
    return position;
}

bool stepper_set_position(int32_t pos)
{
    uint32_t pm = __get_PRIMASK();
    __disable_irq();
    bool ok = !running;
    if (ok) {
        touched = true;
        position = pos;
        target_pos = pos;
        target_vel = 0.0f;
        mode = MODE_IDLE;
    }
    __set_PRIMASK(pm);
    return ok;
}

int32_t stepper_speed(void)
{
    return running ? (int32_t)cur_speed : 0;
}

bool stepper_at_target(void)
{
    return mode == MODE_POS && !running && position == target_pos;
}

bool stepper_touched(void)
{
    return touched;
}

/* ---------------------------------------------------------------- USB 名前表（パルス生成） */

SYM_ENTRY(position, SYM_I32, 1);          /* 現在位置 [step] */
SYM_ENTRY(cur_speed, SYM_F32, 1);         /* 現在速度 [step/s]（符号付き） */
SYM_ENTRY(target_pos, SYM_I32, 1);        /* 目標位置 [step] */
SYM_ENTRY(target_vel, SYM_F32, 1);        /* 速度モードの目標速度 [step/s] */
SYM_ENTRY(mode, SYM_U8, 1);               /* 0 IDLE、1 POS、2 VEL */
SYM_ENTRY(running, SYM_U8, 1);
SYM_ENTRY(touched, SYM_U8, 1);            /* パルスを出した／位置を再定義した（自動同期の判断用） */
SYM_ENTRY(dir, SYM_I8, 1);
SYM_ENTRY_AS("plan_speed", v, SYM_F32, 1);   /* プランナが直前に確定した速度 */
SYM_ENTRY(commit_pos, SYM_I32, 1);        /* 確定済みステップを出し終えた後の位置 */
SYM_ENTRY(prm.max_speed, SYM_F32, 1);
SYM_ENTRY(prm.accel, SYM_F32, 1);
SYM_ENTRY(prm.decel, SYM_F32, 1);
SYM_ENTRY(prm.start_speed, SYM_F32, 1);
SYM_ENTRY(prm.invert_dir, SYM_U8, 1);
