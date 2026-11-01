#include "comm.h"
#include "app.h"
#include "board.h"
#include "config.h"
#include "symtab.h"
#include "usb_proto.h"
#include <string.h>

#define RX_RING_LEN  17u   /* 16 件保持（満杯判定のため 1 スロット空ける）。2 のべき乗でなくてよい。main が停止中（Flash 消去 約 25 ms）の取りこぼし対策 */

typedef struct {
    uint16_t id;
    uint8_t dlc;
    uint8_t data[8];
} rx_entry_t;

static CAN_HandleTypeDef hcan;
static bool can_ready;
static uint8_t board_id;

static rx_entry_t rx_ring[RX_RING_LEN];
static volatile uint8_t rx_head;   /* ISR が書く */
static volatile uint8_t rx_tail;   /* main が書く */

static volatile uint32_t last_request_ms;
static uint32_t activity_count;

static bool fault_pending;         /* 異常通知が送信キューに入るまで保留 */
static comm_frame_t fault_frame;

volatile comm_stats_t comm_stats;
volatile comm_diag_t comm_diag;

/* ---------------------------------------------------------------- 要求処理（トランスポート非依存） */

static void make_error(comm_frame_t *r, uint8_t reg)
{
    r->dlc = 3;
    r->data[0] = COMM_OP_ERROR;
    r->data[1] = reg;
    r->data[2] = RESULT_INVALID;
}

uint8_t comm_process_request(uint16_t can_id, uint8_t dlc, const uint8_t *data,
                             comm_frame_t *resp, uint8_t resp_max)
{
    if (can_id == CAN_ID_BROADCAST) {
        /* ブロードキャスト: DISABLE / STOP / ESTOP だけ実行、応答なし、その他は無視 */
        if (dlc == 1u && (data[0] == CMD_DISABLE || data[0] == CMD_STOP || data[0] == CMD_ESTOP)) {
            app_broadcast_command(data[0]);
        }
        return 0;
    }
    if (can_id != (uint16_t)(CAN_ID_REQUEST_BASE + board_id)) {
        return 0;   /* 他局宛て・対象外 ID は無視（FR-COM-04） */
    }

    last_request_ms = HAL_GetTick();
    if (resp_max < 1u) {
        return 0;
    }

    comm_frame_t *r = &resp[0];
    memset(r, 0, sizeof *r);
    r->id = (uint16_t)(CAN_ID_RESPONSE_BASE + board_id);

    uint8_t op = dlc >= 1u ? data[0] : 0u;
    uint8_t reg = dlc >= 2u ? data[1] : 0u;

    if (op == COMM_OP_WRITE && dlc >= 3u && dlc <= 8u) {
        uint8_t result = app_reg_write(reg, &data[2], (uint8_t)(dlc - 2u));
        r->dlc = 3;
        r->data[0] = COMM_OP_WRITE_RSP;
        r->data[1] = reg;
        r->data[2] = result;
    } else if (op == COMM_OP_READ && dlc == 3u && data[2] >= 1u && data[2] <= COMM_READ_MAX) {
        uint8_t len = data[2];
        r->dlc = (uint8_t)(2u + len);
        r->data[0] = COMM_OP_READ_RSP;
        r->data[1] = reg;
        app_reg_read(reg, len, &r->data[2]);
    } else {
        make_error(r, reg);   /* 未定義の OP、DLC 不一致、LEN 範囲外 */
    }
    return 1;
}

void comm_make_fault_frame(uint8_t state, uint8_t fault, comm_frame_t *out)
{
    memset(out, 0, sizeof *out);
    out->id = (uint16_t)(CAN_ID_FAULT_BASE + board_id);
    out->dlc = 2;
    out->data[0] = state;
    out->data[1] = fault;
}

/* ---------------------------------------------------------------- CAN 層 */

