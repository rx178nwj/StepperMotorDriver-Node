/*
 * comm.c（CAN 通信層）のホスト単体テスト（要求仕様 3.8 節 FR-COM-03〜12、FR-MON-09、FR-UI-04、FR-USB-44、試験 9）。
 * 期待値は requirements.md から決める。
 * app_reg_read / app_reg_write / app_broadcast_command は偽物（呼び出しを記録するだけ）にして、comm.c だけを確かめる。
 * bxCAN は HAL の関数をスタブにして模擬する：送信メールボックスの空き数、送信したフレームの記録、
 * 受信 FIFO（テストが積んだフレームを HAL_CAN_RxFifo0MsgPendingCallback 経由で受信リングへ流す）。
 * ビルドと実行は run_tests.sh / run_tests.ps1。
 */
#include "comm.h"
#include "app.h"
#include "board.h"
#include "config.h"
#include "usb_proto.h"
#include "stm32l4xx_hal.h"
#include <stdio.h>
#include <string.h>

/* ---------------------------------------------------------------- 検査用マクロ */

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

/* ---------------------------------------------------------------- app 側の偽物 */

#define REG_LOG_MAX  32u

typedef struct {
    uint8_t reg;
    uint8_t len;
    uint8_t data[8];
} reg_call_t;

static reg_call_t g_wr[REG_LOG_MAX];
static unsigned g_wr_n;
static reg_call_t g_rd[REG_LOG_MAX];
static unsigned g_rd_n;
static uint8_t g_wr_result;            /* app_reg_write の戻り値 */
static uint8_t g_bc[REG_LOG_MAX];
static unsigned g_bc_n;
static unsigned g_usb_notify_n;
static comm_frame_t g_usb_notify;
uint32_t g_tick;

uint8_t app_reg_write(uint8_t reg, const uint8_t *data, uint8_t len)
{
    if (g_wr_n < REG_LOG_MAX) {
        g_wr[g_wr_n].reg = reg;
        g_wr[g_wr_n].len = len;
        memcpy(g_wr[g_wr_n].data, data, len);
    }
    g_wr_n++;
    return g_wr_result;
}

/* 読み出し値は reg + i + 0x10（レジスタごとに違う値を返す） */
void app_reg_read(uint8_t reg, uint8_t len, uint8_t *out)
{
    if (g_rd_n < REG_LOG_MAX) {
        g_rd[g_rd_n].reg = reg;
        g_rd[g_rd_n].len = len;
    }
    g_rd_n++;
    for (uint8_t i = 0; i < len; i++) {
        out[i] = (uint8_t)(reg + i + 0x10u);
    }
}

void app_broadcast_command(uint8_t cmd)
{
    if (g_bc_n < REG_LOG_MAX) {
        g_bc[g_bc_n] = cmd;
    }
    g_bc_n++;
}

void usb_proto_notify_can_frame(const comm_frame_t *f)
{
    g_usb_notify = *f;
    g_usb_notify_n++;
}

uint32_t HAL_GetTick(void)
{
    return g_tick;
}

/* ---------------------------------------------------------------- bxCAN の模擬 */

#define TX_LOG_MAX  64u
#define RXQ_MAX     64u

CAN_TypeDef g_can1;

typedef struct {
    uint16_t id;
    uint8_t ide;
    uint8_t rtr;
    uint8_t dlc;
    uint8_t data[8];
} bus_frame_t;

static uint32_t g_tx_free;             /* 送信メールボックスの空き数（0 で満杯。送信しても減らさず、テストが決める） */
static comm_frame_t g_tx[TX_LOG_MAX];  /* 送信キューへ入ったフレーム */
static unsigned g_tx_n;
static bus_frame_t g_rxq[RXQ_MAX];     /* 受信 FIFO の中身 */
static unsigned g_rxq_head, g_rxq_tail;
static CAN_FilterTypeDef g_filter;
static unsigned g_filter_n;
static CAN_InitTypeDef g_can_init;
static uint32_t g_cans_at_start;       /* HAL_CAN_Start の時点の CAN_S 出力（BSRR の値） */
static unsigned g_start_n;
static int g_fail_init, g_fail_filter, g_fail_start;   /* 1 で該当 HAL 関数を失敗させる（comm_diag.init_stage 用） */

HAL_StatusTypeDef HAL_CAN_Init(CAN_HandleTypeDef *h)
{
    g_can_init = h->Init;
    return g_fail_init ? HAL_ERROR : HAL_OK;
}

HAL_StatusTypeDef HAL_CAN_ConfigFilter(CAN_HandleTypeDef *h, CAN_FilterTypeDef *f)
{
    (void)h;
    g_filter = *f;
    g_filter_n++;
    return g_fail_filter ? HAL_ERROR : HAL_OK;
}

HAL_StatusTypeDef HAL_CAN_ActivateNotification(CAN_HandleTypeDef *h, uint32_t it)
{
    (void)h;
    (void)it;
    return HAL_OK;
}

HAL_StatusTypeDef HAL_CAN_Start(CAN_HandleTypeDef *h)
{
    (void)h;
    g_cans_at_start = g_gpioc.BSRR;
    g_start_n++;
    return g_fail_start ? HAL_ERROR : HAL_OK;
}

uint32_t HAL_CAN_GetTxMailboxesFreeLevel(CAN_HandleTypeDef *h)
{
    (void)h;
    return g_tx_free;
}

HAL_StatusTypeDef HAL_CAN_AddTxMessage(CAN_HandleTypeDef *h, CAN_TxHeaderTypeDef *hd,
                                       const uint8_t *data, uint32_t *mailbox)
{
    (void)h;
    (void)mailbox;
    if (g_tx_free == 0u) {
        return HAL_ERROR;
    }
    if (g_tx_n < TX_LOG_MAX) {
        comm_frame_t *f = &g_tx[g_tx_n];
        memset(f, 0, sizeof *f);
        f->id = (uint16_t)hd->StdId;
        f->dlc = (uint8_t)hd->DLC;
        memcpy(f->data, data, 8);
        /* 標準 ID のデータフレームだけを送る */
        if (hd->IDE != CAN_ID_STD || hd->RTR != CAN_RTR_DATA) {
            f->id = 0xFFFFu;
        }
    }
    g_tx_n++;
    return HAL_OK;
}

