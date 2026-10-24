/*
 * diag.h — デバッグ用の診断値（USB 名前表で読む）。ハードウェアには触れない純粋なロジック。
 *   リセット要因（RCC->CSR の生値）、メインループ周期、スタック使用量の最大値。
 *   稼働時間は HAL の uwTick をそのまま名前表に登録している（diag.c）。
 */
#ifndef DIAG_H
#define DIAG_H

#include <stdint.h>

/* リセット要因。main() で RCC->CSR を読んだ直後に渡す（生値のまま保持する） */
void diag_set_reset_csr(uint32_t csr);

/* メインループ周期の計測を初期化する。cycles_per_us = CPU クロック[MHz]（DWT->CYCCNT の 1 µs あたりのカウント数） */
void diag_init(uint32_t cycles_per_us);

/* app_main_loop の入口で毎周呼ぶ。cyc = DWT->CYCCNT、tick_ms = HAL_GetTick()。初回は計測しない */
void diag_loop_mark(uint32_t cyc, uint32_t tick_ms);

/*
 * スタック使用量の計測を初期化する。[bottom, limit) をパターンで塗る（bottom は 4 バイト境界へ切り上げ、
 * limit は切り下げ）。top はスタックの上端（_estack）。limit より上（現在使用中の部分）は塗らない
 */
void diag_stack_init(uint32_t *bottom, uint32_t *limit, uint32_t *top);

/* 塗りつぶしの残りを 1 回あたり最大 words 語だけ検査する。未初期化なら何もしない */
void diag_stack_scan(uint32_t words);

#endif /* DIAG_H */
