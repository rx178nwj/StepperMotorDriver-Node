#include "usb_proto.h"
#include "app.h"
#include "config.h"
#include "settings.h"
#include "symtab.h"
#include "usbd_cdc_if.h"
#include <string.h>

/* ---------------------------------------------------------------- 定数 */

#define RX_RING_SIZE      1024u    /* 2 のべき乗。最大フレーム（254 B）の 4 つ分 */
#define RX_RING_MASK      (RX_RING_SIZE - 1u)
#define RX_TIMEOUT_MS     20u      /* フレーム途中でこれ以上途切れたら捨てる（FR-USB-11） */
#define TX_TIMEOUT_MS     50u      /* 送信できない状態がこれ以上続いたら捨てる（FR-USB-05） */
#define FRAME_OVERHEAD    4u       /* SOF + LEN + CRC16 */
#define FRAME_BUF_SIZE    (USB_LEN_MAX + FRAME_OVERHEAD)

volatile usb_stats_t usb_stats;

/* ---------------------------------------------------------------- CRC16 */

uint16_t usb_crc16(const uint8_t *data, uint32_t len)
{
    uint16_t crc = 0xFFFFu;
    while (len--) {
        crc ^= (uint16_t)((uint16_t)*data++ << 8);
        for (int k = 0; k < 8; k++) {
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

/* ---------------------------------------------------------------- メモリ読み出し（FR-USB-31〜33） */

typedef struct {
    uint32_t start;
    uint32_t end;   /* 含む */
} mem_region_t;

static const mem_region_t regions[] = {
    { 0x08000000u, 0x0803FFFFu },   /* Flash */
    { 0x20000000u, 0x2000BFFFu },   /* SRAM1 */
    { 0x2000C000u, 0x2000FFFFu },   /* SRAM2 */
    { 0x10000000u, 0x10003FFFu },   /* SRAM2 別名 */
    { 0x1FFF7000u, 0x1FFF73FFu },   /* OTP */
    { 0x1FFF7500u, 0x1FFF75FFu },   /* UID・パッケージ・Flash サイズ */
};

bool usb_mem_allowed(uint32_t addr, uint32_t size)
{
    if (size == 0u) {
        return false;
    }
    for (uint32_t i = 0; i < sizeof regions / sizeof regions[0]; i++) {
        const mem_region_t *r = &regions[i];
        if (addr >= r->start && addr <= r->end) {
            /* addr + size が 32bit を越えないよう、残りバイト数と比べる */
            return size <= (r->end - addr) + 1u;
        }
    }
    return false;
}

/* ホスト単体テスト（tests/host）は実アドレスを読めないため、-D で差し替える。実機では直接読む */
#ifndef USB_MEM_RD8
#define USB_MEM_RD8(a)   (*(volatile const uint8_t *)(uintptr_t)(a))
#define USB_MEM_RD32(a)  (*(volatile const uint32_t *)(uintptr_t)(a))
#endif

/*
 * 4 バイト境界に揃った部分は 32bit 単位、端は 8bit で読む（FR-USB-32）。
 * 読み出しごとに nv_ecc_error を確認し、立っていたら以降を読まずに中止する（FR-USB-33）。
 */
static void mem_copy(uint8_t *dst, uint32_t addr, uint32_t n)
{
    while (n > 0u && (addr & 3u) != 0u) {
        *dst++ = USB_MEM_RD8(addr);
        if (nv_ecc_error) {
            return;
        }
        addr++;
        n--;
    }
    while (n >= 4u) {
        uint32_t w = USB_MEM_RD32(addr);
        dst[0] = (uint8_t)w;
        dst[1] = (uint8_t)(w >> 8);
        dst[2] = (uint8_t)(w >> 16);
        dst[3] = (uint8_t)(w >> 24);
        if (nv_ecc_error) {
            return;
        }
        dst += 4;
        addr += 4u;
        n -= 4u;
    }
    while (n > 0u) {
        *dst++ = USB_MEM_RD8(addr);
        if (nv_ecc_error) {
            return;
        }
        addr++;
        n--;
    }
}

/*
 * 許可済みアドレスから読む。Flash の書き込み途中ページを読んで ECC エラー（NMI）になった場合は
 * NMI ハンドラが nv_ecc_error を立てて戻るので、その時点で読み出しを中止し、
 * USB_ERR_NO_RESOURCE を返す（リセットしない。FR-USB-33）。
 * atomic = true なら割り込みを禁止してコピーする（8 バイト以下の変数用。禁止時間は 1 µs 以下、FR-USB-38）。
 */
static uint8_t mem_read(uint8_t *dst, uint32_t addr, uint32_t size, bool atomic)
{
    nv_ecc_error = false;
    if (atomic) {
        uint32_t pm = __get_PRIMASK();
        __set_PRIMASK(1u);
        mem_copy(dst, addr, size);
        __set_PRIMASK(pm);
    } else {
        mem_copy(dst, addr, size);
    }
    return nv_ecc_error ? USB_ERR_NO_RESOURCE : USB_ERR_OK;
}

/* ---------------------------------------------------------------- build-id（FR-USB-34） */

/* リンカスクリプトが .note.gnu.build-id の範囲に定義するシンボル */
extern const uint8_t __build_id_start[];
extern const uint8_t __build_id_end[];

static uint32_t le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* ELF ノート [namesz][descsz][type]["GNU\0"][desc] を解析して SHA1 20 バイトの位置を返す。なければ NULL */
static const uint8_t *build_id_find(void)
{
    uint32_t total = (uint32_t)(__build_id_end - __build_id_start);
    if (total < 16u + USB_BUILD_ID_LEN) {
        return NULL;
    }
    const uint8_t *p = __build_id_start;
    uint32_t namesz = le32(&p[0]);
    uint32_t descsz = le32(&p[4]);
    uint32_t type = le32(&p[8]);
    if (namesz != 4u || descsz != USB_BUILD_ID_LEN || type != 3u /* NT_GNU_BUILD_ID */ ||
        memcmp(&p[12], "GNU", 4) != 0) {
        return NULL;
    }
    return &p[16];
}

/* ---------------------------------------------------------------- 受信（割り込み → リング → main） */

static uint8_t rx_ring[RX_RING_SIZE];
static volatile uint16_t rx_head;          /* ISR が書く */
static volatile uint16_t rx_tail;          /* main が書く */
static volatile uint32_t rx_last_ms;       /* 最後にバイトを受けた時刻（ISR が書く） */
static volatile bool bus_event;            /* バスリセット等が起きた（main が受信状態を初期化する） */
static volatile bool session;              /* CONNECT 済み */

void usb_proto_rx_isr(const uint8_t *buf, uint32_t len)
{
    uint16_t head = rx_head;
    uint32_t lost = 0;

    for (uint32_t i = 0; i < len; i++) {
        uint16_t next = (uint16_t)((head + 1u) & RX_RING_MASK);
        if (next == rx_tail) {
            lost++;
            continue;
        }
        rx_ring[head] = buf[i];
        head = next;
    }
    rx_head = head;
    rx_last_ms = HAL_GetTick();
    if (lost != 0u) {
        usb_stats.rx_overflow += lost;
        usb_stats.dropped++;
    }
}

void usb_proto_on_bus_event(void)
{
    session = false;
    bus_event = true;
}

typedef enum { RX_SOF, RX_LEN, RX_DATA } rx_state_t;

static rx_state_t rx_state;
static uint8_t frame[FRAME_BUF_SIZE];      /* [LEN][BODY][CRC lo][CRC hi] */
static uint16_t frame_pos;
static bool frame_ready;                   /* frame[] に CRC まで正しいフレームがある */

static bool ring_empty(void)
{
    return rx_head == rx_tail;
}

/* リングのバイトを状態機械へ流し、1 フレーム揃ったら frame_ready にして戻る */
static void rx_parse(void)
{
    while (!frame_ready && !ring_empty()) {
        uint8_t b = rx_ring[rx_tail];
        rx_tail = (uint16_t)((rx_tail + 1u) & RX_RING_MASK);

        switch (rx_state) {
        case RX_SOF:
            if (b == USB_FRAME_SOF) {
                rx_state = RX_LEN;
            }
            break;

        case RX_LEN:
            if (b >= USB_LEN_MIN && b <= USB_LEN_MAX) {
                frame[0] = b;
                frame_pos = 1;
                rx_state = RX_DATA;
            } else {
                /* LEN 範囲外: この LEN の位置から SOF を探し直す（この b 自身が SOF なら次は LEN） */
                usb_stats.bad_len++;
                rx_state = (b == USB_FRAME_SOF) ? RX_LEN : RX_SOF;
            }
            break;

        default: /* RX_DATA */
            frame[frame_pos++] = b;
            if (frame_pos == (uint16_t)(frame[0] + 3u)) {   /* LEN + BODY + CRC 2 */
                uint16_t calc = usb_crc16(frame, (uint32_t)frame[0] + 1u);
                uint16_t got = (uint16_t)(frame[frame[0] + 1u] | (frame[frame[0] + 2u] << 8));
                rx_state = RX_SOF;
                if (calc == got) {
                    usb_stats.rx_frames++;
                    frame_ready = true;
                } else {
                    usb_stats.crc_errors++;   /* 応答せずに捨てる */
                }
            }
            break;
        }
    }
}

/* ---------------------------------------------------------------- 送信 */

typedef enum { TX_FREE, TX_PENDING, TX_INFLIGHT } tx_state_t;

static uint8_t tx_buf[FRAME_BUF_SIZE];     /* USB ミドルウェアが送り終えるまで書き換えない */
static uint16_t tx_len;
static tx_state_t tx_state;
static uint32_t tx_t0;                     /* PENDING / INFLIGHT になった時刻 */

static volatile bool boot_request;         /* ENTER_BOOTLOADER を受け付けた（app_main が取り出して移行する） */

static bool pending_event;
static comm_frame_t event_frame;
static uint32_t event_t0;

extern USBD_HandleTypeDef hUsbDeviceFS;

static bool usb_link_ready(void)
{
    return hUsbDeviceFS.dev_state == USBD_STATE_CONFIGURED && hUsbDeviceFS.pClassData != NULL;
}

/* USB ミドルウェア側で直前の送信が終わっているか（未接続なら終わっているとみなす） */
static bool usb_tx_done(void)
{
    USBD_CDC_HandleTypeDef *hcdc = (USBD_CDC_HandleTypeDef *)hUsbDeviceFS.pClassData;
    return hcdc == NULL || hcdc->TxState == 0u;
}

static void tx_drop(void)
{
    usb_stats.tx_dropped++;
    usb_stats.dropped++;
}

/* 送信スロットの状態を進める。ブロックしない */
static void tx_service(uint32_t now)
{
    if (tx_state == TX_INFLIGHT && usb_tx_done()) {
        tx_state = TX_FREE;
    }
    if (tx_state != TX_PENDING) {
        return;
    }
    if (usb_link_ready() && CDC_Transmit_FS(tx_buf, tx_len) == USBD_OK) {
        usb_stats.tx_frames++;
        tx_state = TX_INFLIGHT;
        tx_t0 = now;
        return;
    }
    if ((int32_t)(now - tx_t0) >= (int32_t)TX_TIMEOUT_MS) {
        tx_state = TX_FREE;   /* 50 ms 送れなかった応答・イベントは破棄（FR-USB-05） */
        tx_drop();
    }
}

/* 送信を 50 ms 以上始められないまま塞がっている（ホストが読み出さない） */
static bool tx_stuck(uint32_t now)
{
    return tx_state == TX_INFLIGHT && (int32_t)(now - tx_t0) >= (int32_t)TX_TIMEOUT_MS;
}

/* body（len バイト）をフレームにして送信スロットへ。スロットが空いていなければ呼ばないこと */
static void tx_queue(const uint8_t *body, uint8_t len, uint32_t now)
{
    tx_buf[0] = USB_FRAME_SOF;
    tx_buf[1] = len;
    memcpy(&tx_buf[2], body, len);
    uint16_t crc = usb_crc16(&tx_buf[1], (uint32_t)len + 1u);
    tx_buf[2 + len] = (uint8_t)crc;
    tx_buf[3 + len] = (uint8_t)(crc >> 8);
    tx_len = (uint16_t)(len + FRAME_OVERHEAD);
    tx_t0 = now;
    tx_state = TX_PENDING;
}

void usb_proto_notify_can_frame(const comm_frame_t *f)
{
    if (!session) {
        return;
    }
    if (pending_event) {
        tx_drop();   /* 未送信のイベントを新しいイベントで上書きする */
    }
    event_frame = *f;
    event_t0 = HAL_GetTick();
    pending_event = true;
}

/* ---------------------------------------------------------------- コマンド処理 */

static uint32_t mta;   /* メモリ転送アドレス */

static void put16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static void put32(uint8_t *p, uint32_t v)
{
    put16(p, (uint16_t)v);
    put16(p + 2, (uint16_t)(v >> 16));
}

/* アドレス指定の読み出し（UPLOAD / SHORT_UP 共通）。成功したら size バイトを out へ */
static uint8_t cmd_read_mem(uint8_t size, uint32_t addr, uint8_t *out)
{
    if (size < 1u || size > USB_UPLOAD_MAX) {
        return USB_ERR_RANGE;
    }
    if (!usb_mem_allowed(addr, size)) {
        return USB_ERR_DENIED;
    }
    return mem_read(out, addr, size, false);
}

static uint8_t cmd_sym_info(const uint8_t *req, uint8_t len, uint8_t *out, uint8_t *dlen)
{
    if (len != 4u) {
        return USB_ERR_SYNTAX;
    }
    const sym_entry_t *e = symtab_get((uint16_t)(req[2] | (req[3] << 8)));
    if (e == NULL) {
        return USB_ERR_RANGE;
    }
    uint8_t nlen = symtab_name_len(e);
    put32(out, (uint32_t)(uintptr_t)e->addr);
    out[4] = e->type;
    put16(&out[5], e->num);
    out[7] = nlen;
    memcpy(&out[8], e->name, nlen);
    *dlen = (uint8_t)(8u + nlen);
    return USB_ERR_OK;
}

/* [NLEN][NAME] を検査して名前表の INDEX を返す。base = NLEN の位置。tail = NAME の後ろに続くバイト数 */
static uint8_t sym_lookup(const uint8_t *req, uint8_t len, uint8_t base, uint8_t tail, int32_t *index)
{
    if (len < base + 1u) {
        return USB_ERR_SYNTAX;
    }
    uint8_t nlen = req[base];
    if (len != base + 1u + nlen + tail) {
        return USB_ERR_SYNTAX;
    }
    if (nlen < 1u || nlen > SYM_NAME_MAX) {
        return USB_ERR_RANGE;
    }
    int32_t i = symtab_find(&req[base + 1u], nlen);
    if (i < 0) {
        return USB_ERR_NO_RESOURCE;
    }
    *index = i;
    return USB_ERR_OK;
}

static uint8_t cmd_sym_find(const uint8_t *req, uint8_t len, uint8_t *out, uint8_t *dlen)
{
    int32_t i;
    uint8_t err = sym_lookup(req, len, 2, 0, &i);
    if (err != USB_ERR_OK) {
        return err;
    }
    const sym_entry_t *e = symtab_get((uint16_t)i);
    put16(out, (uint16_t)i);
    put32(&out[2], (uint32_t)(uintptr_t)e->addr);
    out[6] = e->type;
    put16(&out[7], e->num);
    *dlen = 9;
    return USB_ERR_OK;
}

static uint8_t cmd_sym_upload(const uint8_t *req, uint8_t len, uint8_t *out, uint8_t *dlen)
{
    int32_t i;
    uint8_t err = sym_lookup(req, len, 2, 3, &i);
    if (err != USB_ERR_OK) {
        return err;
    }
    const uint8_t *p = &req[3u + req[2]];
    uint32_t offset = (uint32_t)(p[0] | (p[1] << 8));
    uint32_t size = p[2];

    const sym_entry_t *e = symtab_get((uint16_t)i);
    uint32_t total = symtab_size(e);
    if (offset >= total) {
        return USB_ERR_RANGE;
    }
    if (size == 0u) {
        size = total - offset;   /* SIZE=0: OFFSET から変数の最後まで（OFFSET=0 なら変数全体） */
    }
    if (size > USB_UPLOAD_MAX || offset + size > total) {
        return USB_ERR_RANGE;
    }
    err = mem_read(out, (uint32_t)(uintptr_t)e->addr + offset, size, total <= 8u);
    if (err == USB_ERR_OK) {
        *dlen = (uint8_t)size;
    }
    return err;
}

static uint8_t cmd_can_request(const uint8_t *req, uint8_t len, uint8_t *out, uint8_t *dlen)
{
    if (len < 5u) {
        return USB_ERR_SYNTAX;
    }
    uint16_t can_id = (uint16_t)(req[2] | (req[3] << 8));
    uint8_t dlc = req[4];
    if (can_id > 0x7FFu || dlc > 8u) {
        return USB_ERR_RANGE;   /* CAN の処理系には渡さない（FR-USB-41） */
    }
    if (len != 5u + dlc) {
        return USB_ERR_SYNTAX;
    }

    comm_frame_t resp[COMM_RESP_MAX];
    uint8_t n = comm_process_request(can_id, dlc, &req[5], resp, COMM_RESP_MAX);

    uint8_t pos = 0;
    out[pos++] = n;
    for (uint8_t k = 0; k < n; k++) {
        put16(&out[pos], resp[k].id);
        out[pos + 2] = resp[k].dlc;
        memcpy(&out[pos + 3], resp[k].data, resp[k].dlc);
        pos = (uint8_t)(pos + 3u + resp[k].dlc);
    }
    *dlen = pos;
    return USB_ERR_OK;
}

/*
 * 要求 1 つを実行する。req = BODY（[CMD][CTR][PARAM…]）、len = BODY 長（2 以上）。
 * 戻り値は ERR。成功時は out に DATA を作り *dlen に長さを入れる（ERR≠0 なら DATA なし）。
 */
static uint8_t exec_command(const uint8_t *req, uint8_t len, uint8_t *out, uint8_t *dlen)
{
    uint8_t cmd = req[0];
    *dlen = 0;

    if (cmd == USB_CMD_GET_VERSION) {
        if (len != 2u) return USB_ERR_SYNTAX;
        out[0] = USB_PROTO_MAJOR;
        out[1] = USB_PROTO_MINOR;
        *dlen = 2;
        return USB_ERR_OK;
    }
    if (cmd == USB_CMD_CONNECT) {
        if (len != 2u) return USB_ERR_SYNTAX;
        session = true;
        mta = 0;
        out[0] = USB_PROTO_MAJOR;
        out[1] = USB_PROTO_MINOR;
        out[2] = DEVICE_ID;
        out[3] = FW_VERSION_MAJOR;
        out[4] = FW_VERSION_MINOR;
        out[5] = comm_board_id();
        out[6] = USB_LEN_MAX;
        *dlen = 7;
        return USB_ERR_OK;
    }
    if (!session) {
        return USB_ERR_LOCKED;   /* CONNECT・GET_VERSION 以外は接続前に受け付けない */
    }

    switch (cmd) {
    case USB_CMD_DISCONNECT:
        if (len != 2u) return USB_ERR_SYNTAX;
        session = false;         /* 応答は送る。以後イベントは送らない */
        pending_event = false;
        return USB_ERR_OK;

    case USB_CMD_EXCHANGE_ID: {
        if (len != 2u) return USB_ERR_SYNTAX;
        const uint8_t *id = build_id_find();
        if (id == NULL) return USB_ERR_NO_RESOURCE;
        out[0] = USB_BUILD_ID_LEN;
        memcpy(&out[1], id, USB_BUILD_ID_LEN);
        *dlen = 1u + USB_BUILD_ID_LEN;
        return USB_ERR_OK;
    }

    case USB_CMD_SET_MTA:
        if (len != 6u) return USB_ERR_SYNTAX;
        mta = le32(&req[2]);
        return USB_ERR_OK;

    case USB_CMD_UPLOAD: {
        if (len != 3u) return USB_ERR_SYNTAX;
        uint8_t size = req[2];
        uint8_t err = cmd_read_mem(size, mta, out);
        if (err == USB_ERR_OK) {
            mta += size;
            *dlen = size;
        }
        return err;
    }

    case USB_CMD_SHORT_UP: {
        if (len != 7u) return USB_ERR_SYNTAX;
        uint8_t size = req[2];
        uint8_t err = cmd_read_mem(size, le32(&req[3]), out);
        if (err == USB_ERR_OK) {
            *dlen = size;
        }
        return err;
    }

    case USB_CMD_SYM_COUNT:
        if (len != 2u) return USB_ERR_SYNTAX;
        put16(out, symtab_count());
        *dlen = 2;
        return USB_ERR_OK;

    case USB_CMD_SYM_INFO:
        return cmd_sym_info(req, len, out, dlen);

    case USB_CMD_SYM_FIND:
        return cmd_sym_find(req, len, out, dlen);

    case USB_CMD_SYM_UPLOAD:
        return cmd_sym_upload(req, len, out, dlen);

    case USB_CMD_CAN_REQUEST:
        return cmd_can_request(req, len, out, dlen);

    case USB_CMD_ENTER_BOOTLOADER:
        /* 未接続（0x35）は上の共通処理。ここでは PARAM 長 → KEY → ENABLED の順に判定する（FR-BL-01・02） */
        if (len != 6u) return USB_ERR_SYNTAX;
        if (le32(&req[2]) != BOOT_KEY) return USB_ERR_RANGE;
        if (app_get_state() == APP_ENABLED) return USB_ERR_DENIED;
        boot_request = true;   /* ACK は通常どおり返る。移行は app_main_loop が送信完了を待ってから行う */
        return USB_ERR_OK;

    default:
        return USB_ERR_UNKNOWN_CMD;
    }
}

/* ---------------------------------------------------------------- メインループ */

/* 受信済みの 1 フレームを実行して応答を積む。送信スロットが空いているときだけ呼ぶ（応答を送れない要求は実行しない） */
static void handle_frame(uint32_t now)
{
    const uint8_t *req = &frame[1];
    uint8_t len = frame[0];

    uint8_t rsp[USB_LEN_MAX];
    uint8_t dlen = 0;
    uint8_t err = exec_command(req, len, &rsp[3], &dlen);

    rsp[0] = USB_PID_RESPONSE;
    rsp[1] = err;
    rsp[2] = req[1];   /* CTR をそのまま返す */
    if (err != USB_ERR_OK) {
        dlen = 0;
    }

    tx_queue(rsp, (uint8_t)(3u + dlen), now);
}

static void send_pending_event(uint32_t now)
{
    if (!pending_event) {
        return;
    }
    if (!session) {
        pending_event = false;
        return;
    }
    if (tx_state == TX_FREE) {
        uint8_t body[2 + 3 + 8];
        body[0] = USB_PID_EVENT;
        body[1] = USB_EVT_CAN_FRAME;
        put16(&body[2], event_frame.id);
        body[4] = event_frame.dlc;
        memcpy(&body[5], event_frame.data, event_frame.dlc);
        tx_queue(body, (uint8_t)(5u + event_frame.dlc), now);
        pending_event = false;
    } else if ((int32_t)(now - event_t0) >= (int32_t)TX_TIMEOUT_MS) {
        pending_event = false;   /* 50 ms 送れなかったイベントは破棄 */
        tx_drop();
    }
}

bool usb_proto_take_bootloader_request(void)
{
    bool r = boot_request;
    boot_request = false;
    return r;
}

bool usb_proto_tx_idle(void)
{
    tx_service(HAL_GetTick());
    return tx_state == TX_FREE;
}

void usb_proto_poll(void)
{
    uint32_t now = HAL_GetTick();

    if (bus_event) {
        /* バスリセット・サスペンド: 受信途中の状態と未送信の応答・イベントを捨てる */
        bus_event = false;
        rx_tail = rx_head;
        rx_state = RX_SOF;
        frame_ready = false;
        pending_event = false;
        if (tx_state == TX_PENDING) {
            tx_state = TX_FREE;
        }
    }

    tx_service(now);

    rx_parse();
    if (frame_ready && tx_stuck(now)) {
        /* 50 ms 以上塞がっている（ホストが読み出さない）。応答を送れないので要求は実行せず捨てる。PC は再送する */
        frame_ready = false;
        tx_drop();
        rx_parse();
    } else if (frame_ready && tx_state == TX_FREE) {
        handle_frame(now);
        frame_ready = false;
        tx_service(now);   /* 受信完了から 10 ms 以内に送信を始める（FR-USB-06） */
        rx_parse();        /* 次のフレームは次回以降に処理する（frame_ready を立てて戻る） */
    } else if (!frame_ready && rx_state != RX_SOF && ring_empty() &&
               (int32_t)(now - rx_last_ms) >= (int32_t)RX_TIMEOUT_MS) {
        rx_state = RX_SOF;   /* フレーム途中で 20 ms 途切れた */
        usb_stats.rx_timeouts++;
        usb_stats.dropped++;
    }

    send_pending_event(now);
    tx_service(now);
}

/* ---------------------------------------------------------------- USB 名前表（USB 統計） */

SYM_ENTRY(usb_stats, SYM_BYTES, sizeof(usb_stats));
SYM_ENTRY(usb_stats.rx_frames, SYM_U32, 1);
SYM_ENTRY(usb_stats.crc_errors, SYM_U32, 1);
SYM_ENTRY(usb_stats.dropped, SYM_U32, 1);
SYM_ENTRY(usb_stats.rx_overflow, SYM_U32, 1);
SYM_ENTRY(usb_stats.rx_timeouts, SYM_U32, 1);
SYM_ENTRY(usb_stats.bad_len, SYM_U32, 1);
SYM_ENTRY(usb_stats.tx_frames, SYM_U32, 1);
SYM_ENTRY(usb_stats.tx_dropped, SYM_U32, 1);