void comm_init(uint8_t id)
{
    board_id = id;

    /* FR-COM-14: CAN を起動する前に CAN_S を Low（ノーマルモード）にする。
     * board_init でも Low にしているが、順序が崩れても安全なようにここでも保証する */
    board_pin_write(CAN_S_PORT, CAN_S_PIN, false);

    __HAL_RCC_CAN1_CLK_ENABLE();

    hcan.Instance = CAN1;
    hcan.Init.Prescaler = CAN_PRESCALER;
    hcan.Init.Mode = CAN_MODE_NORMAL;
    hcan.Init.SyncJumpWidth = CAN_SJW_1TQ;
    hcan.Init.TimeSeg1 = CAN_BS1_13TQ;
    hcan.Init.TimeSeg2 = CAN_BS2_2TQ;
    hcan.Init.TimeTriggeredMode = DISABLE;
    hcan.Init.AutoBusOff = ENABLE;             /* バスオフから自動復帰（FR-COM-12） */
    hcan.Init.AutoWakeUp = DISABLE;
    hcan.Init.AutoRetransmission = ENABLE;     /* NART 無効＝自動再送 */
    hcan.Init.ReceiveFifoLocked = DISABLE;
    hcan.Init.TransmitFifoPriority = DISABLE;
    if (HAL_CAN_Init(&hcan) != HAL_OK) {
        comm_diag.init_stage = COMM_INIT_FAIL_CAN_INIT;
        return;
    }

    /* 受信フィルタ: 16bit スケール・マスクモードの 1 バンクに 2 本。
     * 標準 ID 11bit 完全一致、データフレーム（RTR=0）、標準フレーム（IDE=0）だけ通す。
     *   フィルタ A: 0x000（ブロードキャスト）  フィルタ B: 0x100 + ID（自局宛て要求） */
    const uint32_t mask = (0x7FFu << 5) | (1u << 4) | (1u << 3);   /* STID 全ビット + RTR + IDE */
    CAN_FilterTypeDef f = {0};
    f.FilterBank = 0;
    f.FilterMode = CAN_FILTERMODE_IDMASK;
    f.FilterScale = CAN_FILTERSCALE_16BIT;
    f.FilterIdHigh = (uint32_t)(CAN_ID_BROADCAST << 5);
    f.FilterMaskIdHigh = mask;
    f.FilterIdLow = (uint32_t)((CAN_ID_REQUEST_BASE + id) << 5);
    f.FilterMaskIdLow = mask;
    f.FilterFIFOAssignment = CAN_FILTER_FIFO0;
    f.FilterActivation = ENABLE;
    f.SlaveStartFilterBank = 14;
    if (HAL_CAN_ConfigFilter(&hcan, &f) != HAL_OK) {
        comm_diag.init_stage = COMM_INIT_FAIL_FILTER;
        return;
    }

    HAL_CAN_ActivateNotification(&hcan, CAN_IT_RX_FIFO0_MSG_PENDING | CAN_IT_RX_FIFO0_OVERRUN |
                                        CAN_IT_ERROR_PASSIVE | CAN_IT_BUSOFF | CAN_IT_ERROR);

    HAL_NVIC_SetPriority(CAN1_RX0_IRQn, IRQ_PRIO_CAN, 0);
    HAL_NVIC_SetPriority(CAN1_SCE_IRQn, IRQ_PRIO_CAN, 0);
    HAL_NVIC_EnableIRQ(CAN1_RX0_IRQn);
    HAL_NVIC_EnableIRQ(CAN1_SCE_IRQn);

    if (HAL_CAN_Start(&hcan) == HAL_OK) {
        can_ready = true;
        comm_diag.init_stage = COMM_INIT_RUNNING;
    } else {
        comm_diag.init_stage = COMM_INIT_FAIL_START;
    }
}

/* 送信メールボックスが空いていれば入れる。ブロックしない */
static bool can_try_send(const comm_frame_t *f)
{
    if (!can_ready || HAL_CAN_GetTxMailboxesFreeLevel(&hcan) == 0u) {
        return false;
    }
    CAN_TxHeaderTypeDef h = {0};
    h.StdId = f->id;
    h.IDE = CAN_ID_STD;
    h.RTR = CAN_RTR_DATA;
    h.DLC = f->dlc;
    h.TransmitGlobalTime = DISABLE;
    uint32_t mailbox;
    if (HAL_CAN_AddTxMessage(&hcan, &h, f->data, &mailbox) != HAL_OK) {
        return false;
    }
    comm_stats.tx_frames++;
    return true;
}