uint32_t HAL_CAN_GetRxFifoFillLevel(CAN_HandleTypeDef *h, uint32_t fifo)
{
    (void)h;
    (void)fifo;
    return g_rxq_head - g_rxq_tail;
}

HAL_StatusTypeDef HAL_CAN_GetRxMessage(CAN_HandleTypeDef *h, uint32_t fifo,
                                       CAN_RxHeaderTypeDef *hd, uint8_t *data)
{
    (void)h;
    (void)fifo;
    if (g_rxq_head == g_rxq_tail) {
        return HAL_ERROR;
    }
    const bus_frame_t *b = &g_rxq[g_rxq_tail % RXQ_MAX];
    g_rxq_tail++;
    memset(hd, 0, sizeof *hd);
    hd->StdId = b->id;
    hd->IDE = b->ide;
    hd->RTR = b->rtr;
    hd->DLC = b->dlc;
    memcpy(data, b->data, 8);
    return HAL_OK;
}

uint32_t HAL_CAN_GetError(CAN_HandleTypeDef *h)
{
    return h->ErrorCode;
}

void HAL_CAN_IRQHandler(CAN_HandleTypeDef *h)
{
    (void)h;
}

/* ---------------------------------------------------------------- テストの道具 */

static comm_stats_t stats(void)
{
    comm_stats_t s;
    memcpy(&s, (const void *)&comm_stats, sizeof s);
    return s;
}

/* CAN 層と偽物を初期状態に戻し、基板 ID を id にして comm_init する */
static void setup(uint8_t id)
{
    g_wr_n = g_rd_n = g_bc_n = 0;
    g_usb_notify_n = 0;
    g_wr_result = 0;
    g_tx_n = 0;
    g_tx_free = 3;
    g_rxq_head = g_rxq_tail = 0;
    g_tick = 1000;
    memset((void *)&comm_stats, 0, sizeof comm_stats);
    comm_init(id);
    /* 前のテストが残した保留中の異常通知と受信リングを片付ける */
    comm_poll();
    g_tx_n = 0;
    g_tx_free = 3;
    g_wr_n = g_rd_n = g_bc_n = 0;
    g_usb_notify_n = 0;
    memset((void *)&comm_stats, 0, sizeof comm_stats);
}

/* 受信 FIFO にフレームを積む */
static void bus_push(uint16_t id, uint8_t dlc, const uint8_t *data)
{
    bus_frame_t *b = &g_rxq[g_rxq_head % RXQ_MAX];
    memset(b, 0, sizeof *b);
    b->id = id;
    b->ide = CAN_ID_STD;
    b->rtr = CAN_RTR_DATA;
    b->dlc = dlc;
    if (dlc > 0u) {
        memcpy(b->data, data, dlc > 8u ? 8u : dlc);
    }
    g_rxq_head++;
}

/* 積んだフレームを受信割り込みで受信リングへ流す */
static void bus_deliver(void)
{
    CAN_HandleTypeDef dummy = {0};
    HAL_CAN_RxFifo0MsgPendingCallback(&dummy);
}

/* 1 フレームを CAN で受信して comm_poll で処理する */
static void rx(uint16_t id, uint8_t dlc, const uint8_t *data)
{
    bus_push(id, dlc, data);
    bus_deliver();
    comm_poll();
}

static const comm_frame_t *last_tx(void)
{
    return g_tx_n > 0u ? &g_tx[g_tx_n - 1u] : NULL;
}

/* CAN で受信した 1 フレームについて、応答を送ったか（g_tx_n が増えたか） */
static bool rx_responded(uint16_t id, uint8_t dlc, const uint8_t *data)
{
    unsigned before = g_tx_n;
    rx(id, dlc, data);
    return g_tx_n != before;
}

static comm_frame_t proc(uint16_t id, uint8_t dlc, const uint8_t *data, uint8_t *n)
{
    comm_frame_t r;
    memset(&r, 0xAA, sizeof r);
    *n = comm_process_request(id, dlc, data, &r, COMM_RESP_MAX);
    return r;
}

/* ---------------------------------------------------------------- 初期化（FR-COM-04、FR-COM-12、FR-COM-14） */

static void test_init(void)
{
    g_gpioc.BSRR = 0;
    g_start_n = 0;
    g_filter_n = 0;
    setup(5);
    CHECK(g_start_n >= 1u);
    /* FR-COM-14: CAN 起動前に CAN_S を Low（BSRR の上位 16 ビット＝リセット）にする */
    CHECK(g_cans_at_start == ((uint32_t)CAN_S_PIN << 16));
    /* FR-COM-12: バスオフから自動復帰（ABOM 有効）、送信は自動再送（NART 無効） */
    CHECK(g_can_init.AutoBusOff == ENABLE);
    CHECK(g_can_init.AutoRetransmission == ENABLE);
    /* FR-COM-04: 受信フィルタは 0x000 と 0x100+ID だけ、標準 ID 11bit 完全一致、データフレームのみ */
    CHECK(g_filter_n == 1u);
    CHECK((g_filter.FilterIdHigh >> 5) == 0x000u);
    CHECK((g_filter.FilterIdLow >> 5) == 0x105u);
    CHECK((g_filter.FilterMaskIdHigh >> 5) == 0x7FFu);
    CHECK((g_filter.FilterMaskIdLow >> 5) == 0x7FFu);
    CHECK((g_filter.FilterMaskIdHigh & (1u << 4)) != 0u);   /* RTR を比較する（IdHigh/IdLow の RTR=0） */
    CHECK((g_filter.FilterMaskIdLow & (1u << 4)) != 0u);
    CHECK((g_filter.FilterMaskIdHigh & (1u << 3)) != 0u);   /* IDE を比較する（標準フレームだけ） */
    CHECK((g_filter.FilterMaskIdLow & (1u << 3)) != 0u);
    CHECK((g_filter.FilterIdHigh & ((1u << 4) | (1u << 3))) == 0u);
    CHECK((g_filter.FilterIdLow & ((1u << 4) | (1u << 3))) == 0u);
    CHECK(comm_board_id() == 5u);

    setup(0);
    CHECK((g_filter.FilterIdLow >> 5) == 0x100u);
    CHECK(comm_board_id() == 0u);
    setup(7);
    CHECK((g_filter.FilterIdLow >> 5) == 0x107u);
    CHECK(comm_board_id() == 7u);
}

