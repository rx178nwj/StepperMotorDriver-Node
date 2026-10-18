/*
 * position.h — 出力軸の位置センサ AS5600（I2C1、J9）と PA7（J10.5）の機能切り替え
 *
 * 要求仕様 3.10 節（FR-POS-01〜11）。センサ値はモータ制御（脱調検出・閉ループ補正）には使わない。
 *
 * I2C は割り込みを使わず、main ループ内でレジスタを直接操作して 1 トランザクション
 * POS_I2C_TIMEOUT_US（DWT で計時）以内に終わらせる。TIM1・EXTI・CAN・USB の割り込みは常に割り込める。
 */
#ifndef POSITION_H
#define POSITION_H

#include <stdbool.h>
#include <stdint.h>

/* I2C1 と PA7 を初期化する。PA7 は board_init で設定したアナログ入力のまま */
void position_init(void);

/* 10 ms 周期で呼ぶ。センサ読み出し（無効の間は 1 秒ごと）と、PA7_MODE=2 のときの Z 入力の取り込み */
void position_poll_10ms(uint32_t now_ms);

/* センサが有効（3 回連続失敗していない、かつ磁石を検出） */
bool position_valid(void);
/* RAW ANGLE（0〜4095）。無効時は 0xFFFF */
uint16_t position_raw(void);
/* SENSOR_STATUS の bit0（有効）、bit1（MH）、bit2（ML）、bit3（応答なし）、bit5（Z 入力）。bit4 は含まない */
uint8_t position_status_bits(void);

/* PA7 の機能を切り替える（mode: 0 AD_IN、1 DIR、2 Z）。dir_level は mode=1 のときの出力レベル。
 * モードが変わるときは一度アナログに戻してから設定する。mode が同じなら DIR のレベルだけ更新する */
void position_pa7_set(uint8_t mode, bool dir_level);

/* ---- 純粋な計算（ホストでも検証できる） ---- */

/* a = (RAW − offset) mod 4096 */
uint16_t position_axis_count(uint16_t raw, uint16_t offset);
/* SHAFT_ANGLE [0.01°] = a × 36000 / 4096（四捨五入）。raw が 0〜4095 でなければ 0xFFFF */
uint16_t position_shaft_angle(uint16_t raw, uint16_t offset);
/* SYNC_POSITION: a × gear_ratio × steps_per_rev × microstep / 4096（四捨五入、64bit で計算） */
int32_t position_sync_steps(uint16_t a, uint16_t gear_ratio, uint16_t steps_per_rev, uint8_t microstep);

#endif /* POSITION_H */
