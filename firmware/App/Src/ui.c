#include "ui.h"
#include "board.h"

#define DEBOUNCE_MS      20u
#define COMM_FLASH_MS    50u
#define FAULT_BLINK_MS   200u
#define FAULT_GAP_MS     1000u

typedef struct {
    bool stable;
    bool last_raw;
    uint8_t count;
} debounce_t;

static debounce_t sw1, sw2;
static uint32_t comm_seen;
static uint32_t comm_until;

static void debounce(debounce_t *d, bool raw)
{
    if (raw != d->last_raw) {
        d->last_raw = raw;
        d->count = 0;
    } else if (d->count < DEBOUNCE_MS) {
        if (++d->count == DEBOUNCE_MS) {
            d->stable = raw;
        }
    }
}

void ui_scan_switches(void)
{
    debounce(&sw1, !board_pin_read(SW1_PORT, SW1_PIN));   /* アクティブ Low */
    debounce(&sw2, !board_pin_read(SW2_PORT, SW2_PIN));
}

bool ui_sw1(void)
{
    return sw1.stable;
}

bool ui_sw2(void)
{
    return sw2.stable;
}

/* code 回点滅 → 消灯ギャップ を繰り返す */
static bool fault_pattern(uint32_t now, uint8_t code)
{
    uint32_t period = (uint32_t)code * 2u * FAULT_BLINK_MS + FAULT_GAP_MS;
    uint32_t t = now % period;
    if (t >= (uint32_t)code * 2u * FAULT_BLINK_MS) {
        return false;
    }
    return (t / FAULT_BLINK_MS) % 2u == 0u;
}

void ui_update(uint32_t now, const ui_view_t *v)
{
    board_pin_write(LED_MCU_PORT, LED_MCU_PIN, (now % 1000u) < 500u);

    bool run;
    if (v->moving) {
        run = (now % 200u) < 100u;
    } else {
        run = v->enabled;
    }
    board_pin_write(LED_RUN_PORT, LED_RUN_PIN, run);

    bool fault = v->fault_code ? fault_pattern(now, v->fault_code) : v->temp_warn;
    board_pin_write(LED_FAULT_PORT, LED_FAULT_PIN, fault);

    if (v->comm_count != comm_seen) {
        comm_seen = v->comm_count;
        comm_until = now + COMM_FLASH_MS;
    }
    board_pin_write(LED_COMM_PORT, LED_COMM_PIN, (int32_t)(comm_until - now) > 0);
}