/* ---------------------------------------------------------------- 受け付ける ID（FR-COM-03、FR-COM-04） */

/* 読み出し要求 [02][reg][1] を id で送り、応答したか・app を呼んだかを返す */
static void check_id_filter(uint8_t board)
{
    setup(board);
    const uint8_t wr[] = {COMM_OP_WRITE, 0x20, 0x55};
    const uint8_t rd[] = {COMM_OP_READ, 0x20, 1};
    const uint16_t own = (uint16_t)(0x100u + board);

    /* 自局宛て */
    CHECK(rx_responded(own, 3, rd));
    CHECK(g_rd_n == 1u);
    CHECK(rx_responded(own, 3, wr));
    CHECK(g_wr_n == 1u);

    /* 無視する ID：他局宛て 0x100〜0x107、0x180+ID、0x080+ID、その他。応答もせず app も呼ばない */
    g_rd_n = g_wr_n = g_bc_n = 0;
    unsigned tx_before = g_tx_n;
    for (uint16_t id = 0; id <= 0x7FFu; id++) {
        if (id == own || id == CAN_ID_BROADCAST) {
            continue;
        }
        /* 受信フィルタを通らないフレームだが、comm_process_request 側でも捨てることを確かめる */
        uint8_t n;
        (void)proc(id, 3, rd, &n);
        CHECK(n == 0u);
        (void)proc(id, 3, wr, &n);
        CHECK(n == 0u);
        const uint8_t bc[] = {CMD_STOP};
        (void)proc(id, 1, bc, &n);
        CHECK(n == 0u);
    }
    CHECK(g_rd_n == 0u);
    CHECK(g_wr_n == 0u);
    CHECK(g_bc_n == 0u);
    CHECK(g_tx_n == tx_before);

    /* 同じフレームを受信リング経由（comm_poll）で流しても応答しない */
    static const uint16_t others[] = {0x080, 0x100, 0x107, 0x180, 0x1FF, 0x200, 0x7FF, 0x001};
    for (unsigned i = 0; i < sizeof others / sizeof others[0]; i++) {
        uint16_t id = (uint16_t)(others[i] + 0u);
        if (id == own) {
            id = (uint16_t)(own == 0x100u ? 0x101u : 0x100u);
        }
        CHECK(!rx_responded(id, 3, rd));
    }
    CHECK(!rx_responded((uint16_t)(0x080u + board), 3, rd));
    CHECK(!rx_responded((uint16_t)(0x180u + board), 3, rd));
    CHECK(!rx_responded((uint16_t)(0x100u + ((board + 1u) & 7u)), 3, rd));
    CHECK(g_rd_n == 0u);
    CHECK(g_wr_n == 0u);
}

static void test_id_filter(void)
{
    check_id_filter(0);
    check_id_filter(3);
    check_id_filter(7);
}

/* 拡張 ID・リモートフレームは受信割り込みで捨てる（comm.c は標準 ID のデータフレームだけ扱う） */
static void test_ext_and_rtr_ignored(void)
{
    setup(2);
    const uint8_t rd[] = {COMM_OP_READ, 0x20, 1};

    bus_push(0x102, 3, rd);
    g_rxq[(g_rxq_head - 1u) % RXQ_MAX].ide = CAN_ID_EXT;   /* 拡張フレーム（StdId は 0x102 と同じ） */
    bus_deliver();
    comm_poll();
    CHECK(g_tx_n == 0u);
    CHECK(g_rd_n == 0u);

    bus_push(0x102, 3, rd);
    g_rxq[(g_rxq_head - 1u) % RXQ_MAX].rtr = CAN_RTR_REMOTE;   /* リモートフレーム */
    bus_deliver();
    comm_poll();
    CHECK(g_tx_n == 0u);
    CHECK(g_rd_n == 0u);

    bus_push(CAN_ID_BROADCAST, 1, (const uint8_t[]){CMD_STOP});
    g_rxq[(g_rxq_head - 1u) % RXQ_MAX].rtr = CAN_RTR_REMOTE;
    bus_deliver();
    comm_poll();
    CHECK(g_bc_n == 0u);

    /* 標準データフレームは受け付ける */
    CHECK(rx_responded(0x102, 3, rd));
    CHECK(stats().rx_frames == 1u);   /* 捨てたフレームは数えない */
}

/* ---------------------------------------------------------------- 書き込み（FR-COM-05） */

static void test_write(void)
{
    for (uint8_t board = 0; board <= 7u; board += 7u) {
        setup(board);
        for (uint8_t dlc = 3; dlc <= 8u; dlc++) {
            g_wr_n = 0;
            g_tx_n = 0;
            const uint8_t d[8] = {COMM_OP_WRITE, 0x24, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66};
            g_wr_result = (uint8_t)(dlc & 3u);
            rx((uint16_t)(0x100u + board), dlc, d);
            CHECK(g_wr_n == 1u);
            CHECK(g_wr[0].reg == 0x24u);
            CHECK(g_wr[0].len == dlc - 2u);
            CHECK(memcmp(g_wr[0].data, &d[2], dlc - 2u) == 0);
            CHECK(g_tx_n == 1u);
            const comm_frame_t *r = last_tx();
            CHECK(r != NULL && r->id == 0x180u + board);
            CHECK(r != NULL && r->dlc == 3u);
            CHECK(r != NULL && r->data[0] == 0x81u);
            CHECK(r != NULL && r->data[1] == 0x24u);
            CHECK(r != NULL && r->data[2] == (dlc & 3u));   /* app の戻り値（CMD_RESULT）をそのまま返す */
        }
    }
}