/* CAN1->ESR を読んで comm_diag を更新する（comm_poll から 1 ms ごと。新しい割り込みは使わない）。
 * 1 ms ごとのサンプリングなので、その間に複数回起きたエラーや短い EWGF は取りこぼす。
 * lec_count / ewg_count は実際の回数の下限値。
 * LEC はハードが上書きするまで保持されるため、読んだら 7（ソフトで書ける未使用コード）にして
 * 次のエラー更新を検出できるようにする（RM0394） */
static void diag_update(void)
{
    static bool prev_ewg;

    uint32_t esr = CAN1->ESR;
    uint32_t tec = (esr & CAN_ESR_TEC) >> CAN_ESR_TEC_Pos;
    uint32_t rec = (esr & CAN_ESR_REC) >> CAN_ESR_REC_Pos;
    bool ewg = (esr & CAN_ESR_EWGF) != 0u;
    uint32_t lec = (esr & CAN_ESR_LEC) >> CAN_ESR_LEC_Pos;

    comm_diag.esr = esr;
    comm_diag.tec = tec;
    comm_diag.rec = rec;
    if (tec > comm_diag.tec_max) comm_diag.tec_max = tec;
    if (rec > comm_diag.rec_max) comm_diag.rec_max = rec;
    if (ewg && !prev_ewg) comm_diag.ewg_count++;
    prev_ewg = ewg;

    if (lec >= 1u && lec <= 6u) {
        comm_diag.lec_count[lec - 1u]++;
        CAN1->ESR = CAN_ESR_LEC;   /* LEC を 7 にする */
    }
}

void comm_poll(void)
{
    if (can_ready) {
        diag_update();
    }

    while (rx_tail != rx_head) {
        rx_entry_t e = rx_ring[rx_tail];
        rx_tail = (uint8_t)((rx_tail + 1u) % RX_RING_LEN);

        /* FR-UI-04: 自局宛て／ブロードキャストの受信ごとに LED_COMM を点灯させる */
        if (e.id == CAN_ID_BROADCAST || e.id == (uint16_t)(CAN_ID_REQUEST_BASE + board_id)) {
            activity_count++;
        }

        comm_frame_t resp[COMM_RESP_MAX];
        uint8_t n = comm_process_request(e.id, e.dlc, e.data, resp, COMM_RESP_MAX);
        for (uint8_t i = 0; i < n; i++) {
            if (!can_try_send(&resp[i])) {
                comm_stats.tx_dropped++;   /* 満杯時は諦めて破棄（main をブロックしない） */
            }
        }
    }

    if (fault_pending && can_try_send(&fault_frame)) {
        fault_pending = false;
    }
}

void comm_notify_fault(uint8_t state, uint8_t fault)
{
    if (fault_pending) {
        comm_stats.tx_dropped++;   /* 未送信の異常通知を新しい通知で上書きする（FR-COM-12） */
    }
    comm_make_fault_frame(state, fault, &fault_frame);
    fault_pending = true;
    if (can_try_send(&fault_frame)) {
        fault_pending = false;
    }
    /* FR-USB 3.9.3: 同じ内容を USB イベント（EVT=0x01）としても通知する（セッション接続中のみ） */
    usb_proto_notify_can_frame(&fault_frame);
}

uint8_t comm_board_id(void)
{
    return board_id;
}

uint32_t comm_last_request_ms(void)
{
    return last_request_ms;
}

uint32_t comm_activity_count(void)
{
    return activity_count;
}

bool comm_bus_degraded(void)
{
    return can_ready && (CAN1->ESR & (CAN_ESR_EPVF | CAN_ESR_BOFF)) != 0u;
}

/* ---------------------------------------------------------------- 割り込み */

