/*
 * home.c（原点復帰の状態機械）のホスト単体テスト
 * （要求仕様 3.12.3 節 FR-HOME-05・06・07、試験 36）。期待値は requirements.md から決める。
 * home.c はハードウェアに触れない純粋な関数なので、入力（確定レベル・パルス出力中か・位置）を作って直接呼ぶ。
 */
#include "home.h"
#include <stdio.h>

static int g_checks;
static int g_fail;

#define CHECK(cond) \
    do { \
        g_checks++; \
        if (!(cond)) { \
            g_fail++; \
            printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        } \
    } while (0)

#define RUN(fn) \
    do { \
        int before_ = g_fail; \
        fn(); \
        printf("%s %s\n", (g_fail == before_) ? "PASS" : "FAIL", #fn); \
    } while (0)

/* 既定: HOME_SPEED 800、MAX_SPEED 3200、START_SPEED 200、1 回転 = 200 × 16 = 3200 step */
static home_params_t prm(void)
{
    home_params_t p = { .home_speed = 800, .max_speed = 3200, .start_speed = 200, .limit_steps = 3200 };
    return p;
}

static home_in_t in(bool h1, bool h2, bool moving, int32_t pos)
{
    home_in_t i = { .home1 = h1, .home2 = h2, .moving = moving, .position = pos };
    return i;
}

static bool is(home_out_t o, home_action_t a, int32_t speed)
{
    return o.action == a && (a != HOME_ACT_RUN || o.speed == speed);
}

/* ---- 正常完了 ---- */

/* HOME_POS（HOME1 側、＋ 方向）: 探索 ＋800 → HOME1 作動で ESTOP → 停止後に − 方向 200 で戻る → 非作動で ESTOP → 停止後に POSITION=0 */
static void test_normal_pos(void)
{
    home_t h;
    home_init(&h);
    CHECK(h.state == HOME_IDLE && !home_is_active(&h) && !home_is_done(&h) && !home_is_failed(&h));
    home_params_t p = prm();
    home_in_t i = in(false, false, false, 100);
    CHECK(is(home_start(&h, +1, &p, &i), HOME_ACT_RUN, 800));
    CHECK(h.state == HOME_SEEK && home_is_active(&h));

    /* 探索中（作動なし）は何もしない */
    for (int k = 0; k < 5; k++) {
        i = in(false, false, true, 100 + k);
        CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));
    }
    /* (2) 指定側（HOME1）が作動 → 即停止 */
    i = in(true, false, true, 500);
    CHECK(is(home_step(&h, &i), HOME_ACT_ESTOP, 0));
    CHECK(home_is_active(&h));
    /* パルスが止まるまで待つ */
    i = in(true, false, true, 500);
    CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));
    /* 止まったら (3) 反対方向へ START_SPEED */
    i = in(true, false, false, 501);
    CHECK(is(home_step(&h, &i), HOME_ACT_RUN, -200));
    CHECK(h.state == HOME_BACK && home_is_active(&h));
    /* 戻り中は作動のあいだ何もしない */
    i = in(true, false, true, 480);
    CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));
    /* 非作動に確定 → 即停止 */
    i = in(false, false, true, 470);
    CHECK(is(home_step(&h, &i), HOME_ACT_ESTOP, 0));
    CHECK(home_is_active(&h) && !home_is_done(&h));
    i = in(false, false, true, 470);
    CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));
    /* 停止したら (4) POSITION を 0 に */
    i = in(false, false, false, 470);
    CHECK(is(home_step(&h, &i), HOME_ACT_SET_ZERO, 0));
    CHECK(home_is_done(&h) && !home_is_active(&h) && !home_is_failed(&h));
    /* 完了後は何もしない */
    CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));
}

/* HOME_NEG（HOME2 側、− 方向）: 探索 −800 → HOME2 作動で ESTOP → ＋200 で戻る */
static void test_normal_neg(void)
{
    home_t h;
    home_init(&h);
    home_params_t p = prm();
    home_in_t i = in(false, false, false, 0);
    CHECK(is(home_start(&h, -1, &p, &i), HOME_ACT_RUN, -800));
    i = in(false, true, true, -300);
    CHECK(is(home_step(&h, &i), HOME_ACT_ESTOP, 0));
    i = in(false, true, false, -301);
    CHECK(is(home_step(&h, &i), HOME_ACT_RUN, 200));
    i = in(false, true, true, -250);
    CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));
    i = in(false, false, true, -240);
    CHECK(is(home_step(&h, &i), HOME_ACT_ESTOP, 0));
    i = in(false, false, false, -240);
    CHECK(is(home_step(&h, &i), HOME_ACT_SET_ZERO, 0));
    CHECK(home_is_done(&h));
}