/* ---------------------------------------------------------------- 読み出し（FR-COM-06） */

static void test_read(void)
{
    for (uint8_t board = 0; board <= 7u; board += 7u) {
        setup(board);
        for (uint8_t len = 1; len <= 6u; len++) {
            g_rd_n = 0;
            g_tx_n = 0;
            const uint8_t d[3] = {COMM_OP_READ, 0x28, len};
            rx((uint16_t)(0x100u + board), 3, d);
            CHECK(g_rd_n == 1u);
            CHECK(g_rd[0].reg == 0x28u);
            CHECK(g_rd[0].len == len);
            CHECK(g_tx_n == 1u);
            const comm_frame_t *r = last_tx();
            CHECK(r != NULL && r->id == 0x180u + board);
            CHECK(r != NULL && r->dlc == 2u + len);
            CHECK(r != NULL && r->data[0] == 0x82u);
            CHECK(r != NULL && r->data[1] == 0x28u);
            for (uint8_t i = 0; r != NULL && i < len; i++) {
                CHECK(r->data[2 + i] == (uint8_t)(0x28u + i + 0x10u));
            }
            for (uint8_t i = 2u + len; r != NULL && i < 8u; i++) {
                CHECK(r->data[i] == 0u);   /* 余りは 0（送信しない領域だが決定的に） */
            }
        }
    }
}

/* ---------------------------------------------------------------- 不正な要求（FR-COM-07） */

static void check_error(uint8_t board, uint8_t dlc, const uint8_t *d, int check_reg, uint8_t reg)
{
    g_wr_n = g_rd_n = 0;
    g_tx_n = 0;
    rx((uint16_t)(0x100u + board), dlc, d);
    CHECK(g_wr_n == 0u);   /* 不正な要求は実行しない */
    CHECK(g_rd_n == 0u);
    CHECK(g_tx_n == 1u);
    const comm_frame_t *r = last_tx();
    CHECK(r != NULL && r->id == 0x180u + board);
    CHECK(r != NULL && r->dlc == 3u);
    CHECK(r != NULL && r->data[0] == 0xFFu);
    CHECK(r != NULL && r->data[2] == 2u);
    if (check_reg) {
        CHECK(r != NULL && r->data[1] == reg);
    }
}

static void test_invalid_request(void)
{
    for (uint8_t board = 0; board <= 7u; board += 7u) {
        setup(board);
        /* 未定義の OP（0x00、0x03、0x7F、0x81、0x82、0xFF）。DLC 3 */
        static const uint8_t ops[] = {0x00, 0x03, 0x7F, 0x80, 0x81, 0x82, 0xFE, 0xFF};
        for (unsigned i = 0; i < sizeof ops; i++) {
            const uint8_t d[3] = {ops[i], 0x31, 0x01};
            check_error(board, 3, d, 1, 0x31);
        }
        /* 書き込み DLC 2（DATA なし）、DLC 1、DLC 0 */
        const uint8_t w2[8] = {COMM_OP_WRITE, 0x20};
        check_error(board, 2, w2, 1, 0x20);
        check_error(board, 1, w2, 0, 0);
        check_error(board, 0, w2, 0, 0);
        /* 読み出し DLC 2・4・8・1・0 */
        const uint8_t r2[8] = {COMM_OP_READ, 0x20, 1, 1, 1, 1, 1, 1};
        check_error(board, 2, r2, 1, 0x20);
        check_error(board, 4, r2, 1, 0x20);
        check_error(board, 8, r2, 1, 0x20);
        check_error(board, 1, r2, 0, 0);
        check_error(board, 0, r2, 0, 0);
        /* 読み出し LEN 0・7・8・255 */
        static const uint8_t lens[] = {0, 7, 8, 255};
        for (unsigned i = 0; i < sizeof lens; i++) {
            const uint8_t d[3] = {COMM_OP_READ, 0x40, lens[i]};
            check_error(board, 3, d, 1, 0x40);
        }
        /* 不正な要求のあとも、正しい要求は処理できる */
        const uint8_t ok[3] = {COMM_OP_READ, 0x20, 2};
        g_tx_n = 0;
        rx((uint16_t)(0x100u + board), 3, ok);
        CHECK(g_tx_n == 1u && last_tx()->data[0] == 0x82u);
    }
}

/* ---------------------------------------------------------------- ブロードキャスト（FR-COM-09） */

static void test_broadcast(void)
{
    setup(4);
    /* 受け付けるのは 2 DISABLE、3 STOP、4 ESTOP だけで、応答は返さない */
    for (unsigned c = 0; c < 256u; c++) {
        g_bc_n = 0;
        g_tx_n = 0;
        const uint8_t d[1] = {(uint8_t)c};
        rx(CAN_ID_BROADCAST, 1, d);
        CHECK(g_tx_n == 0u);
        if (c >= 2u && c <= 4u) {
            CHECK(g_bc_n == 1u && g_bc[0] == (uint8_t)c);
        } else {
            CHECK(g_bc_n == 0u);
        }
    }
    /* DLC が 1 以外は、2・3・4 でも無視する */
    const uint8_t d[8] = {CMD_STOP, CMD_STOP, 0, 0, 0, 0, 0, 0};
    for (uint8_t dlc = 0; dlc <= 8u; dlc++) {
        if (dlc == 1u) {
            continue;
        }
        g_bc_n = 0;
        g_tx_n = 0;
        rx(CAN_ID_BROADCAST, dlc, d);
        CHECK(g_bc_n == 0u);
        CHECK(g_tx_n == 0u);
    }
    /* ブロードキャストはレジスタの読み書きに使われない */
    CHECK(g_rd_n == 0u && g_wr_n == 0u);

    /* 受け付ける場合も、comm_process_request は応答を返さない */
    uint8_t n;
    (void)proc(CAN_ID_BROADCAST, 1, (const uint8_t[]){CMD_ESTOP}, &n);
    CHECK(n == 0u);
}

