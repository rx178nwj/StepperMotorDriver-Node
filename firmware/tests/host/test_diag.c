/*
 * diag.c のホスト単体テスト。診断値の static 変数を直接見るため diag.c を #include する。
 * uwTick の実体はここで定義する（実機では HAL が持つ）。
 */
#include "../../App/Src/diag.c"
#include <stdio.h>

volatile uint32_t uwTick;

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

static void test_reset_csr(void)
{
    CHECK(reset_csr == 0u);
    diag_set_reset_csr(0x24000000u);
    CHECK(reset_csr == 0x24000000u);
}

/* 初回は計測しない。2 回目から差分を最大値に反映（80 cycles/µs） */
static void test_loop_basic(void)
{
    diag_init(80u);
    diag_loop_mark(1000u, 0u);
    CHECK(loop.max_us == 0u);
    diag_loop_mark(1000u + 800u, 0u);        /* 10 µs */
    CHECK(loop.max_us == 10u);
    diag_loop_mark(1800u + 4000u, 0u);       /* 50 µs */
    CHECK(loop.max_us == 50u);
    diag_loop_mark(5800u + 160u, 0u);        /* 2 µs: 最大は変わらない */
    CHECK(loop.max_us == 50u);
}

static void test_loop_wrap(void)
{
    diag_init(80u);
    diag_loop_mark(0xFFFFFF00u, 0u);
    diag_loop_mark(0x00000100u, 0u);         /* 0x200 = 512 cycles = 6 µs */
    CHECK(loop.max_us == 6u);
}

/* 1 秒窓: 確定で max_1s_us・rate_hz が更新され、次の窓でリセットされる。max_us は残る */
static void test_loop_window(void)
{
    uint32_t cyc = 0u;
    diag_init(10u);
    diag_loop_mark(cyc, 100u);               /* 窓の開始 */
    cyc += 1000u; diag_loop_mark(cyc, 400u); /* 100 µs */
    cyc += 5000u; diag_loop_mark(cyc, 900u); /* 500 µs */
    CHECK(loop.rate_hz == 0u && loop.max_1s_us == 0u);
    cyc += 200u;  diag_loop_mark(cyc, 1100u);/* 20 µs、tick 差 1000 で確定。計 4 回 */
    CHECK(loop.rate_hz == 4u);
    CHECK(loop.max_1s_us == 500u);
    CHECK(loop.max_us == 500u);
    /* 次の窓: 小さい周期だけ。確定後に max_1s_us は新しい窓の値になり、max_us は残る */
    cyc += 100u;  diag_loop_mark(cyc, 1500u);
    cyc += 300u;  diag_loop_mark(cyc, 2099u);
    CHECK(loop.rate_hz == 4u && loop.max_1s_us == 500u);
    cyc += 200u;  diag_loop_mark(cyc, 2100u);/* 計 3 回、最大 30 µs */
    CHECK(loop.rate_hz == 3u);
    CHECK(loop.max_1s_us == 30u);
    CHECK(loop.max_us == 500u);
}

/* tick のラップをまたいでも窓が確定する */
static void test_loop_tick_wrap(void)
{
    diag_init(1u);
    diag_loop_mark(0u, 0xFFFFFE00u);
    diag_loop_mark(10u, 0xFFFFFFFFu);
    CHECK(loop.rate_hz == 0u);
    diag_loop_mark(20u, 0x000001E8u);        /* 差 0x3E8 = 1000 */
    CHECK(loop.rate_hz == 3u);
    CHECK(loop.max_1s_us == 10u);
}

#define STK_WORDS 256u
static uint32_t stk[STK_WORDS];

static void scan_all(void)
{
    for (int i = 0; i < 1000; i++) {
        diag_stack_scan(64u);
    }
}

/* limit = 配列の末尾から 16 語手前、top = 配列の末尾 */
static void stack_setup(void)
{
    for (uint32_t i = 0; i < STK_WORDS; i++) {
        stk[i] = 0x12345678u;
    }
    diag_stack_init(&stk[0], &stk[STK_WORDS - 16u], &stk[STK_WORDS]);
}

static void test_stack_init(void)
{
    stack_setup();
    CHECK(stack.size == STK_WORDS * 4u);
    CHECK(stack.used_max == 16u * 4u);       /* 初期値は limit から top まで */
    CHECK(stk[0] == STACK_PATTERN);
    CHECK(stk[STK_WORDS - 17u] == STACK_PATTERN);
    CHECK(stk[STK_WORDS - 16u] == 0x12345678u);   /* limit 以上は塗らない */
}

static void test_stack_scan(void)
{
    stack_setup();
    stk[STK_WORDS - 40u] = 0u;               /* 使われた */
    scan_all();
    CHECK(stack.used_max == 40u * 4u);
    stk[STK_WORDS - 100u] = 0u;              /* さらに深く */
    stk[STK_WORDS - 90u] = 0u;
    scan_all();
    CHECK(stack.used_max == 100u * 4u);
    stk[STK_WORDS - 30u] = 0u;               /* 浅い書き込みでは減らない */
    scan_all();
    CHECK(stack.used_max == 100u * 4u);
}

/* words 制限で 1 回では届かない: 途中では古い値、続けて呼ぶと正しい値 */
static void test_stack_incremental(void)
{
    stack_setup();
    stk[STK_WORDS - 200u] = 0u;              /* 先頭から 56 語目 */
    diag_stack_scan(10u);
    CHECK(stack.used_max == 16u * 4u);
    diag_stack_scan(10u);
    CHECK(stack.used_max == 16u * 4u);
    scan_all();
    CHECK(stack.used_max == 200u * 4u);
}

/* limit 手前まで全て無傷なら、走査が一巡してもウォーターマークは変わらない */
static void test_stack_untouched(void)
{
    stack_setup();
    scan_all();
    CHECK(stack.used_max == 16u * 4u);
}

/* 未初期化の scan は何もしない（状態を作る前に別のプロセスで実行する想定のため、状態を明示的に戻す） */
static void test_stack_uninit(void)
{
    stk_bottom = 0;
    stack.used_max = 0u;
    stack.size = 0u;
    diag_stack_scan(64u);
    CHECK(stack.used_max == 0u && stack.size == 0u);
}

/* 不正な範囲（bottom >= limit）は初期化しない */
static void test_stack_bad_range(void)
{
    stk_bottom = 0;
    diag_stack_init(&stk[10], &stk[10], &stk[20]);
    CHECK(stk_bottom == 0);
    diag_stack_scan(8u);
}

int main(void)
{
    RUN(test_reset_csr);
    RUN(test_loop_basic);
    RUN(test_loop_wrap);
    RUN(test_loop_window);
    RUN(test_loop_tick_wrap);
    RUN(test_stack_init);
    RUN(test_stack_scan);
    RUN(test_stack_incremental);
    RUN(test_stack_untouched);
    RUN(test_stack_uninit);
    RUN(test_stack_bad_range);
    printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
