/*
 * usb_proto.c / symtab.c のホスト単体テスト（要求仕様 6 章 試験 17〜21 のうちホストで検証できるもの）。
 * 素の C アサーションのみ。ビルドと実行は run_tests.ps1。
 */
#include "config.h"
#include "test_stubs.h"
#include "usb_proto.h"
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

/* ---------------------------------------------------------------- ヘルパー */

static void put16le(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static void put32le(uint8_t *p, uint32_t v)
{
    put16le(p, v);
    put16le(p + 2, v >> 16);
}

/* body（len バイト）を A5 フレームにして buf へ。全長を返す */
static unsigned build_frame(uint8_t *buf, const uint8_t *body, uint8_t len)
{
    buf[0] = USB_FRAME_SOF;
    buf[1] = len;
    memcpy(&buf[2], body, len);
    uint16_t crc = usb_crc16(&buf[1], (uint32_t)len + 1u);
    buf[2 + len] = (uint8_t)crc;
    buf[3 + len] = (uint8_t)(crc >> 8);
    return (unsigned)len + 4u;
}

/* 要求 [CMD][CTR][PARAM…] のフレームを作る */
static unsigned build_req(uint8_t *buf, uint8_t cmd, uint8_t ctr, const uint8_t *p, uint8_t plen)
{
    uint8_t body[USB_LEN_MAX];
    body[0] = cmd;
    body[1] = ctr;
    if (plen > 0u) {
        memcpy(&body[2], p, plen);
    }
    return build_frame(buf, body, (uint8_t)(2u + plen));
}

static void feed_req(uint8_t cmd, uint8_t ctr, const uint8_t *p, uint8_t plen)
{
    uint8_t buf[USB_LEN_MAX + 4];
    unsigned n = build_req(buf, cmd, ctr, p, plen);
    usb_proto_rx_isr(buf, n);
}

/* 要求を送って poll し、応答フレームを返す（なければ NULL）。直前の送信は完了させておく */
static const uint8_t *req(uint8_t cmd, uint8_t ctr, const uint8_t *p, uint8_t plen)
{
    test_tx_complete();
    unsigned before = g_sent_n;
    feed_req(cmd, ctr, p, plen);
    usb_proto_poll();
    return (g_sent_n == before + 1u) ? g_sent[g_sent_n - 1u].bytes : NULL;
}

/* 応答フレーム f が [FF][err][ctr][data] で CRC も正しいことを検査 */
static void expect(const uint8_t *f, uint8_t err, uint8_t ctr, const uint8_t *data, uint8_t dlen)
{
    CHECK(f != NULL);
    if (f == NULL) {
        return;
    }
    CHECK(f[0] == USB_FRAME_SOF);
    CHECK(f[1] == 3u + dlen);
    CHECK(f[2] == USB_PID_RESPONSE);
    CHECK(f[3] == err);
    CHECK(f[4] == ctr);
    if (dlen > 0u) {
        CHECK(memcmp(&f[5], data, dlen) == 0);
    }
    uint16_t crc = usb_crc16(&f[1], (uint32_t)f[1] + 1u);
    CHECK(f[2 + f[1]] == (uint8_t)crc);
    CHECK(f[3 + f[1]] == (uint8_t)(crc >> 8));
}

static void expect_err(const uint8_t *f, uint8_t err, uint8_t ctr)
{
    expect(f, err, ctr, NULL, 0);
}

static void expect_pattern(const uint8_t *f, uint8_t ctr, uint32_t addr, uint8_t size)
{
    uint8_t exp[USB_UPLOAD_MAX];
    for (uint8_t i = 0; i < size; i++) {
        exp[i] = test_pattern(addr + i);
    }
    expect(f, USB_ERR_OK, ctr, exp, size);
}

/* 全状態を初期化する（切断・受信破棄・送信スロット解放・統計クリア・スタブ初期化） */
static void reset(void)
{
    g_tick += 100u;
    test_tx_complete();
    usb_proto_on_bus_event();
    usb_proto_poll();
    test_stubs_reset();
    memset((void *)&usb_stats, 0, sizeof usb_stats);

    /* build-id ノート: namesz=4, descsz=20, type=3, "GNU\0", SHA1 */
    uint8_t *n = test_build_id_note();
    put32le(&n[0], 4u);
    put32le(&n[4], 20u);
    put32le(&n[8], 3u);
    memcpy(&n[12], "GNU", 4);
    for (uint8_t i = 0; i < 20u; i++) {
        n[16 + i] = (uint8_t)(0x10u + i);
    }

    /* 名前表 */
    static const sym_entry_t tbl[TEST_SYM_N] = {
        { "state", (const void *)(uintptr_t)0x20000010u, 1, SYM_U8,    0 },
        { "speed", (const void *)(uintptr_t)0x20000100u, 1, SYM_I32,   0 },
        { "buf",   (const void *)(uintptr_t)0x20000200u, 16, SYM_BYTES, 0 },
        { "arr",   (const void *)(uintptr_t)0x20000300u, 4, SYM_U16,   0 },
    };
    test_sym_set(tbl);
}

static void connect(void)
{
    const uint8_t *r = req(USB_CMD_CONNECT, 0x01, NULL, 0);
    CHECK(r != NULL);
}

static void set_mta(uint32_t addr, uint8_t ctr)
{
    uint8_t p[4];
    put32le(p, addr);
    expect_err(req(USB_CMD_SET_MTA, ctr, p, 4), USB_ERR_OK, ctr);
}

static const uint8_t *sym_find_req(const char *name, uint8_t ctr)
{
    uint8_t p[40];
    uint8_t n = (uint8_t)strlen(name);
    p[0] = n;
    memcpy(&p[1], name, n);
    return req(USB_CMD_SYM_FIND, ctr, p, (uint8_t)(1u + n));
}

static const uint8_t *sym_upload_req(const char *name, uint16_t off, uint8_t size, uint8_t ctr)
{
    uint8_t p[40];
    uint8_t n = (uint8_t)strlen(name);
    p[0] = n;
    memcpy(&p[1], name, n);
    put16le(&p[1 + n], off);
    p[3 + n] = size;
    return req(USB_CMD_SYM_UPLOAD, ctr, p, (uint8_t)(4u + n));
}

/* ---------------------------------------------------------------- 試験 18: CRC・フレーム受信 */

static void test_crc16(void)
{
    CHECK(usb_crc16((const uint8_t *)"123456789", 9) == 0x29B1u);
    CHECK(usb_crc16((const uint8_t *)"", 0) == 0xFFFFu);
}

static void test_rx_bad_crc(void)
{
    reset();
    uint8_t body[2] = { USB_CMD_GET_VERSION, 0x11 };
    uint8_t buf[8];
    unsigned n = build_frame(buf, body, 2);
    buf[n - 1] ^= 0x01u;   /* CRC 上位を壊す */
    usb_proto_rx_isr(buf, n);
    usb_proto_poll();
    CHECK(g_sent_n == 0u);
    CHECK(usb_stats.crc_errors == 1u);
    CHECK(usb_stats.rx_frames == 0u);

    /* 次の正しいフレームには応答する */
    const uint8_t ok[2] = { USB_PROTO_MAJOR, USB_PROTO_MINOR };
    expect(req(USB_CMD_GET_VERSION, 0x12, NULL, 0), USB_ERR_OK, 0x12, ok, 2);
    CHECK(usb_stats.rx_frames == 1u);
}

static void test_rx_bad_len(void)
{
    reset();
    /* LEN=0、LEN=1、LEN=251 は範囲外。SOF の次のバイトから探し直す */
    const uint8_t noise[] = { USB_FRAME_SOF, 0x00, USB_FRAME_SOF, 0x01, USB_FRAME_SOF, 0xFB, 0x77 };
    usb_proto_rx_isr(noise, sizeof noise);
    usb_proto_poll();
    CHECK(g_sent_n == 0u);
    CHECK(usb_stats.bad_len == 3u);

    const uint8_t ok[2] = { USB_PROTO_MAJOR, USB_PROTO_MINOR };
    expect(req(USB_CMD_GET_VERSION, 0x21, NULL, 0), USB_ERR_OK, 0x21, ok, 2);
}

static void test_rx_timeout(void)
{
    reset();
    uint8_t buf[8];
    unsigned n = build_req(buf, USB_CMD_GET_VERSION, 0x31, NULL, 0);
    usb_proto_rx_isr(buf, n - 2u);   /* CRC を欠いたところで途切れる */
    usb_proto_poll();
    g_tick += 19u;
    usb_proto_poll();
    CHECK(usb_stats.rx_timeouts == 0u);
    g_tick += 1u;
    usb_proto_poll();
    CHECK(usb_stats.rx_timeouts == 1u);
    CHECK(usb_stats.dropped == 1u);
    CHECK(g_sent_n == 0u);

    const uint8_t ok[2] = { USB_PROTO_MAJOR, USB_PROTO_MINOR };
    expect(req(USB_CMD_GET_VERSION, 0x32, NULL, 0), USB_ERR_OK, 0x32, ok, 2);
}

static void test_ctr_echo(void)
{
    reset();
    static const uint8_t ctrs[] = { 0x00, 0x01, 0x7F, 0x80, 0xFF };
    const uint8_t ok[2] = { USB_PROTO_MAJOR, USB_PROTO_MINOR };
    for (unsigned i = 0; i < sizeof ctrs; i++) {
        expect(req(USB_CMD_GET_VERSION, ctrs[i], NULL, 0), USB_ERR_OK, ctrs[i], ok, 2);
    }
    /* エラー応答でも CTR を返す */
    expect_err(req(USB_CMD_SYM_COUNT, 0xC3, NULL, 0), USB_ERR_LOCKED, 0xC3);
}

/* ---------------------------------------------------------------- 試験 17・18: 接続 */

static void test_connect_lock(void)
{
    reset();
    const uint8_t ver[2] = { USB_PROTO_MAJOR, USB_PROTO_MINOR };
    expect(req(USB_CMD_GET_VERSION, 9, NULL, 0), USB_ERR_OK, 9, ver, 2);

    /* CONNECT 前は CONNECT・GET_VERSION 以外を ERR 0x35 で拒否（未定義コマンドも） */
    uint8_t p[1] = { 4 };
    expect_err(req(USB_CMD_UPLOAD, 5, p, 1), USB_ERR_LOCKED, 5);
    expect_err(req(USB_CMD_SYM_COUNT, 6, NULL, 0), USB_ERR_LOCKED, 6);
    expect_err(req(USB_CMD_EXCHANGE_ID, 7, NULL, 0), USB_ERR_LOCKED, 7);
    expect_err(req(0x55, 8, NULL, 0), USB_ERR_LOCKED, 8);

    const uint8_t con[7] = { USB_PROTO_MAJOR, USB_PROTO_MINOR, DEVICE_ID, FW_VERSION_MAJOR,
                             FW_VERSION_MINOR, 3, USB_LEN_MAX };
    expect(req(USB_CMD_CONNECT, 10, NULL, 0), USB_ERR_OK, 10, con, 7);

    expect_err(req(0x55, 11, NULL, 0), USB_ERR_UNKNOWN_CMD, 11);
    expect_err(req(USB_CMD_GET_VERSION, 12, p, 1), USB_ERR_SYNTAX, 12);   /* 余分な PARAM */
    expect_err(req(USB_CMD_CONNECT, 13, p, 1), USB_ERR_SYNTAX, 13);

    /* DISCONNECT で再び拒否 */
    expect_err(req(USB_CMD_DISCONNECT, 14, NULL, 0), USB_ERR_OK, 14);
    expect_err(req(USB_CMD_SYM_COUNT, 15, NULL, 0), USB_ERR_LOCKED, 15);
}

static void test_bus_event_disconnects(void)
{
    reset();
    connect();
    uint8_t cnt[2] = { TEST_SYM_N, 0 };
    expect(req(USB_CMD_SYM_COUNT, 2, NULL, 0), USB_ERR_OK, 2, cnt, 2);
    usb_proto_on_bus_event();
    usb_proto_poll();
    expect_err(req(USB_CMD_SYM_COUNT, 3, NULL, 0), USB_ERR_LOCKED, 3);
}

static void test_exchange_id(void)
{
    reset();
    connect();
    uint8_t exp[21];
    exp[0] = 20;
    for (uint8_t i = 0; i < 20u; i++) {
        exp[1 + i] = (uint8_t)(0x10u + i);
    }
    expect(req(USB_CMD_EXCHANGE_ID, 3, NULL, 0), USB_ERR_OK, 3, exp, 21);

    /* ノートの type が NT_GNU_BUILD_ID でなければ ERR 0x36 */
    put32le(&test_build_id_note()[8], 1u);
    expect_err(req(USB_CMD_EXCHANGE_ID, 4, NULL, 0), USB_ERR_NO_RESOURCE, 4);
}

/* ---------------------------------------------------------------- 試験 19: メモリ読み出し */

static void test_mem_allowed(void)
{
    CHECK(usb_mem_allowed(0x08000000u, 1));
    CHECK(usb_mem_allowed(0x0803FFFCu, 4));
    CHECK(!usb_mem_allowed(0x0803FFFEu, 4));
    CHECK(!usb_mem_allowed(0x08000000u, 0));
    CHECK(usb_mem_allowed(0x2000C000u, 0x4000u));
    CHECK(!usb_mem_allowed(0x2000C000u, 0x4001u));
    CHECK(!usb_mem_allowed(0x2000BFFEu, 4));       /* SRAM1 と SRAM2 を跨ぐ */
    CHECK(usb_mem_allowed(0x10003FFFu, 1));
    CHECK(!usb_mem_allowed(0x10003FFFu, 2));
    CHECK(usb_mem_allowed(0x1FFF7000u, 0x400u));
    CHECK(!usb_mem_allowed(0x1FFF7400u, 1));
    CHECK(usb_mem_allowed(0x1FFF7500u, 0x100u));
    CHECK(!usb_mem_allowed(0x40000000u, 4));       /* ペリフェラル */
    CHECK(!usb_mem_allowed(0xFFFFFFFEu, 4));
}

static void test_mem_upload(void)
{
    reset();
    connect();
    uint8_t p[1];

    set_mta(0x20000100u, 2);
    p[0] = 4;
    expect_pattern(req(USB_CMD_UPLOAD, 3, p, 1), 3, 0x20000100u, 4);
    p[0] = 8;
    unsigned r8 = g_rd8_calls, r32 = g_rd32_calls;
    expect_pattern(req(USB_CMD_UPLOAD, 4, p, 1), 4, 0x20000104u, 8);   /* MTA が進んでいる */
    CHECK(g_rd8_calls - r8 == 0u);       /* 4 バイト境界に揃っていれば 32bit 単位で読む */
    CHECK(g_rd32_calls - r32 == 2u);

    /* 境界に揃っていない開始: 先頭 3 バイトを 8bit、続く 4 バイトを 32bit */
    set_mta(0x20000201u, 5);
    p[0] = 7;
    r8 = g_rd8_calls;
    r32 = g_rd32_calls;
    expect_pattern(req(USB_CMD_UPLOAD, 6, p, 1), 6, 0x20000201u, 7);
    CHECK(g_rd8_calls - r8 == 3u);
    CHECK(g_rd32_calls - r32 == 1u);
    p[0] = 1;
    expect_pattern(req(USB_CMD_UPLOAD, 7, p, 1), 7, 0x20000208u, 1);

    /* 上限 240 バイト */
    set_mta(0x08001000u, 8);
    p[0] = 240;
    expect_pattern(req(USB_CMD_UPLOAD, 9, p, 1), 9, 0x08001000u, 240);
}

static void test_mem_short_up(void)
{
    reset();
    connect();
    set_mta(0x20000100u, 2);

    /* SHORT_UP のパラメータは [SIZE][ADDR u32] */
    uint8_t q[5];
    q[0] = 5;
    put32le(&q[1], 0x08001003u);
    expect_pattern(req(USB_CMD_SHORT_UP, 3, q, 5), 3, 0x08001003u, 5);

    /* MTA は変わらない */
    uint8_t s[1] = { 4 };
    expect_pattern(req(USB_CMD_UPLOAD, 4, s, 1), 4, 0x20000100u, 4);
}

static void short_up_expect(uint32_t addr, uint8_t size, uint8_t err, uint8_t ctr)
{
    uint8_t q[5];
    q[0] = size;
    put32le(&q[1], addr);
    const uint8_t *r = req(USB_CMD_SHORT_UP, ctr, q, 5);
    if (err == USB_ERR_OK) {
        expect_pattern(r, ctr, addr, size);
    } else {
        expect_err(r, err, ctr);
    }
}

static void test_mem_denied(void)
{
    reset();
    connect();
    short_up_expect(0x40000000u, 4, USB_ERR_DENIED, 1);     /* ペリフェラル */
    short_up_expect(0x2000BFFEu, 4, USB_ERR_DENIED, 2);     /* SRAM1/SRAM2 を跨ぐ */
    short_up_expect(0x0803FFFEu, 4, USB_ERR_DENIED, 3);     /* Flash の末尾を越える */
    short_up_expect(0x1FFF7400u, 4, USB_ERR_DENIED, 4);
    short_up_expect(0x0803FFFCu, 4, USB_ERR_OK, 5);
    short_up_expect(0x1FFF7000u, 4, USB_ERR_OK, 6);
    short_up_expect(0x10000000u, 4, USB_ERR_OK, 7);         /* SRAM2 別名 */
    short_up_expect(0x08000000u, 0, USB_ERR_RANGE, 8);
    short_up_expect(0x08000000u, 241, USB_ERR_RANGE, 9);

    /* UPLOAD が失敗しても MTA は進まない */
    set_mta(0x2000BFFEu, 10);
    uint8_t s[1] = { 4 };
    expect_err(req(USB_CMD_UPLOAD, 11, s, 1), USB_ERR_DENIED, 11);
    s[0] = 2;
    expect_pattern(req(USB_CMD_UPLOAD, 12, s, 1), 12, 0x2000BFFEu, 2);

    /* 形式エラー */
    uint8_t p3[3] = { 0, 0, 0 };
    expect_err(req(USB_CMD_SET_MTA, 13, p3, 3), USB_ERR_SYNTAX, 13);
    expect_err(req(USB_CMD_UPLOAD, 14, NULL, 0), USB_ERR_SYNTAX, 14);
    expect_err(req(USB_CMD_SHORT_UP, 15, p3, 3), USB_ERR_SYNTAX, 15);
}

static void test_mem_ecc(void)
{
    reset();
    connect();
    g_ecc_addr = 0x20000102u;
    short_up_expect(0x20000100u, 4, USB_ERR_NO_RESOURCE, 1);   /* Flash ECC エラー相当 → ERR 0x36 */
    g_ecc_addr = 0;
    short_up_expect(0x20000100u, 4, USB_ERR_OK, 2);            /* 次の読み出しは正常 */
}

/* ---------------------------------------------------------------- 試験 20: 名前表 */

static void test_symbols(void)
{
    reset();
    connect();

    const uint8_t cnt[2] = { TEST_SYM_N, 0 };
    expect(req(USB_CMD_SYM_COUNT, 1, NULL, 0), USB_ERR_OK, 1, cnt, 2);

    /* SYM_INFO: [ADDR u32][TYPE][NUM u16][NLEN][NAME] */
    uint8_t p[40];
    uint8_t exp[40];
    put16le(p, 1);
    put32le(&exp[0], 0x20000100u);
    exp[4] = SYM_I32;
    put16le(&exp[5], 1);
    exp[7] = 5;
    memcpy(&exp[8], "speed", 5);
    expect(req(USB_CMD_SYM_INFO, 2, p, 2), USB_ERR_OK, 2, exp, 13);

    put16le(p, 2);
    put32le(&exp[0], 0x20000200u);
    exp[4] = SYM_BYTES;
    put16le(&exp[5], 16);
    exp[7] = 3;
    memcpy(&exp[8], "buf", 3);
    expect(req(USB_CMD_SYM_INFO, 3, p, 2), USB_ERR_OK, 3, exp, 11);

    put16le(p, TEST_SYM_N);   /* 範囲外 */
    expect_err(req(USB_CMD_SYM_INFO, 4, p, 2), USB_ERR_RANGE, 4);
    expect_err(req(USB_CMD_SYM_INFO, 5, p, 1), USB_ERR_SYNTAX, 5);

    /* SYM_FIND: [INDEX u16][ADDR u32][TYPE][NUM u16] */
    put16le(&exp[0], 2);
    put32le(&exp[2], 0x20000200u);
    exp[6] = SYM_BYTES;
    put16le(&exp[7], 16);
    expect(sym_find_req("buf", 6), USB_ERR_OK, 6, exp, 9);

    put16le(&exp[0], 0);
    put32le(&exp[2], 0x20000010u);
    exp[6] = SYM_U8;
    put16le(&exp[7], 1);
    expect(sym_find_req("state", 7), USB_ERR_OK, 7, exp, 9);

    /* 未登録名・大文字小文字・前方一致は ERR 0x36 */
    expect_err(sym_find_req("nope", 8), USB_ERR_NO_RESOURCE, 8);
    expect_err(sym_find_req("STATE", 9), USB_ERR_NO_RESOURCE, 9);
    expect_err(sym_find_req("stat", 10), USB_ERR_NO_RESOURCE, 10);

    /* NLEN の範囲外・長さ不一致 */
    p[0] = 0;
    expect_err(req(USB_CMD_SYM_FIND, 11, p, 1), USB_ERR_RANGE, 11);
    memset(p, 'a', sizeof p);
    p[0] = 32;
    expect_err(req(USB_CMD_SYM_FIND, 12, p, 33), USB_ERR_RANGE, 12);
    p[0] = 5;
    expect_err(req(USB_CMD_SYM_FIND, 13, p, 4), USB_ERR_SYNTAX, 13);
}

static void test_sym_upload(void)
{
    reset();
    connect();

    /* 8 バイト以下の変数: 全体 */
    uint8_t exp[USB_UPLOAD_MAX];
    for (uint8_t i = 0; i < 4u; i++) {
        exp[i] = test_pattern(0x20000100u + i);
    }
    unsigned sets = g_primask_sets;
    expect(sym_upload_req("speed", 0, 0, 1), USB_ERR_OK, 1, exp, 4);
    CHECK(g_primask_sets - sets == 2u);     /* 割り込みを禁止して復帰（FR-USB-38） */
    CHECK(g_primask == 0u);

    /* OFFSET と SIZE 指定 */
    sets = g_primask_sets;
    expect_pattern(sym_upload_req("arr", 2, 4, 2), 2, 0x20000302u, 4);
    CHECK(g_primask_sets - sets == 2u);     /* arr は 2×4 = 8 バイト */

    /* 8 バイトを超える変数は割り込みを禁止しない */
    sets = g_primask_sets;
    expect_pattern(sym_upload_req("buf", 4, 8, 3), 3, 0x20000204u, 8);
    CHECK(g_primask_sets == sets);

    /* SIZE=0 は OFFSET から最後まで */
    expect_pattern(sym_upload_req("buf", 4, 0, 4), 4, 0x20000204u, 12);
    expect_pattern(sym_upload_req("buf", 0, 0, 5), 5, 0x20000200u, 16);

    /* 範囲外 */
    expect_err(sym_upload_req("buf", 16, 1, 6), USB_ERR_RANGE, 6);
    expect_err(sym_upload_req("buf", 8, 9, 7), USB_ERR_RANGE, 7);
    expect_err(sym_upload_req("nope", 0, 0, 8), USB_ERR_NO_RESOURCE, 8);

    /* 長さ不一致（OFFSET・SIZE が欠けている） */
    uint8_t p[5] = { 3, 'b', 'u', 'f', 0 };
    expect_err(req(USB_CMD_SYM_UPLOAD, 9, p, 5), USB_ERR_SYNTAX, 9);
}

/* ---------------------------------------------------------------- 試験 21: CAN 代行 */

static const uint8_t *can_req(uint16_t id, uint8_t dlc, const uint8_t *data, uint8_t dlen, uint8_t ctr)
{
    uint8_t p[16];
    put16le(p, id);
    p[2] = dlc;
    if (dlen > 0u) {
        memcpy(&p[3], data, dlen);
    }
    return req(USB_CMD_CAN_REQUEST, ctr, p, (uint8_t)(3u + dlen));
}

static void test_can_request(void)
{
    reset();
    connect();
    uint8_t d[8] = { 0x02, 0x10, 0x03, 0, 0, 0, 0, 0 };

    /* 範囲外は CAN の処理系へ渡さない */
    expect_err(can_req(0x800, 0, NULL, 0, 1), USB_ERR_RANGE, 1);
    expect_err(can_req(0x100, 9, d, 8, 2), USB_ERR_RANGE, 2);
    CHECK(g_cr_calls == 0u);
    /* DLC と DATA の長さが合わない / 短すぎる */
    expect_err(can_req(0x100, 2, d, 1, 3), USB_ERR_SYNTAX, 3);
    expect_err(req(USB_CMD_CAN_REQUEST, 4, d, 2), USB_ERR_SYNTAX, 4);
    CHECK(g_cr_calls == 0u);

    /* 応答なし: N=0 */
    const uint8_t none[1] = { 0 };
    expect(can_req(0x000, 1, d, 1, 5), USB_ERR_OK, 5, none, 1);
    CHECK(g_cr_calls == 1u);
    CHECK(g_cr_id == 0x000u && g_cr_dlc == 1u && g_cr_data[0] == 0x02u);

    /* 応答あり: comm_process_request が作ったフレームを DATA に入れて返す */
    g_cr_resp_n = 1;
    g_cr_resp.id = 0x183;
    g_cr_resp.dlc = 3;
    g_cr_resp.data[0] = 0x82;
    g_cr_resp.data[1] = 0x10;
    g_cr_resp.data[2] = 0x55;
    const uint8_t rsp[7] = { 1, 0x83, 0x01, 3, 0x82, 0x10, 0x55 };
    expect(can_req(0x103, 3, d, 3, 6), USB_ERR_OK, 6, rsp, 7);
    CHECK(g_cr_calls == 2u);
    CHECK(g_cr_id == 0x103u && g_cr_dlc == 3u);
    CHECK(memcmp(g_cr_data, d, 3) == 0);

    /* DLC 0 */
    const uint8_t rsp0[7] = { 1, 0x83, 0x01, 3, 0x82, 0x10, 0x55 };
    expect(can_req(0x103, 0, NULL, 0, 7), USB_ERR_OK, 7, rsp0, 7);
    CHECK(g_cr_dlc == 0u);
}

/* ---------------------------------------------------------------- 送信詰まり（FR-USB-05、A-1） */

static void test_tx_stuck_drops_request(void)
{
    reset();
    connect();
    set_mta(0x20000100u, 2);

    /* 応答を 1 つ INFLIGHT にしたまま完了させない（ホストが読み出さない） */
    test_tx_complete();
    feed_req(USB_CMD_GET_VERSION, 3, NULL, 0);
    usb_proto_poll();
    unsigned sent0 = g_sent_n;
    uint32_t t = g_tick;

    /* 詰まっている間に届く要求: SET_MTA・CAN_REQUEST・DISCONNECT */
    uint8_t p[4];
    put32le(p, 0x20000200u);
    feed_req(USB_CMD_SET_MTA, 4, p, 4);
    uint8_t c[3] = { 0x03, 0x01, 0 };
    feed_req(USB_CMD_CAN_REQUEST, 5, c, 3);
    feed_req(USB_CMD_DISCONNECT, 6, NULL, 0);

    g_tick = t + 49u;
    usb_proto_poll();
    CHECK(usb_stats.tx_dropped == 0u);       /* 50 ms 未満は待つ */
    CHECK(g_sent_n == sent0);

    g_tick = t + 50u;
    for (int i = 0; i < 4; i++) {
        usb_proto_poll();
    }
    CHECK(g_sent_n == sent0);                /* 応答は出ない */
    CHECK(usb_stats.tx_dropped == 3u);       /* 捨てた要求 1 つにつき 1 */
    CHECK(usb_stats.dropped == 3u);
    CHECK(g_cr_calls == 0u);                 /* 要求は実行されない */

    /* SET_MTA・DISCONNECT の副作用が無い: MTA は元のまま、セッションも維持 */
    test_tx_complete();
    usb_proto_poll();
    uint8_t s[1] = { 4 };
    expect_pattern(req(USB_CMD_UPLOAD, 7, s, 1), 7, 0x20000100u, 4);
    expect_pattern(req(USB_CMD_UPLOAD, 8, s, 1), 8, 0x20000104u, 4);
}

static void test_tx_failure_drops_after_50ms(void)
{
    reset();
    g_tx_fail = 1;
    test_tx_complete();
    feed_req(USB_CMD_GET_VERSION, 1, NULL, 0);
    usb_proto_poll();
    CHECK(g_sent_n == 0u);
    g_tick += 49u;
    usb_proto_poll();
    CHECK(usb_stats.tx_dropped == 0u);
    g_tick += 1u;
    usb_proto_poll();
    CHECK(usb_stats.tx_dropped == 1u);
    CHECK(usb_stats.dropped == 1u);

    g_tx_fail = 0;
    const uint8_t ok[2] = { USB_PROTO_MAJOR, USB_PROTO_MINOR };
    expect(req(USB_CMD_GET_VERSION, 2, NULL, 0), USB_ERR_OK, 2, ok, 2);
}

/* ---------------------------------------------------------------- イベント（EVT 0x01、A-2） */

static comm_frame_t fault_frame(uint8_t state, uint8_t fault)
{
    comm_frame_t f;
    memset(&f, 0, sizeof f);
    f.id = CAN_ID_FAULT_BASE + 3;
    f.dlc = 2;
    f.data[0] = state;
    f.data[1] = fault;
    return f;
}

static void expect_event(const uint8_t *f, const comm_frame_t *e)
{
    CHECK(f != NULL);
    if (f == NULL) {
        return;
    }
    CHECK(f[0] == USB_FRAME_SOF);
    CHECK(f[1] == 5u + e->dlc);
    CHECK(f[2] == USB_PID_EVENT);
    CHECK(f[3] == USB_EVT_CAN_FRAME);
    CHECK(f[4] == (uint8_t)e->id && f[5] == (uint8_t)(e->id >> 8));
    CHECK(f[6] == e->dlc);
    CHECK(memcmp(&f[7], e->data, e->dlc) == 0);
    uint16_t crc = usb_crc16(&f[1], (uint32_t)f[1] + 1u);
    CHECK(f[2 + f[1]] == (uint8_t)crc && f[3 + f[1]] == (uint8_t)(crc >> 8));
}

static void test_fault_event_only_when_connected(void)
{
    reset();
    comm_frame_t e = fault_frame(5, 2);

    /* 未接続では送らない */
    usb_proto_notify_can_frame(&e);
    usb_proto_poll();
    CHECK(g_sent_n == 0u);

    /* 接続中は送る */
    connect();
    test_tx_complete();
    unsigned n = g_sent_n;
    usb_proto_notify_can_frame(&e);
    usb_proto_poll();
    CHECK(g_sent_n == n + 1u);
    expect_event(g_sent[g_sent_n - 1u].bytes, &e);

    /* DISCONNECT 後は送らない */
    expect_err(req(USB_CMD_DISCONNECT, 2, NULL, 0), USB_ERR_OK, 2);
    test_tx_complete();
    n = g_sent_n;
    usb_proto_notify_can_frame(&e);
    usb_proto_poll();
    CHECK(g_sent_n == n);
}

static void test_event_overwrite_counts_drop(void)
{
    reset();
    connect();

    /* 送信スロットを塞いでおく */
    test_tx_complete();
    feed_req(USB_CMD_GET_VERSION, 2, NULL, 0);
    usb_proto_poll();
    unsigned n = g_sent_n;

    comm_frame_t a = fault_frame(1, 1);
    comm_frame_t b = fault_frame(2, 2);
    usb_proto_notify_can_frame(&a);
    CHECK(usb_stats.tx_dropped == 0u);       /* 最初のイベントは上書きではない */
    usb_proto_notify_can_frame(&b);
    CHECK(usb_stats.tx_dropped == 1u);       /* 未送信の a を b で上書き */
    CHECK(usb_stats.dropped == 1u);

    /* 送信が空いたら新しいほう（b）だけが出る */
    test_tx_complete();
    usb_proto_poll();
    CHECK(g_sent_n == n + 1u);
    expect_event(g_sent[g_sent_n - 1u].bytes, &b);
    test_tx_complete();
    usb_proto_poll();
    CHECK(g_sent_n == n + 1u);
    CHECK(usb_stats.tx_dropped == 1u);
}

static void test_event_dropped_after_50ms(void)
{
    reset();
    connect();
    test_tx_complete();
    feed_req(USB_CMD_GET_VERSION, 2, NULL, 0);
    usb_proto_poll();
    unsigned n = g_sent_n;

    comm_frame_t a = fault_frame(1, 1);
    usb_proto_notify_can_frame(&a);
    g_tick += 49u;
    usb_proto_poll();
    CHECK(usb_stats.tx_dropped == 0u);
    g_tick += 1u;
    usb_proto_poll();
    CHECK(usb_stats.tx_dropped == 1u);
    CHECK(g_sent_n == n);
}

/* ---------------------------------------------------------------- main */

int main(void)
{
    test_stubs_reset();

    RUN(test_crc16);
    RUN(test_rx_bad_crc);
    RUN(test_rx_bad_len);
    RUN(test_rx_timeout);
    RUN(test_ctr_echo);
    RUN(test_connect_lock);
    RUN(test_bus_event_disconnects);
    RUN(test_exchange_id);
    RUN(test_mem_allowed);
    RUN(test_mem_upload);
    RUN(test_mem_short_up);
    RUN(test_mem_denied);
    RUN(test_mem_ecc);
    RUN(test_symbols);
    RUN(test_sym_upload);
    RUN(test_can_request);
    RUN(test_tx_stuck_drops_request);
    RUN(test_tx_failure_drops_after_50ms);
    RUN(test_fault_event_only_when_connected);
    RUN(test_event_overwrite_counts_drop);
    RUN(test_event_dropped_after_50ms);

    printf("\n%d checks, %d failed\n", g_checks, g_fail);
    return g_fail == 0 ? 0 : 1;
}
