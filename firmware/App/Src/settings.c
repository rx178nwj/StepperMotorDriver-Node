#include "settings.h"
#include "app_main.h"
#include "board.h"
#include "config.h"
#include "drv.h"
#include <stddef.h>
#include <string.h>

/* リンカスクリプト STM32L433xx_app.ld で FLASH を 252K にして確保した領域。
 * ホスト単体テスト（tests/host）は実アドレスを読めないため、-D で NV_BASE と NV_MEMCPY を差し替える。実機では直接読む */
#ifndef NV_BASE
#define NV_BASE        0x0803F000u
#define NV_MEMCPY(dst, addr, n)  memcpy((dst), (const void *)(uintptr_t)(addr), (n))
#endif
#define NV_FIRST_PAGE  126u
#define NV_PAGES       2u
#define REC_SIZE       64u
#define REC_PER_PAGE   (FLASH_PAGE_SIZE / REC_SIZE)
#define REC_MAGIC      0x4D53454Eu   /* "NESM" */
#define REC_VERSION    3u
/* 版 1: settings_t が 32 バイト（s の後ろの予約 12 バイト + CRC）。版 2 は s が 40 バイト（位置センサの項目を追加）、
 * 版 3 も 40 バイト（版 2 の予約 3 バイトを J10_MODE・HOME_SPEED に置き換え）で、
 * 新項目は s の直後に追加しただけなので CRC の位置は同じ。旧版は新項目を既定値で補って読む */
#define REC_VERSION_V1 1u
#define REC_VERSION_V2 2u
#define SETTINGS_V1_SIZE 32u

typedef struct {
    /* dword0: 最後に書く（コミット印） */
    uint32_t magic;
    uint16_t version;
    uint16_t size;
    /* dword1 */
    uint32_t seq;
    uint32_t reserved0;
    /* dword2-6 */
    settings_t s;
    /* dword7 */
    uint32_t reserved1;
    uint32_t crc;
} record_t;

_Static_assert(sizeof(settings_t) == 40, "settings_t layout changed: bump REC_VERSION");
_Static_assert(offsetof(settings_t, gear_ratio) == SETTINGS_V1_SIZE, "v1 fields must stay in place");
_Static_assert(offsetof(settings_t, j10_mode) + 3u == sizeof(settings_t), "v3 fields must fill the v2 reserved area");
_Static_assert(sizeof(record_t) == REC_SIZE, "record size");
_Static_assert(offsetof(record_t, crc) == REC_SIZE - 4u, "crc must stay at the end (v1 compatible)");

volatile bool nv_ecc_error;

/* 書き込み途中で電源断したダブルワードを読むと ECC 2bit エラーで NMI になる。
 * Flash 読み出し由来ならフラグを立てて true を返し、CRC で不正レコードとして扱う。
 * NMI_Handler（stm32l4xx_it.c）から呼ばれる。 */
bool app_nmi_handled(void)
{
    if (FLASH->ECCR & FLASH_ECCR_ECCD) {
        FLASH->ECCR |= FLASH_ECCR_ECCD;
        nv_ecc_error = true;
        return true;
    }
    return false;
}

void settings_defaults(settings_t *s)
{
    memset(s, 0, sizeof *s);
    s->run_current_ma = DEF_RUN_CURRENT_MA;
    s->hold_current_ma = DEF_HOLD_CURRENT_MA;
    s->microstep = DEF_MICROSTEP;
    s->flags = DEF_FLAGS;
    s->comm_timeout_ms = 0;
    s->max_speed = DEF_MAX_SPEED;
    s->accel = DEF_ACCEL;
    s->decel = DEF_DECEL;
    s->start_speed = DEF_START_SPEED;
    s->jog_speed = DEF_JOG_SPEED;
    s->hold_delay_ms = DEF_HOLD_DELAY_MS;
    s->steps_per_rev = DEF_STEPS_PER_REV;
    s->gear_ratio = DEF_GEAR_RATIO;
    s->sensor_offset = DEF_SENSOR_OFFSET;
    s->pa7_mode = DEF_PA7_MODE;
    s->j10_mode = DEF_J10_MODE;
    s->home_speed = DEF_HOME_SPEED;
}