/* ---------------------------------------------------------------- 異常通知（FR-COM-10） */

static void test_fault_notify(void)
{
    for (uint8_t board = 0; board <= 7u; board += 7u) {
        setup(board);
        comm_notify_fault(3, FAULT_TSD | FAULT_VM_UV);
        CHECK(g_tx_n == 1u);
        const comm_frame_t *r = last_tx();
        CHECK(r != NULL && r->id == 0x080u + board);
        CHECK(r != NULL && r->dlc == 2u);
        CHECK(r != NULL && r->data[0] == 3u);
        CHECK(r != NULL && r->data[1] == (FAULT_TSD | FAULT_VM_UV));
        CHECK(stats().tx_frames == 1u);
        CHECK(stats().tx_dropped == 0u);
        /* 同じ内容を USB イベントにも渡す（FR-USB 3.9.3） */
        CHECK(g_usb_notify_n == 1u);
        CHECK(g_usb_notify.id == 0x080u + board && g_usb_notify.dlc == 2u);
        CHECK(g_usb_notify.data[0] == 3u && g_usb_notify.data[1] == (FAULT_TSD | FAULT_VM_UV));
        /* 1 回の遷移につき 1 回だけ送る（comm_poll で再送しない） */
        comm_poll();
        comm_poll();
        CHECK(g_tx_n == 1u);
    }
    /* comm_make_fault_frame */
    comm_frame_t f;
    setup(6);
    comm_make_fault_frame(1, 0x20, &f);
    CHECK(f.id == 0x086u && f.dlc == 2u && f.data[0] == 1u && f.data[1] == 0x20u);
}

/* ---------------------------------------------------------------- 送信メールボックス満杯（FR-COM-12） */

static void test_tx_mailbox_full(void)
{
    setup(1);
    const uint8_t rd[3] = {COMM_OP_READ, 0x20, 1};
    const uint8_t wr[3] = {COMM_OP_WRITE, 0x20, 7};

    /* 満杯：応答は破棄して tx_dropped に計上。要求の実行自体は行われる */
    g_tx_free = 0;
    rx(0x101, 3, rd);
    CHECK(g_tx_n == 0u);
    CHECK(stats().tx_dropped == 1u);
    CHECK(stats().tx_frames == 0u);
    CHECK(g_rd_n == 1u);
    rx(0x101, 3, wr);
    CHECK(g_tx_n == 0u);
    CHECK(stats().tx_dropped == 2u);
    CHECK(g_wr_n == 1u);
    /* 破棄した応答は後から送られない */
    g_tx_free = 3;
    comm_poll();
    CHECK(g_tx_n == 0u);

    /* 空きが戻れば応答する */
    rx(0x101, 3, rd);
    CHECK(g_tx_n == 1u);
    CHECK(stats().tx_dropped == 2u);
    CHECK(stats().tx_frames == 1u);

    /* 不正応答も同じ */
    g_tx_free = 0;
    const uint8_t bad[3] = {0x55, 0, 0};
    rx(0x101, 3, bad);
    CHECK(stats().tx_dropped == 3u);

    /* ブロードキャスト・他局宛ては応答がないので計上しない */
    rx(CAN_ID_BROADCAST, 1, (const uint8_t[]){CMD_STOP});
    rx(0x102, 3, rd);
    CHECK(stats().tx_dropped == 3u);

    /* 異常通知：満杯のあいだは送信キューに入らず保留。最初の保留では tx_dropped は増えない */
    g_tx_n = 0;
    g_usb_notify_n = 0;
    comm_notify_fault(3, 0x04);
    CHECK(g_tx_n == 0u);
    CHECK(stats().tx_dropped == 3u);
    /* USB イベントは CAN の送信可否と無関係に通知する */
    CHECK(g_usb_notify_n == 1u);
    /* 未送信の通知を新しい通知で上書き：1 計上 */
    comm_notify_fault(3, 0x08);
    CHECK(stats().tx_dropped == 4u);
    comm_notify_fault(3, 0x10);
    CHECK(stats().tx_dropped == 5u);
    /* メールボックスが空けば最新の通知を 1 回だけ送る */
    g_tx_free = 3;
    comm_poll();
    CHECK(g_tx_n == 1u);
    CHECK(last_tx()->id == 0x081u && last_tx()->dlc == 2u);
    CHECK(last_tx()->data[0] == 3u && last_tx()->data[1] == 0x10u);
    CHECK(stats().tx_dropped == 5u);
    comm_poll();
    CHECK(g_tx_n == 1u);
    /* 送信済みのあとの新しい通知は上書きではない */
    comm_notify_fault(3, 0x01);
    CHECK(g_tx_n == 2u);
    CHECK(stats().tx_dropped == 5u);

    /* 保留中に応答が来る場合：応答は空きがなければ破棄、通知は保留のまま */
    g_tx_free = 0;
    comm_notify_fault(3, 0x02);
    rx(0x101, 3, rd);
    CHECK(stats().tx_dropped == 6u);   /* 応答の破棄 1 */
    CHECK(g_tx_n == 2u);
    g_tx_free = 1;
    comm_poll();
    CHECK(g_tx_n == 3u);
    CHECK(last_tx()->id == 0x081u && last_tx()->data[1] == 0x02u);
}

/* CAN 未起動（comm_init 前）は comm.c の can_ready が false で送信しないが、comm_init は本ファイルで
 * 先に呼ぶ必要があり、再現できない。HAL_CAN_Start が成功した場合のみ can_ready になる点は実機で確認する */

/* ---------------------------------------------------------------- 通信タイムアウトの計時（FR-MON-09、FR-USB-42） */

