/*
 * test_poweron.h — 電源投入時の安全状態試験モード（docs/requirements.md 6章 試験 1）
 *
 * CMake オプション FW_TEST_POWERON=ON のときだけ有効（プリセット TestPowerOn / TestPowerOnHold）。
 * 通常ビルドでは全て空の inline 関数になり、起動シーケンスは変わらない。
 * 手順は docs/test_poweron_safe_state.md。
 */
#ifndef TEST_POWERON_H
#define TEST_POWERON_H

#include <stdint.h>

#ifdef FW_TEST_POWERON
void test_poweron_preinit(void);       /* マーカ 1 パルス。HOLD 版はリセット状態のまま待つ */
void test_poweron_mark(uint32_t n);    /* LED_MCU(PB0) に n 発のパルス */
void test_poweron_hold(void);          /* 安全状態を監視し続ける。戻らない */
#else
static inline void test_poweron_preinit(void) {}
static inline void test_poweron_mark(uint32_t n) { (void)n; }
static inline void test_poweron_hold(void) {}
#endif

#endif /* TEST_POWERON_H */
