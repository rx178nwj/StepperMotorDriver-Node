/*
 * usb_proto.h — USB CDC によるデバッグ・設定通信プロトコル（要求仕様 3.9 節）
 *
 *   フレーム: [0xA5][LEN][BODY: LEN バイト][CRC16 下位][CRC16 上位]
 *   要求 BODY [CMD][CTR][PARAM…] → 応答 BODY [0xFF][ERR][CTR][DATA…]、イベント BODY [0xFE][EVT][DATA…]
 *
 * 構成:
 *   - USB 割り込み（CDC_Receive_FS）は usb_proto_rx_isr で受信リングバッファへ積むだけ（FR-USB-04）。
 *   - フレーム解析・コマンド処理・送信は usb_proto_poll を main ループから呼んで行う。
 *   - CAN 要求処理は comm_process_request を再利用する（USB↔CAN バスのブリッジはしない）。
 */
#ifndef USB_PROTO_H
#define USB_PROTO_H

#include "comm.h"
#include <stdbool.h>
#include <stdint.h>

#define USB_PROTO_MAJOR   1u
#define USB_PROTO_MINOR   0u

#define USB_FRAME_SOF     0xA5u
#define USB_LEN_MIN       2u
#define USB_LEN_MAX       250u            /* CONNECT 応答の MAX_LEN */

#define USB_PID_RESPONSE  0xFFu
#define USB_PID_EVENT     0xFEu
#define USB_EVT_CAN_FRAME 0x01u

/* コマンド（CMD） */
#define USB_CMD_CONNECT       0x01u
#define USB_CMD_SET_MTA       0x02u
#define USB_CMD_UPLOAD        0x04u
#define USB_CMD_DISCONNECT    0x07u
#define USB_CMD_SHORT_UP      0x0Fu
#define USB_CMD_EXCHANGE_ID   0x17u
#define USB_CMD_GET_VERSION   0x1Bu
#define USB_CMD_SYM_COUNT     0x20u
#define USB_CMD_SYM_INFO      0x21u
#define USB_CMD_SYM_FIND      0x22u
#define USB_CMD_SYM_UPLOAD    0x23u
#define USB_CMD_CAN_REQUEST   0x40u

/* ERR（CCP のコードに合わせる） */
#define USB_ERR_OK            0x00u
#define USB_ERR_UNKNOWN_CMD   0x30u   /* 未定義コマンド */
#define USB_ERR_SYNTAX        0x31u   /* BODY 長がコマンドの形式と合わない */
#define USB_ERR_RANGE         0x32u   /* SIZE・INDEX・CAN_ID・DLC が範囲外 */
#define USB_ERR_DENIED        0x33u   /* 読み出し許可領域外 */
#define USB_ERR_LOCKED        0x35u   /* CONNECT していない */
#define USB_ERR_NO_RESOURCE   0x36u   /* 名前なし、Flash ECC エラー */

#define USB_UPLOAD_MAX        240u    /* UPLOAD / SHORT_UP / SYM_UPLOAD の最大 SIZE */
#define USB_BUILD_ID_LEN      20u

/* USB 統計（名前表 usb_stats.* で公開。割り込みからも更新される） */
typedef struct {
    uint32_t rx_frames;     /* CRC まで正しく受信したフレーム数 */
    uint32_t crc_errors;    /* CRC 不一致で捨てたフレーム数 */
    uint32_t dropped;       /* 捨てた回数の合計（受信あふれ発生回数 + rx_timeouts + tx_dropped） */
    uint32_t rx_overflow;   /* 受信リングバッファあふれで捨てたバイト数 */
    uint32_t rx_timeouts;   /* フレーム途中で 20 ms 途切れて捨てた数 */
    uint32_t bad_len;       /* LEN が範囲外で SOF を探し直した回数 */
    uint32_t tx_frames;     /* 送信を USB ミドルウェアへ渡したフレーム数 */
    uint32_t tx_dropped;    /* 50 ms 送信できず捨てた応答・イベント数 */
} usb_stats_t;

extern volatile usb_stats_t usb_stats;

/* main ループから毎回呼ぶ（受信解析・コマンド処理・送信・イベント） */
void usb_proto_poll(void);

/* USB 割り込みから: 受信データをリングバッファへ積むだけ */
void usb_proto_rx_isr(const uint8_t *buf, uint32_t len);

/* USB バスリセット・サスペンド・クラス初期化／解除で呼ぶ。セッションを切断状態にする（FR-USB-07）。
 * 割り込みから呼んでよい */
void usb_proto_on_bus_event(void);

/* FR-COM-10 の異常通知など、CAN に送るフレームを USB イベント（EVT=0x01）としても送る。
 * セッション接続中のみ。main ループから呼ぶ */
void usb_proto_notify_can_frame(const comm_frame_t *f);

/* ---- 単体で試験できる部品 ---- */

/* CRC-16/CCITT-FALSE（多項式 0x1021、初期値 0xFFFF、反転なし）。"123456789" → 0x29B1 */
uint16_t usb_crc16(const uint8_t *data, uint32_t len);

/* 読み出し許可領域の判定（FR-USB-31）。size バイトの全体が 1 つの許可領域に収まれば true */
bool usb_mem_allowed(uint32_t addr, uint32_t size);

#endif /* USB_PROTO_H */