static bool clamp16(uint16_t *v, uint16_t lo, uint16_t hi)
{
    uint16_t o = *v;
    if (*v < lo) *v = lo;
    if (*v > hi) *v = hi;
    return *v != o;
}

static bool clamp32(uint32_t *v, uint32_t lo, uint32_t hi)
{
    uint32_t o = *v;
    if (*v < lo) *v = lo;
    if (*v > hi) *v = hi;
    return *v != o;
}

bool settings_sanitize(settings_t *s)
{
    bool changed = false;
    changed |= clamp16(&s->run_current_ma, CURRENT_MIN_MA, CURRENT_LIMIT_MA);
    changed |= clamp16(&s->hold_current_ma, CURRENT_MIN_MA, CURRENT_LIMIT_MA);
    if (!drv_microstep_valid(s->microstep)) {
        s->microstep = DEF_MICROSTEP;
        changed = true;
    }
    const uint8_t flags_mask = SETTINGS_FLAG_INVERT_DIR | SETTINGS_FLAG_LOCAL_JOG | SETTINGS_FLAG_SENSOR_DIR |
                               SETTINGS_FLAG_AGC | SETTINGS_FLAG_HOME_ACTIVE_HIGH;
    if (s->flags & ~flags_mask) {
        s->flags &= flags_mask;
        changed = true;
    }
    changed |= clamp32(&s->max_speed, 1u, STEP_RATE_MAX);
    /* start/jog > max は使用時に max へ制限する（書き込み順に依存させないため、ここでは見ない） */
    changed |= clamp32(&s->start_speed, 1u, STEP_RATE_MAX);
    changed |= clamp32(&s->jog_speed, 1u, STEP_RATE_MAX);
    changed |= clamp32(&s->accel, 1u, 10000000u);
    changed |= clamp32(&s->decel, 1u, 10000000u);
    changed |= clamp16(&s->steps_per_rev, 1u, 0xFFFFu);
    changed |= clamp16(&s->gear_ratio, 1u, GEAR_RATIO_MAX);
    changed |= clamp16(&s->sensor_offset, 0u, SENSOR_OFFSET_MAX);
    if (s->pa7_mode > PA7_MODE_Z) {
        s->pa7_mode = DEF_PA7_MODE;
        changed = true;
    }
    if (s->j10_mode > 2u) {
        s->j10_mode = DEF_J10_MODE;
        changed = true;
    }
    changed |= clamp16(&s->home_speed, 1u, 0xFFFFu);
    return changed;
}

static uint32_t crc32(const uint8_t *p, uint32_t n)
{
    uint32_t c = 0xFFFFFFFFu;
    while (n--) {
        c ^= *p++;
        for (int k = 0; k < 8; k++) {
            c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u)));
        }
    }
    return ~c;
}

static uint32_t slot_addr(uint32_t page, uint32_t slot)
{
    return NV_BASE + page * FLASH_PAGE_SIZE + slot * REC_SIZE;
}

typedef enum { SLOT_BLANK, SLOT_VALID, SLOT_USED } slot_state_t;

static slot_state_t read_slot(uint32_t addr, record_t *r)
{
    nv_ecc_error = false;
    NV_MEMCPY(r, addr, sizeof *r);
    if (nv_ecc_error) {
        return SLOT_USED;
    }
    const uint32_t *w = (const uint32_t *)r;
    bool blank = true;
    for (uint32_t i = 0; i < REC_SIZE / 4u; i++) {
        if (w[i] != 0xFFFFFFFFu) {
            blank = false;
            break;
        }
    }
    if (blank) {
        return SLOT_BLANK;
    }
    bool layout_ok = ((r->version == REC_VERSION || r->version == REC_VERSION_V2) && r->size == sizeof(settings_t)) ||
                     (r->version == REC_VERSION_V1 && r->size == SETTINGS_V1_SIZE);
    if (r->magic == REC_MAGIC && layout_ok &&
        r->crc == crc32((const uint8_t *)r, offsetof(record_t, crc))) {
        return SLOT_VALID;
    }
    return SLOT_USED;
}