static void test_timeout_timer(void)
{
    for (uint8_t board = 0; board <= 7u; board += 7u) {
        setup(board);
        const uint8_t rd[3] = {COMM_OP_READ, 0x20, 1};
        const uint8_t wr[3] = {COMM_OP_WRITE, 0x20, 1};

        g_tick = 5000;
        rx((uint16_t)(0x100u + board), 3, rd);
        CHECK(comm_last_request_ms() == 5000u);

        /* ブロードキャスト（受け付ける値・無視する値とも）ではリセットしない */
        g_tick = 5100;
        rx(CAN_ID_BROADCAST, 1, (const uint8_t[]){CMD_STOP});
        rx(CAN_ID_BROADCAST, 1, (const uint8_t[]){CMD_ESTOP});
        rx(CAN_ID_BROADCAST, 1, (const uint8_t[]){CMD_ENABLE});
        rx(CAN_ID_BROADCAST, 2, (const uint8_t[]){CMD_STOP, 0});
        CHECK(comm_last_request_ms() == 5000u);

        /* 他局宛て・対象外 ID ではリセットしない */
        g_tick = 5200;
        rx((uint16_t)(0x100u + ((board + 1u) & 7u)), 3, rd);
        rx((uint16_t)(0x180u + board), 3, rd);
        rx((uint16_t)(0x080u + board), 3, rd);
        rx(0x7FF, 3, wr);
        CHECK(comm_last_request_ms() == 5000u);

        /* 自局宛ては、書き込み・読み出し・不正な要求のどれでもリセットする（FR-MON-09 は「要求フレームを受信」） */
        g_tick = 5300;
        rx((uint16_t)(0x100u + board), 3, wr);
        CHECK(comm_last_request_ms() == 5300u);
        g_tick = 5400;
        rx((uint16_t)(0x100u + board), 3, (const uint8_t[]){0x77, 0, 0});
        CHECK(comm_last_request_ms() == 5400u);

        /* 応答を送れない（メールボックス満杯）ときも、要求を受けたのでリセットする */
        g_tick = 5500;
        g_tx_free = 0;
        rx((uint16_t)(0x100u + board), 3, rd);
        CHECK(comm_last_request_ms() == 5500u);
        g_tx_free = 3;

        /* USB 経由（comm_process_request を直接呼ぶ）では、自局宛てでもリセットしない（FR-USB-42）。
         * 計時のリセットは CAN 受信経路（comm_poll）だけが行う */
        g_tick = 5600;
        uint8_t n;
        (void)proc((uint16_t)(0x100u + board), 3, rd, &n);
        CHECK(n == 1u);                                  /* 要求自体は処理され、応答は作られる */
        CHECK(comm_last_request_ms() == 5500u);
        (void)proc((uint16_t)(0x100u + board), 3, wr, &n);
        (void)proc((uint16_t)(0x100u + board), 3, (const uint8_t[]){0x77, 0, 0}, &n);
        CHECK(comm_last_request_ms() == 5500u);
        g_tick = 5700;
        (void)proc(CAN_ID_BROADCAST, 1, (const uint8_t[]){CMD_STOP}, &n);
        (void)proc((uint16_t)(0x100u + ((board + 1u) & 7u)), 3, rd, &n);
        CHECK(comm_last_request_ms() == 5500u);

        /* USB 経由の後でも、CAN で自局宛てを受ければリセットする */
        g_tick = 5800;
        rx((uint16_t)(0x100u + board), 3, rd);
        CHECK(comm_last_request_ms() == 5800u);
    }
}

/* ---------------------------------------------------------------- LED_COMM の点灯要求（FR-UI-04、FR-USB-44） */

static void test_led_activity(void)
{
    for (uint8_t board = 0; board <= 7u; board += 7u) {
        setup(board);
        const uint8_t rd[3] = {COMM_OP_READ, 0x20, 1};
        uint32_t a0 = comm_activity_count();

        /* 自局宛てを受信するたびに増える（読み出し・書き込み・不正な要求のどれも） */
        rx((uint16_t)(0x100u + board), 3, rd);
        CHECK(comm_activity_count() == a0 + 1u);
        rx((uint16_t)(0x100u + board), 3, (const uint8_t[]){COMM_OP_WRITE, 0x20, 1});
        CHECK(comm_activity_count() == a0 + 2u);
        rx((uint16_t)(0x100u + board), 3, (const uint8_t[]){0x66, 0, 0});
        CHECK(comm_activity_count() == a0 + 3u);

        /* ブロードキャストでも増える（無視する値でも、フレームを受信したので点灯） */
        rx(CAN_ID_BROADCAST, 1, (const uint8_t[]){CMD_STOP});
        CHECK(comm_activity_count() == a0 + 4u);
        rx(CAN_ID_BROADCAST, 1, (const uint8_t[]){CMD_ENABLE});
        CHECK(comm_activity_count() == a0 + 5u);

        /* 他局宛て・対象外 ID では増えない（受信リングに入った場合も） */
        rx((uint16_t)(0x100u + ((board + 1u) & 7u)), 3, rd);
        rx((uint16_t)(0x180u + board), 3, rd);
        rx((uint16_t)(0x080u + board), 3, rd);
        CHECK(comm_activity_count() == a0 + 5u);

        /* 1 フレーム 1 回。複数フレームをまとめて受信した場合も数が合う */
        for (int i = 0; i < 3; i++) {
            bus_push(CAN_ID_BROADCAST, 1, (const uint8_t[]){CMD_STOP});
        }
        bus_push((uint16_t)(0x100u + board), 3, rd);
        bus_deliver();
        comm_poll();
        CHECK(comm_activity_count() == a0 + 9u);

        /* 異常通知の送信では増えない */
        comm_notify_fault(3, 1);
        comm_poll();
        CHECK(comm_activity_count() == a0 + 9u);

        /* USB 経由（comm_process_request を直接呼ぶ）では増えない（FR-USB-44） */
        uint8_t n;
        (void)proc((uint16_t)(0x100u + board), 3, rd, &n);
        (void)proc(CAN_ID_BROADCAST, 1, (const uint8_t[]){CMD_STOP}, &n);
        CHECK(comm_activity_count() == a0 + 9u);
    }
}

