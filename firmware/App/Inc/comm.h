/*
 * comm.h — 上位通信（CAN1 / bxCAN、CAN 2.0A）
 *
 * 要求仕様 3.8 節。ID 割り当ては config.h の CAN_ID_*。
 *   書き込み要求 [0x01][REG][DATA 1〜6 B]  → 応答 [0x81][REG][CMD_RESULT]
 *   読み出し要求 [0x02][REG][LEN 1〜6]     → 応答 [0x82][REG][DATA LEN B]
 *   不正な要求                              → [0xFF][REG][2]
 *   ブロードキャスト [COMMAND]（DLC 1）      → 応答なし
 *
 * 構成:
 *   - CAN 層（comm_init / comm_poll / 割り込み）: 受信は RX0 割り込みでリングバッファに積むだけ。
 *     解析と送信は main ループの comm_poll で行う。
 *   - 要求処理（comm_process_request）: トランスポート非依存。入力フレーム → 返すべきフレームを
 *     作るだけで、送信は呼び出し側が行う（USB 代行からも同じ関数を使う）。
 */
#ifndef COMM_H
#define COMM_H

#include <stdbool.h>
#include <stdint.h>

#define REG_MAP_SIZE      0x59u   /* レジスタマップの末尾（0x58 PA7_MODE まで）。これ以降は読むと 0xFF */
#define COMM_READ_MAX     6u      /* 1 フレームで読み書きできる最大バイト数 */
#define COMM_RESP_MAX     1u      /* 1 要求あたりの最大応答フレーム数 */

#define COMM_OP_WRITE     0x01u
#define COMM_OP_READ      0x02u
#define COMM_OP_WRITE_RSP 0x81u
#define COMM_OP_READ_RSP  0x82u
#define COMM_OP_ERROR     0xFFu

/* CAN フレーム（標準 ID、データフレームのみ） */
typedef struct {
    uint16_t id;
    uint8_t dlc;
    uint8_t data[8];
} comm_frame_t;

/* CAN 統計（USB の名前表で公開する。割り込みから更新されるので読むときは volatile 扱い） */
typedef struct {
    uint32_t rx_frames;        /* 受信したフレーム数（フィルタ通過後） */
    uint32_t rx_dropped;       /* 受信リングバッファあふれで捨てた数 */
    uint32_t tx_frames;        /* 送信キュー（メールボックス）へ入れた数 */
    uint32_t tx_dropped;       /* メールボックス満杯で捨てた数 */
    uint32_t err_events;       /* HAL エラーコールバックの回数 */
    uint32_t err_passive;      /* エラーパッシブへ遷移した回数 */
    uint32_t err_busoff;       /* バスオフになった回数 */
    uint32_t err_rx_overrun;   /* 受信 FIFO オーバーラン回数 */
    uint32_t err_last_code;    /* 直近の HAL_CAN_GetError() 値 */
} comm_stats_t;

extern volatile comm_stats_t comm_stats;

/* ---- トランスポート非依存の要求処理 ---- */

/*
 * can_id/dlc/data で受け取ったフレームを処理し、送信すべき応答フレームを resp に作る。
 * 戻り値は応答フレーム数（0 または 1、resp_max が上限）。
 *   - 0x000 でも 0x100+ID でもないフレームは無視する（0 を返す。FR-COM-04）
 *   - 0x100+ID を受けると通信タイムアウトの計時をリセットする（FR-MON-09）
 *   - 0x000（ブロードキャスト）は DISABLE/STOP/ESTOP のみ実行し、応答しない
 * data は dlc バイト以上有効であること。main ループから呼ぶ。
 */
uint8_t comm_process_request(uint16_t can_id, uint8_t dlc, const uint8_t *data,
                             comm_frame_t *resp, uint8_t resp_max);

/* 異常通知フレーム（0x080+ID、[STATE][FAULT]、DLC 2）を作る（FR-COM-10。USB イベントにも流用可） */
void comm_make_fault_frame(uint8_t state, uint8_t fault, comm_frame_t *out);

/* ---- CAN 層 ---- */

/* CAN1 を初期化して開始する。id = DIP 値（起動時に確定済み）。CAN_S は呼び出し前に Low にしておく */
void comm_init(uint8_t id);

/* 受信リングバッファを処理して応答を送る。保留中の異常通知があれば再送を試す（main ループから） */
void comm_poll(void);

/* STATE が FAULT へ遷移したときに 1 回呼ぶ。異常通知を送信キューへ入れる */
void comm_notify_fault(uint8_t state, uint8_t fault);

/* 自局の ID（DIP 値 0〜7。comm_init で渡されたもの。USB の CONNECT 応答用） */
uint8_t comm_board_id(void);

/* 自局宛て要求（0x100+ID、USB 経由を含む）を最後に受けた時刻 [ms]（FR-MON-09） */
uint32_t comm_last_request_ms(void);

/* LED_COMM 用: CAN で自局宛て／ブロードキャストを受けるたびに増える */
uint32_t comm_activity_count(void);

/* LED_COMM 用: CAN がエラーパッシブまたはバスオフ */
bool comm_bus_degraded(void);

void comm_can_rx0_isr(void);
void comm_can_sce_isr(void);

#endif /* COMM_H */