void HAL_CAN_RxFifo0MsgPendingCallback(CAN_HandleTypeDef *h)
{
    CAN_RxHeaderTypeDef hd;
    uint8_t d[8];

    while (HAL_CAN_GetRxFifoFillLevel(h, CAN_RX_FIFO0) > 0u) {
        if (HAL_CAN_GetRxMessage(h, CAN_RX_FIFO0, &hd, d) != HAL_OK) {
            break;
        }
        if (hd.IDE != CAN_ID_STD || hd.RTR != CAN_RTR_DATA) {
            continue;   /* フィルタで除外済みのはず（念のため） */
        }
        comm_stats.rx_frames++;
        comm_diag.last_rx_id = hd.StdId;
        comm_diag.last_rx_dlc = hd.DLC;
        for (uint8_t i = 0; i < 8u; i++) {
            comm_diag.last_rx_data[i] = d[i];
        }
        uint8_t next = (uint8_t)((rx_head + 1u) % RX_RING_LEN);
        if (next == rx_tail) {
            comm_stats.rx_dropped++;
            continue;
        }
        rx_entry_t *e = &rx_ring[rx_head];
        e->id = (uint16_t)hd.StdId;
        e->dlc = hd.DLC > 8u ? 8u : (uint8_t)hd.DLC;   /* DLC 9〜15 は 8 バイト扱い */
        memcpy(e->data, d, sizeof e->data);
        rx_head = next;
    }
}

/* エラー割り込み（エラーパッシブ・バスオフ・FIFO オーバーラン）。回数だけ数える */
void HAL_CAN_ErrorCallback(CAN_HandleTypeDef *h)
{
    uint32_t err = HAL_CAN_GetError(h);

    comm_stats.err_events++;
    comm_stats.err_last_code = err;
    if (err & HAL_CAN_ERROR_EPV) comm_stats.err_passive++;
    if (err & HAL_CAN_ERROR_BOF) comm_stats.err_busoff++;
    if (err & HAL_CAN_ERROR_RX_FOV0) comm_stats.err_rx_overrun++;
    h->ErrorCode = HAL_CAN_ERROR_NONE;   /* 次回の判定が累積しないようにクリア */
}

void comm_can_rx0_isr(void)
{
    HAL_CAN_IRQHandler(&hcan);
}

void comm_can_sce_isr(void)
{
    HAL_CAN_IRQHandler(&hcan);
}

/* ---------------------------------------------------------------- USB 名前表（CAN 統計） */

SYM_ENTRY(comm_stats, SYM_BYTES, sizeof(comm_stats));
SYM_ENTRY(comm_stats.rx_frames, SYM_U32, 1);
SYM_ENTRY(comm_stats.rx_dropped, SYM_U32, 1);
SYM_ENTRY(comm_stats.tx_frames, SYM_U32, 1);
SYM_ENTRY(comm_stats.tx_dropped, SYM_U32, 1);
SYM_ENTRY(comm_stats.err_events, SYM_U32, 1);
SYM_ENTRY(comm_stats.err_passive, SYM_U32, 1);
SYM_ENTRY(comm_stats.err_busoff, SYM_U32, 1);
SYM_ENTRY(comm_stats.err_rx_overrun, SYM_U32, 1);
SYM_ENTRY(comm_stats.err_last_code, SYM_U32, 1);

/* ---------------------------------------------------------------- USB 名前表（CAN 診断） */

SYM_ENTRY(comm_diag, SYM_BYTES, sizeof(comm_diag));
SYM_ENTRY(comm_diag.init_stage, SYM_U32, 1);
SYM_ENTRY(comm_diag.esr, SYM_U32, 1);
SYM_ENTRY(comm_diag.tec, SYM_U32, 1);
SYM_ENTRY(comm_diag.rec, SYM_U32, 1);
SYM_ENTRY(comm_diag.tec_max, SYM_U32, 1);
SYM_ENTRY(comm_diag.rec_max, SYM_U32, 1);
SYM_ENTRY(comm_diag.ewg_count, SYM_U32, 1);
SYM_ENTRY(comm_diag.lec_count, SYM_U32, 6);
SYM_ENTRY(comm_diag.last_rx_id, SYM_U32, 1);
SYM_ENTRY(comm_diag.last_rx_dlc, SYM_U32, 1);
SYM_ENTRY(comm_diag.last_rx_data, SYM_U8, 8);