typedef struct {
    bool found;
    uint32_t page;
    uint32_t slot;
    uint32_t seq;
    record_t rec;
} latest_t;

static void find_latest(latest_t *l)
{
    record_t r;
    l->found = false;
    for (uint32_t p = 0; p < NV_PAGES; p++) {
        for (uint32_t i = 0; i < REC_PER_PAGE; i++) {
            if (read_slot(slot_addr(p, i), &r) == SLOT_VALID &&
                (!l->found || (int32_t)(r.seq - l->seq) > 0)) {
                l->found = true;
                l->page = p;
                l->slot = i;
                l->seq = r.seq;
                l->rec = r;
            }
        }
    }
}

bool settings_load(settings_t *s)
{
    latest_t l;
    find_latest(&l);
    if (!l.found) {
        return false;
    }
    if (l.rec.version == REC_VERSION_V1) {
        settings_defaults(s);                       /* 新項目（位置センサ・J10 関連）は既定値 */
        memcpy(s, &l.rec.s, SETTINGS_V1_SIZE);
    } else {
        *s = l.rec.s;
        if (l.rec.version == REC_VERSION_V2) {
            /* 版 2 では同じ位置が予約領域（0）だった。新項目は既定値にする */
            s->j10_mode = DEF_J10_MODE;
            s->home_speed = DEF_HOME_SPEED;
        }
    }
    return true;
}

static bool erase_page(uint32_t page)
{
    FLASH_EraseInitTypeDef e = {0};
    e.TypeErase = FLASH_TYPEERASE_PAGES;
    e.Banks = FLASH_BANK_1;
    e.Page = NV_FIRST_PAGE + page;
    e.NbPages = 1;
    uint32_t err = 0;
    return HAL_FLASHEx_Erase(&e, &err) == HAL_OK;
}

static bool page_has_blank_from(uint32_t page, uint32_t from, uint32_t *slot)
{
    record_t r;
    for (uint32_t i = from; i < REC_PER_PAGE; i++) {
        if (read_slot(slot_addr(page, i), &r) == SLOT_BLANK) {
            *slot = i;
            return true;
        }
    }
    return false;
}

bool settings_save(const settings_t *s)
{
    latest_t l;
    find_latest(&l);

    record_t r;
    memset(&r, 0xFF, sizeof r);
    r.magic = REC_MAGIC;
    r.version = REC_VERSION;
    r.size = sizeof(settings_t);
    r.seq = l.found ? l.seq + 1u : 1u;
    r.s = *s;
    r.crc = crc32((const uint8_t *)&r, offsetof(record_t, crc));

    HAL_FLASH_Unlock();
    __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_ALL_ERRORS);

    /* 書き込み先: 最新レコードと同じページの後ろの空き。なければもう一方のページを消去 */
    uint32_t page = l.found ? l.page : 0u;
    uint32_t slot = 0;
    bool ok = true;
    if (!page_has_blank_from(page, l.found ? l.slot + 1u : 0u, &slot)) {
        if (l.found) {
            page = (page + 1u) % NV_PAGES;
        }
        slot = 0;
        ok = erase_page(page);
    }

    uint32_t addr = slot_addr(page, slot);
    const uint64_t *dw = (const uint64_t *)&r;
    /* dword1〜7 を先に、コミット印の dword0 を最後に書く */
    for (uint32_t i = 1; ok && i < REC_SIZE / 8u; i++) {
        ok = HAL_FLASH_Program(FLASH_TYPEPROGRAM_DOUBLEWORD, addr + i * 8u, dw[i]) == HAL_OK;
    }
    if (ok) {
        ok = HAL_FLASH_Program(FLASH_TYPEPROGRAM_DOUBLEWORD, addr, dw[0]) == HAL_OK;
    }
    HAL_FLASH_Lock();

    record_t check;
    return ok && read_slot(addr, &check) == SLOT_VALID && memcmp(&check, &r, sizeof r) == 0;
}
