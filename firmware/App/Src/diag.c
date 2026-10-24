/*
 * 診断値（デバッグ用）。仕様は firmware/docs/usb_symbols.md の「診断」。
 * ハードウェアには触れない。レジスタや HAL の値は呼び出し側（main.c・app_main.c）が渡す。
 */
#include "diag.h"
#include "stm32l4xx_hal.h"   /* uwTick の extern */
#include "symtab.h"

#define STACK_PATTERN  0xA5A5A5A5u
#define LOOP_WINDOW_MS 1000u

/* ---- リセット要因 ---- */
static uint32_t reset_csr;

void diag_set_reset_csr(uint32_t csr)
{
    reset_csr = csr;
}

/* ---- メインループ周期 ---- */
static struct {
    uint32_t max_us;      /* diag_init 以降の最大周期 [µs] */
    uint32_t max_1s_us;   /* 直前に完了した 1 秒窓の最大周期 [µs] */
    uint32_t rate_hz;     /* 直前の 1 秒窓のループ回数 */
} loop;

static uint32_t cycles_per_us = 1u;
static bool     loop_started;       /* 初回の mark を受けたか */
static uint32_t loop_last_cyc;
static uint32_t loop_max_cyc;       /* diag_init 以降の最大周期 [cycles] */
static uint32_t win_start_ms;
static uint32_t win_count;
static uint32_t win_max_cyc;

void diag_init(uint32_t cycles_per_us_)
{
    cycles_per_us = (cycles_per_us_ != 0u) ? cycles_per_us_ : 1u;
    loop_started = false;
    loop_max_cyc = 0u;
    win_count = 0u;
    win_max_cyc = 0u;
    loop.max_us = 0u;
    loop.max_1s_us = 0u;
    loop.rate_hz = 0u;
}

void diag_loop_mark(uint32_t cyc, uint32_t tick_ms)
{
    if (!loop_started) {
        loop_started = true;
        win_start_ms = tick_ms;
    } else {
        uint32_t d = cyc - loop_last_cyc;   /* CYCCNT のラップは符号なし差分で吸収 */
        if (d > win_max_cyc) {
            win_max_cyc = d;
        }
        if (d > loop_max_cyc) {
            loop_max_cyc = d;               /* 更新は最大値が伸びたときだけなので除算は稀 */
            loop.max_us = d / cycles_per_us;
        }
    }
    loop_last_cyc = cyc;
    win_count++;

    if ((tick_ms - win_start_ms) >= LOOP_WINDOW_MS) {   /* 窓の確定（除算はここだけ） */
        loop.max_1s_us = win_max_cyc / cycles_per_us;
        loop.rate_hz = win_count;
        win_start_ms = tick_ms;
        win_count = 0u;
        win_max_cyc = 0u;
    }
}

/* ---- スタック使用量の最大値（塗りつぶし方式） ---- */
static struct {
    uint32_t used_max;   /* top - ウォーターマーク [バイト] */
    uint32_t size;       /* top - bottom [バイト] */
} stack;

static volatile uint32_t *stk_bottom;
static volatile uint32_t *stk_top;
static volatile uint32_t *stk_mark;     /* これまでの最低到達位置（ここより下は塗り残し） */
static volatile uint32_t *stk_cursor;

void diag_stack_init(uint32_t *bottom, uint32_t *limit, uint32_t *top)
{
    uintptr_t b = ((uintptr_t)bottom + 3u) & ~(uintptr_t)3u;
    uintptr_t l = (uintptr_t)limit & ~(uintptr_t)3u;
    if (b >= l || l > (uintptr_t)top) {
        return;
    }
    volatile uint32_t *p = (volatile uint32_t *)b;
    for (; p < (volatile uint32_t *)l; p++) {
        *p = STACK_PATTERN;
    }
    stk_bottom = (volatile uint32_t *)b;
    stk_top = (volatile uint32_t *)top;
    stk_mark = (volatile uint32_t *)l;
    stk_cursor = stk_bottom;
    stack.size = (uint32_t)((uintptr_t)top - b);
    stack.used_max = (uint32_t)((uintptr_t)top - l);
}

void diag_stack_scan(uint32_t words)
{
    if (stk_bottom == 0) {
        return;
    }
    while (words-- > 0u) {
        if (stk_cursor >= stk_mark) {            /* ウォーターマークまで塗りが残っていた: 先頭から再走査 */
            stk_cursor = stk_bottom;
            return;
        }
        if (*stk_cursor != STACK_PATTERN) {      /* 塗りが崩れた: これが新しい最低到達位置 */
            stk_mark = stk_cursor;
            stack.used_max = (uint32_t)((uintptr_t)stk_top - (uintptr_t)stk_mark);
            stk_cursor = stk_bottom;
            return;
        }
        stk_cursor++;
    }
}

SYM_ENTRY(uwTick, SYM_U32, 1);
SYM_ENTRY(reset_csr, SYM_U32, 1);
SYM_ENTRY(loop.max_us, SYM_U32, 1);
SYM_ENTRY(loop.max_1s_us, SYM_U32, 1);
SYM_ENTRY(loop.rate_hz, SYM_U32, 1);
SYM_ENTRY(stack.used_max, SYM_U32, 1);
SYM_ENTRY(stack.size, SYM_U32, 1);