/* 戻りの移動量が上限 − 1 のときに非作動になれば完了（上限は「動いても非作動にならない」場合） */
static void test_back_just_within_limit(void)
{
    home_t h;
    home_init(&h);
    home_params_t p = prm();
    home_in_t i = in(false, false, false, 0);
    (void)home_start(&h, +1, &p, &i);
    i = in(true, false, true, 1000);
    (void)home_step(&h, &i);
    i = in(true, false, false, 1000);
    (void)home_step(&h, &i);                 /* BACK 開始（start_pos = 1000） */
    i = in(true, false, true, 1000 - 3199);
    CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));
    i = in(false, false, true, 1000 - 3199); /* 3199 step で非作動 */
    CHECK(is(home_step(&h, &i), HOME_ACT_ESTOP, 0));
    CHECK(!home_is_failed(&h));
}

/* ---- 開始時に指定側が作動中 ---- */

static void test_start_while_active(void)
{
    home_t h;
    home_init(&h);
    home_params_t p = prm();
    /* HOME_POS で HOME1 がすでに作動: (3) から。反対方向（−）へ START_SPEED */
    home_in_t i = in(true, false, false, 700);
    CHECK(is(home_start(&h, +1, &p, &i), HOME_ACT_RUN, -200));
    CHECK(h.state == HOME_BACK);
    i = in(true, false, true, 650);
    CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));
    i = in(false, false, true, 640);
    CHECK(is(home_step(&h, &i), HOME_ACT_ESTOP, 0));
    i = in(false, false, false, 640);
    CHECK(is(home_step(&h, &i), HOME_ACT_SET_ZERO, 0));
    CHECK(home_is_done(&h));

    /* HOME_NEG で HOME2 がすでに作動: ＋ 方向へ戻る */
    home_init(&h);
    i = in(false, true, false, -5);
    CHECK(is(home_start(&h, -1, &p, &i), HOME_ACT_RUN, 200));
    i = in(false, false, true, 0);
    CHECK(is(home_step(&h, &i), HOME_ACT_ESTOP, 0));
    i = in(false, false, false, 0);
    CHECK(is(home_step(&h, &i), HOME_ACT_SET_ZERO, 0));
    CHECK(home_is_done(&h));

    /* 指定側と反対側が同時に作動（固着・配線の誤り）: 動かさず失敗 */
    home_init(&h);
    i = in(true, true, false, 0);
    CHECK(is(home_start(&h, +1, &p, &i), HOME_ACT_NONE, 0));
    CHECK(home_is_failed(&h) && !home_is_active(&h));
}

/* 開始時に反対側が作動中でも、そこから離れる方向（指定側へ）の探索は始める。新たに作動したときだけ失敗 */
static void test_start_with_opposite_active(void)
{
    home_t h;
    home_init(&h);
    home_params_t p = prm();
    home_in_t i = in(false, true, false, -10);   /* HOME2 作動中に HOME_POS（＋ へ探索） */
    CHECK(is(home_start(&h, +1, &p, &i), HOME_ACT_RUN, 800));
    i = in(false, true, true, 0);                /* まだ HOME2 の上: 失敗にしない */
    CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));
    i = in(false, false, true, 20);              /* 離れた */
    CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));
    i = in(false, true, true, 30);               /* 再び反対側が作動 → 失敗 */
    CHECK(is(home_step(&h, &i), HOME_ACT_ESTOP, 0));
    CHECK(home_is_failed(&h));
}

/* ---- 失敗 ---- */

/* (1) の途中で反対側が作動（スイッチの取り違え）→ 即停止して失敗 */
static void test_opposite_during_seek(void)
{
    home_t h;
    home_init(&h);
    home_params_t p = prm();
    home_in_t i = in(false, false, false, 0);
    (void)home_start(&h, +1, &p, &i);
    i = in(false, true, true, 50);               /* HOME_POS の探索中に HOME2 */
    CHECK(is(home_step(&h, &i), HOME_ACT_ESTOP, 0));
    CHECK(home_is_failed(&h) && !home_is_active(&h) && !home_is_done(&h));
    CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));

    home_init(&h);
    i = in(false, false, false, 0);
    (void)home_start(&h, -1, &p, &i);
    i = in(true, false, true, -50);              /* HOME_NEG の探索中に HOME1 */
    CHECK(is(home_step(&h, &i), HOME_ACT_ESTOP, 0));
    CHECK(home_is_failed(&h));
}

