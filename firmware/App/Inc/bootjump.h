/*
 * bootjump.h — ROM ブートローダ（USB DFU）への移行（要求仕様 3.13 節、FR-BL-03・FR-BL-04）
 *
 * IWDG は一度起動するとリセットでしか止まらないため、移行は次の 2 段で行う。
 *   1. 稼働中: bootjump_request() が移行フラグ（マジック値）を .noinit に書く → NVIC_SystemReset
 *   2. リセット直後（main の最初）: bootjump_check() がフラグを見て、一致すれば ROM へ飛ぶ
 */
#ifndef BOOTJUMP_H
#define BOOTJUMP_H

#include <stdbool.h>
#include <stdint.h>

/* main() の最初（HAL_Init・IWDG・クロック・周辺の初期化より前）で呼ぶ。
 * フラグがマジック値と完全に一致したときだけ、フラグを消して ROM ブートローダへ飛ぶ（戻らない）。
 * 一致しなければ何もしない */
void bootjump_check(void);

/* 移行フラグを書く（リセットは呼び出し側が行う）。割り込み禁止状態で呼ぶこと */
void bootjump_request(void);

/* ---- 単体試験用 ---- */
bool bootjump_flag_is_set(void);

#endif /* BOOTJUMP_H */