/* ---------------------------------------------------------------- comm_process_request の戻り値・出力 */

static void test_process_request_direct(void)
{
    setup(2);
    uint8_t n;
    const uint8_t rd[3] = {COMM_OP_READ, 0x10, 3};
    comm_frame_t r = proc(0x102, 3, rd, &n);
    CHECK(n == 1u);
    CHECK(r.id == 0x182u && r.dlc == 5u && r.data[0] == 0x82u && r.data[1] == 0x10u);

    /* 応答を送らない場合、出力は触らない */
    r = proc(0x103, 3, rd, &n);
    CHECK(n == 0u);
    CHECK(r.id == 0xAAAAu);

    /* resp_max = 0 のときは応答を作らない（要求の実行もしない） */
    g_rd_n = 0;
    comm_frame_t r0;
    memset(&r0, 0xAA, sizeof r0);
    n = comm_process_request(0x102, 3, rd, &r0, 0);
    CHECK(n == 0u);
    CHECK(g_rd_n == 0u);
    CHECK(r0.id == 0xAAAAu);
}

/* ---------------------------------------------------------------- 受信リング（FR-COM-11） */

/* 受信リングへ n 件をまとめて積み（comm_poll を呼ばずに）、あとで処理する。i 件目は reg = 0x20+i の読み出し */
static void push_reads(unsigned n, uint8_t board)
{
    for (unsigned i = 0; i < n; i++) {
        const uint8_t d[3] = {COMM_OP_READ, (uint8_t)(0x20u + i), 1};
        bus_push((uint16_t)(0x100u + board), 3, d);
    }
    bus_deliver();
}

static void test_rx_ring_order_and_capacity(void)
{
    /* 8 件・15 件：取りこぼさず到着順に処理する */
    static const unsigned counts[] = {1, 8, 15};
    for (unsigned k = 0; k < sizeof counts / sizeof counts[0]; k++) {
        setup(3);
        unsigned n = counts[k];
        g_tx_free = 64;
        push_reads(n, 3);
        CHECK(stats().rx_frames == n);
        CHECK(stats().rx_dropped == 0u);
        CHECK(g_rd_n == 0u);   /* comm_poll を呼ぶまで処理しない */
        comm_poll();
        CHECK(g_rd_n == n);
        CHECK(g_tx_n == n);
        for (unsigned i = 0; i < n && i < g_rd_n; i++) {
            CHECK(g_rd[i].reg == 0x20u + i);
            CHECK(g_tx[i].data[1] == 0x20u + i);
        }
    }
}

/* FR-COM-11：受信リング 16 件までは取りこぼさず到着順に処理する。17 件目は捨てて rx_dropped に計上する */
static void test_rx_ring_16(void)
{
    setup(3);
    g_tx_free = 64;
    push_reads(16, 3);
    CHECK(stats().rx_frames == 16u);
    CHECK(stats().rx_dropped == 0u);
    comm_poll();
    CHECK(g_rd_n == 16u);
    for (unsigned i = 0; i < 16u && i < g_rd_n; i++) {
        CHECK(g_rd[i].reg == 0x20u + i);
    }

    /* 17 件目は捨てる（先の 16 件は残る） */
    setup(3);
    g_tx_free = 64;
    push_reads(17, 3);
    CHECK(stats().rx_frames == 17u);
    CHECK(stats().rx_dropped == 1u);
    comm_poll();
    CHECK(g_rd_n == 16u);
    for (unsigned i = 0; i < 16u && i < g_rd_n; i++) {
        CHECK(g_rd[i].reg == 0x20u + i);
    }
}

/* あふれた場合：実装の挙動（FR-COM-11 に規定なし）。新しいフレームを捨てて rx_dropped に計上し、
 * すでにリングにあるフレームは到着順に処理される。ここでは「捨てた数が rx_dropped に合い、処理された数と
 * 合わせて受信数に等しい」「処理済みのフレームは到着順」だけを確かめる（保持できる件数は決めない）。 */
static void test_rx_ring_overflow(void)
{
    setup(3);
    g_tx_free = 64;
    push_reads(20, 3);
    comm_stats_t s = stats();
    CHECK(s.rx_frames == 20u);
    CHECK(s.rx_dropped >= 1u);
    comm_poll();
    CHECK(g_rd_n + s.rx_dropped == 20u);
    for (unsigned i = 0; i < g_rd_n && i < REG_LOG_MAX; i++) {
        CHECK(g_rd[i].reg == 0x20u + i);   /* 先に着いたものから順に残る */
    }

    /* あふれたあとも通常どおり受信・処理できる */
    g_rd_n = 0;
    g_tx_n = 0;
    uint32_t dropped = stats().rx_dropped;
    rx(0x103, 3, (const uint8_t[]){COMM_OP_READ, 0x30, 1});
    CHECK(g_rd_n == 1u && g_rd[0].reg == 0x30u);
    CHECK(stats().rx_dropped == dropped);

    /* 処理しながらであれば、合計 16 件を超えても取りこぼさない */
    setup(3);
    g_tx_free = 64;
    for (unsigned i = 0; i < 40u; i++) {
        push_reads(1, 3);
        comm_poll();
    }
    CHECK(stats().rx_dropped == 0u);
    CHECK(g_rd_n == 40u);
}

/* ---------------------------------------------------------------- 統計 */