/* (3) で 1 回転分（limit_steps）動いても非作動にならない → 即停止して失敗（両方向） */
static void test_back_over_one_rev(void)
{
    for (int side = -1; side <= 1; side += 2) {
        home_t h;
        home_init(&h);
        home_params_t p = prm();
        home_in_t i = in(side > 0, side < 0, false, 1000);
        (void)home_start(&h, (int8_t)side, &p, &i);   /* すでに作動中: 位置 1000 から戻る */
        int32_t back = -side;                          /* 戻りの向き */
        i = in(side > 0, side < 0, true, 1000 + back * 3199);
        CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));
        CHECK(home_is_active(&h));
        i = in(side > 0, side < 0, true, 1000 + back * 3200);
        CHECK(is(home_step(&h, &i), HOME_ACT_ESTOP, 0));
        CHECK(home_is_failed(&h) && !home_is_done(&h));
    }
}

/* (3) で戻る向きの先（反対のリミット）が作動したら失敗（仕様に明記なし。安全のため） */
static void test_opposite_during_back(void)
{
    home_t h;
    home_init(&h);
    home_params_t p = prm();
    home_in_t i = in(true, false, false, 0);
    (void)home_start(&h, +1, &p, &i);
    i = in(true, true, true, -100);
    CHECK(is(home_step(&h, &i), HOME_ACT_ESTOP, 0));
    CHECK(home_is_failed(&h));
}

/* 予期せずパルスが止まった（リミット停止・外部の停止）→ 失敗（追加の停止動作はなし） */
static void test_unexpected_stop(void)
{
    home_t h;
    home_init(&h);
    home_params_t p = prm();
    home_in_t i = in(false, false, false, 0);
    (void)home_start(&h, +1, &p, &i);
    i = in(false, false, false, 10);
    CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));
    CHECK(home_is_failed(&h));

    home_init(&h);
    i = in(true, false, false, 0);
    (void)home_start(&h, +1, &p, &i);            /* (3) から */
    i = in(true, false, false, -10);
    CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));
    CHECK(home_is_failed(&h));
}

/* STOP・ESTOP・DISABLE・通信タイムアウト・FAULT による中止: home_abort（各経路が自分の停止を行ったうえで呼ぶ）。
 * どの段でも FAILED（bit2=0、bit4=1）になり、その後 RUN や SET_ZERO を出さない */
static void test_abort_in_each_stage(void)
{
    home_params_t p = prm();
    for (int stage = 0; stage < 4; stage++) {
        home_t h;
        home_init(&h);
        home_in_t i = in(false, false, false, 0);
        (void)home_start(&h, +1, &p, &i);                         /* SEEK */
        if (stage >= 1) {
            i = in(true, false, true, 100);
            (void)home_step(&h, &i);                              /* SEEK_STOP */
        }
        if (stage >= 2) {
            i = in(true, false, false, 100);
            (void)home_step(&h, &i);                              /* BACK */
        }
        if (stage >= 3) {
            i = in(false, false, true, 90);
            (void)home_step(&h, &i);                              /* BACK_STOP */
        }
        CHECK(home_is_active(&h));
        home_abort(&h);
        CHECK(!home_is_active(&h) && home_is_failed(&h) && !home_is_done(&h));
        i = in(false, false, false, 90);
        CHECK(is(home_step(&h, &i), HOME_ACT_NONE, 0));           /* 中止後は何も出さない */
        CHECK(home_is_failed(&h));
    }
}

/* 原点復帰中でなければ home_abort は何もしない（IDLE・DONE のまま） */
static void test_abort_when_not_active(void)
{
    home_t h;
    home_init(&h);
    home_abort(&h);
    CHECK(h.state == HOME_IDLE && !home_is_failed(&h));

    home_params_t p = prm();
    home_in_t i = in(true, false, false, 0);
    (void)home_start(&h, +1, &p, &i);
    i = in(false, false, true, -5);
    (void)home_step(&h, &i);
    i = in(false, false, false, -5);
    (void)home_step(&h, &i);
    CHECK(home_is_done(&h));
    home_abort(&h);
    CHECK(home_is_done(&h) && !home_is_failed(&h));   /* 完了後の STOP などで bit3 を消さない */
}

