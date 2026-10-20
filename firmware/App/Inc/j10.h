/*
 * j10.h — J10.3/J10.4（PA15/PA1）の機能切り替え：未使用・ENC（TIM2 エンコーダ）・HOME（原点・リミット入力）
 *
 * 要求仕様 3.12 節（FR-J10-01〜04、FR-ENC-01〜04、FR-HOME-01〜02）。CubeMX の MX_*_Init は使わず、
 * GPIOA と TIM2 のレジスタを直接設定する（position.c の PA7 と同じ方針）。割り込みは使わない。
 */
#ifndef J10_H
#define J10_H

#include <stdbool.h>
#include <stdint.h>

#define J10_MODE_NONE  0u   /* 未使用: PA15/PA1 はアナログ入力 */
#define J10_MODE_ENC   1u   /* A/B エンコーダ（TIM2 エンコーダモード） */
#define J10_MODE_HOME  2u   /* 原点・リミットスイッチ入力（GPIO 入力、内部プルアップ） */

/* ソフトウェアの状態を初期化する。端子はリセット直後のまま（PA15 は JTDI）なので、次の j10_set_mode は必ず端子を設定する */
void j10_init(void);

/* 機能を切り替える（mode: 0〜2。範囲外は無視）。同じモードなら何もしない。
 * 切り替えるときは一度 PA15/PA1 をアナログ（プルなし）に戻し、TIM2 を止めてから新しい機能を設定する（FR-J10-02）。
 * モード 1 へ切り替えたとき ENC_COUNT は 0 から数える（FR-ENC-03） */
void j10_set_mode(uint8_t mode);
uint8_t j10_mode(void);

/* ENC_COUNT: モード 1 なら TIM2->CNT を符号付き 32bit で返す。それ以外は 0 */
int32_t j10_enc_count(void);

/* 1 ms 周期で呼ぶ。モード 2 のとき PA15（HOME1）・PA1（HOME2）を読み、HOME_CONFIRM_SAMPLES 回連続で同じなら確定する。
 * active_high: FLAGS bit4（false = アクティブ Low、true = アクティブ High。両入力共通）。他のモードでは確定状態を非作動にする */
void j10_poll_1ms(bool active_high);

/* 確定した作動状態（モード 2 以外では false） */
bool j10_home1_active(void);   /* HOME1（PA15）= ＋ 側 */
bool j10_home2_active(void);   /* HOME2（PA1）= − 側 */

#endif /* J10_H */