static void test_stats(void)
{
    setup(0);
    const uint8_t rd[3] = {COMM_OP_READ, 0x20, 1};
    rx(0x100, 3, rd);
    rx(0x100, 3, rd);
    CHECK(stats().rx_frames == 2u);
    CHECK(stats().tx_frames == 2u);
    CHECK(stats().rx_dropped == 0u && stats().tx_dropped == 0u);

    /* エラー割り込みの計数 */
    CAN_HandleTypeDef h = {0};
    h.ErrorCode = HAL_CAN_ERROR_EPV | HAL_CAN_ERROR_RX_FOV0;
    HAL_CAN_ErrorCallback(&h);
    CHECK(stats().err_events == 1u);
    CHECK(stats().err_passive == 1u);
    CHECK(stats().err_busoff == 0u);
    CHECK(stats().err_rx_overrun == 1u);
    CHECK(stats().err_last_code == (HAL_CAN_ERROR_EPV | HAL_CAN_ERROR_RX_FOV0));
    CHECK(h.ErrorCode == HAL_CAN_ERROR_NONE);   /* 次の判定が累積しないようにクリアする */
    h.ErrorCode = HAL_CAN_ERROR_BOF;
    HAL_CAN_ErrorCallback(&h);
    CHECK(stats().err_events == 2u && stats().err_busoff == 1u && stats().err_passive == 1u);

    /* comm_bus_degraded：エラーパッシブまたはバスオフ（LED_COMM 点滅、FR-UI-04） */
    g_can1.ESR = 0;
    CHECK(!comm_bus_degraded());
    g_can1.ESR = CAN_ESR_EPVF;
    CHECK(comm_bus_degraded());
    g_can1.ESR = CAN_ESR_BOFF;
    CHECK(comm_bus_degraded());
    g_can1.ESR = 0;
}

/* ---------------------------------------------------------------- comm_diag */

static void test_diag(void)
{
    /* 初期化の成否 */
    g_fail_init = 1;
    comm_init(0);
    CHECK(comm_diag.init_stage == COMM_INIT_FAIL_CAN_INIT);
    g_fail_init = 0;
    g_fail_filter = 1;
    comm_init(0);
    CHECK(comm_diag.init_stage == COMM_INIT_FAIL_FILTER);
    g_fail_filter = 0;
    g_fail_start = 1;
    comm_init(0);
    CHECK(comm_diag.init_stage == COMM_INIT_FAIL_START);
    g_fail_start = 0;

    setup(0);
    CHECK(comm_diag.init_stage == COMM_INIT_RUNNING);
    memset((void *)&comm_diag, 0, sizeof comm_diag);
    comm_diag.init_stage = COMM_INIT_RUNNING;
    g_can1.ESR = 0;
    comm_poll();   /* EWGF の前回値を 0 に揃える */

    /* TEC / REC / LEC。LEC=3（ACK）を検出したら ESR の LEC を 7 にする */
    g_can1.ESR = (5u << CAN_ESR_REC_Pos) | (9u << CAN_ESR_TEC_Pos) | (3u << CAN_ESR_LEC_Pos);
    comm_poll();
    CHECK(comm_diag.tec == 9u && comm_diag.rec == 5u);
    CHECK(comm_diag.tec_max == 9u && comm_diag.rec_max == 5u);
    CHECK(comm_diag.esr == ((5u << CAN_ESR_REC_Pos) | (9u << CAN_ESR_TEC_Pos) | (3u << CAN_ESR_LEC_Pos)));
    CHECK(comm_diag.lec_count[2] == 1u);
    CHECK((g_can1.ESR & CAN_ESR_LEC) == CAN_ESR_LEC);
    comm_poll();   /* LEC=7 のままなら数え直さない */
    CHECK(comm_diag.lec_count[2] == 1u);

    /* max は下がらない。LEC 0 は数えない。他の LEC も数える */
    g_can1.ESR = (2u << CAN_ESR_REC_Pos) | (1u << CAN_ESR_TEC_Pos);
    comm_poll();
    CHECK(comm_diag.tec == 1u && comm_diag.rec == 2u);
    CHECK(comm_diag.tec_max == 9u && comm_diag.rec_max == 5u);
    for (uint32_t lec = 1; lec <= 6u; lec++) {
        g_can1.ESR = lec << CAN_ESR_LEC_Pos;
        comm_poll();
    }
    CHECK(comm_diag.lec_count[0] == 1u && comm_diag.lec_count[1] == 1u && comm_diag.lec_count[2] == 2u);
    CHECK(comm_diag.lec_count[3] == 1u && comm_diag.lec_count[4] == 1u && comm_diag.lec_count[5] == 1u);

    /* EWGF の立ち上がり（0→1→1 で 1 回） */
    g_can1.ESR = 0;
    comm_poll();
    uint32_t ewg0 = comm_diag.ewg_count;
    g_can1.ESR = CAN_ESR_EWGF;
    comm_poll();
    CHECK(comm_diag.ewg_count == ewg0 + 1u);
    comm_poll();
    CHECK(comm_diag.ewg_count == ewg0 + 1u);
    g_can1.ESR = 0;
    comm_poll();
    g_can1.ESR = CAN_ESR_EWGF;
    comm_poll();
    CHECK(comm_diag.ewg_count == ewg0 + 2u);
    g_can1.ESR = 0;

    /* 直近の受信フレーム */
    const uint8_t d[3] = {COMM_OP_READ, 0x20, 1};
    rx(0x100, 3, d);
    CHECK(comm_diag.last_rx_id == 0x100u && comm_diag.last_rx_dlc == 3u);
    CHECK(comm_diag.last_rx_data[0] == COMM_OP_READ && comm_diag.last_rx_data[1] == 0x20u &&
          comm_diag.last_rx_data[2] == 1u);
}

/* ---------------------------------------------------------------- 実行 */

int main(void)
{
    printf("test_comm: comm.c host tests\n");
    RUN(test_init);
    RUN(test_id_filter);
    RUN(test_ext_and_rtr_ignored);
    RUN(test_write);
    RUN(test_read);
    RUN(test_invalid_request);
    RUN(test_broadcast);
    RUN(test_fault_notify);
    RUN(test_tx_mailbox_full);
    RUN(test_timeout_timer);
    RUN(test_led_activity);
    RUN(test_process_request_direct);
    RUN(test_rx_ring_order_and_capacity);
    RUN(test_rx_ring_16);
    RUN(test_rx_ring_overflow);
    RUN(test_stats);
    RUN(test_diag);

    printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