/* 新しい原点復帰を始めると完了・失敗のフラグが消える（bit3・bit4 を 0） */
static void test_restart_clears_flags(void)
{
    home_t h;
    home_init(&h);
    home_params_t p = prm();
    home_in_t i = in(false, false, false, 0);
    (void)home_start(&h, +1, &p, &i);
    home_abort(&h);
    CHECK(home_is_failed(&h));
    (void)home_start(&h, +1, &p, &i);
    CHECK(!home_is_failed(&h) && !home_is_done(&h) && home_is_active(&h));

    /* 完了後 → 再開始で完了フラグが消える */
    home_init(&h);
    i = in(true, false, false, 0);
    (void)home_start(&h, +1, &p, &i);
    i = in(false, false, true, 0);
    (void)home_step(&h, &i);
    i = in(false, false, false, 0);
    (void)home_step(&h, &i);
    CHECK(home_is_done(&h));
    i = in(false, false, false, 0);
    (void)home_start(&h, -1, &p, &i);
    CHECK(!home_is_done(&h) && home_is_active(&h));
}

/* ---- 速度の制限 ---- */

/* HOME_SPEED が最高速度を超えるときは最高速度に制限（探索速度） */
static void test_home_speed_limited_by_max(void)
{
    home_t h;
    home_init(&h);
    home_params_t p = prm();
    p.home_speed = 5000;
    p.max_speed = 3200;
    home_in_t i = in(false, false, false, 0);
    CHECK(is(home_start(&h, +1, &p, &i), HOME_ACT_RUN, 3200));
    home_init(&h);
    CHECK(is(home_start(&h, -1, &p, &i), HOME_ACT_RUN, -3200));

    /* HOME_SPEED が最高速度以下ならそのまま */
    home_init(&h);
    p.home_speed = 3200;
    CHECK(is(home_start(&h, +1, &p, &i), HOME_ACT_RUN, 3200));
    home_init(&h);
    p.home_speed = 1;
    CHECK(is(home_start(&h, +1, &p, &i), HOME_ACT_RUN, 1));
}

/* START_SPEED が HOME_SPEED を超えるときは HOME_SPEED に制限（戻り速度） */
static void test_back_speed_limited_by_home_speed(void)
{
    home_t h;
    home_init(&h);
    home_params_t p = prm();
    p.home_speed = 800;
    p.start_speed = 1000;
    home_in_t i = in(true, false, false, 0);      /* すでに作動中 → (3) から */
    CHECK(is(home_start(&h, +1, &p, &i), HOME_ACT_RUN, -800));

    /* START_SPEED が HOME_SPEED 以下ならそのまま */
    home_init(&h);
    p.start_speed = 800;
    CHECK(is(home_start(&h, +1, &p, &i), HOME_ACT_RUN, -800));
    home_init(&h);
    p.start_speed = 799;
    i = in(false, true, false, 0);
    CHECK(is(home_start(&h, -1, &p, &i), HOME_ACT_RUN, 799));

    /* 探索から戻りへ進む経路でも同じ（HOME_SPEED 800、START_SPEED 1000 → 戻り 800） */
    home_init(&h);
    p.start_speed = 1000;
    i = in(false, false, false, 0);
    (void)home_start(&h, +1, &p, &i);
    i = in(true, false, true, 40);
    (void)home_step(&h, &i);
    i = in(true, false, false, 40);
    CHECK(is(home_step(&h, &i), HOME_ACT_RUN, -800));

    /* HOME_SPEED が最高速度を超えて制限された場合、戻りも制限後の探索速度（最高速度）を超えない */
    home_init(&h);
    p.home_speed = 5000;
    p.max_speed = 3200;
    p.start_speed = 4000;
    i = in(true, false, false, 0);
    CHECK(is(home_start(&h, +1, &p, &i), HOME_ACT_RUN, -3200));
}

int main(void)
{
    printf("test_home: home.c state machine tests\n");
    RUN(test_normal_pos);
    RUN(test_normal_neg);
    RUN(test_back_just_within_limit);
    RUN(test_start_while_active);
    RUN(test_start_with_opposite_active);
    RUN(test_opposite_during_seek);
    RUN(test_back_over_one_rev);
    RUN(test_opposite_during_back);
    RUN(test_unexpected_stop);
    RUN(test_abort_in_each_stage);
    RUN(test_abort_when_not_active);
    RUN(test_restart_clears_flags);
    RUN(test_home_speed_limited_by_max);
    RUN(test_back_speed_limited_by_home_speed);

    printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
