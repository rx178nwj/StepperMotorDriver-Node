#include "app.h"
#include "board.h"
#include "comm.h"
#include "config.h"
#include "drv.h"
#include "monitor.h"
#include "settings.h"
#include "stepper.h"
#include "ui.h"
#include <string.h>

#define FLAG_INVERT_DIR  SETTINGS_FLAG_INVERT_DIR
#define FLAG_LOCAL_JOG   SETTINGS_FLAG_LOCAL_JOG
#define CHORD_HOLD_MS    2000u

static settings_t cfg;

/* CONFIG_STATUS レジスタ */
#define CFG_ST_LOADED      (1u << 0)   /* 起動時に保存設定を読み込んだ */
#define CFG_ST_DIRTY       (1u << 1)   /* 未保存の変更あり */
#define CFG_ST_SAVE_FAILED (1u << 2)   /* 直前の保存に失敗 */
#define CFG_ST_SANITIZED   (1u << 3)   /* 保存値が範囲外で補正した */
static uint8_t cfg_status;

static app_state_t state = APP_NO_VM;
static uint8_t fault_bits;
static volatile uint8_t isr_fault;
static uint8_t cmd_result;
static uint8_t board_id;
static uint32_t now;

static monitor_values_t mon = { .vdda_mv = 3300u, .board_temp_dc = TEMP_INVALID };
static bool vm_ok;
static uint32_t vm_good_ms;
static uint8_t vm_bad_count;
static uint8_t vm_bad_reason;
static bool temp_warn;

static bool lo_armed;
static uint32_t lo_arm_at;

static bool hold_active;
static uint32_t idle_since;
static uint32_t start_allowed_at;

static int8_t jog_dir;
static bool chord_done;
static uint32_t chord_since;

static inline bool time_reached(uint32_t t)
{
    return (int32_t)(now - t) >= 0;
}

/* ---------------------------------------------------------------- 状態遷移 */

static void apply_stepper_params(void)
{
    stepper_params_t p = {
        .max_speed = (float)cfg.max_speed,
        .accel = (float)cfg.accel,
        .decel = (float)cfg.decel,
        .start_speed = (float)cfg.start_speed,
        .invert_dir = (cfg.flags & FLAG_INVERT_DIR) != 0,
    };
    stepper_set_params(&p);
}

static void driver_wake(void)
{
    drv_set_current_ma(cfg.hold_current_ma, mon.vdda_mv);
    drv_wake(cfg.microstep);
    lo_armed = false;
    lo_arm_at = now + LO_ARM_DELAY_MS;
}

static void driver_sleep(void)
{
    drv_enable(false);
    stepper_estop();
    drv_standby();
    drv_set_current_min();
    lo_armed = false;
}

static void enter_fault(uint8_t bits)
{
    driver_sleep();
    fault_bits |= bits;
    jog_dir = 0;
    state = APP_FAULT;
}

/* LO1/LO2 割り込み（EXTI、優先度 IRQ_PRIO_FAULT） */
void app_on_driver_fault_isr(drv_fault_t f)
{
    drv_enable(false);
    stepper_estop();
    isr_fault |= (uint8_t)f;
}

static uint8_t cmd_enable(void)
{
    if (state != APP_DISABLED) {
        return RESULT_REJECTED;
    }
    drv_set_current_ma(cfg.run_current_ma, mon.vdda_mv);
    hold_active = false;
    idle_since = now;
    start_allowed_at = now + CURRENT_SETTLE_MS;
    drv_enable(true);
    state = APP_ENABLED;
    return RESULT_OK;
}

static uint8_t cmd_disable(void)
{
    if (state != APP_ENABLED) {
        return RESULT_REJECTED;
    }
    stepper_estop();
    drv_enable(false);
    jog_dir = 0;
    state = APP_DISABLED;
    return RESULT_OK;
}

static uint8_t cmd_clear_fault(void)
{
    if (state != APP_FAULT) {
        return RESULT_REJECTED;
    }
    if (mon.board_temp_dc != TEMP_INVALID && mon.board_temp_dc >= BOARD_TEMP_WARN_DC) {
        return RESULT_REJECTED;   /* 冷えるまで解除しない */
    }
    fault_bits = 0;
    if (vm_ok) {
        driver_wake();            /* スタンバイ経由でドライバのラッチも解除済み */
        state = APP_DISABLED;
    } else {
        state = APP_NO_VM;
    }
    return RESULT_OK;
}

static uint8_t cmd_reset_angle(void)
{
    if (!drv_is_awake() || stepper_is_running()) {
        return RESULT_REJECTED;
    }
    drv_pulse_reset();
    return RESULT_OK;
}

static bool motion_idle(void);
static void apply_settings(void);

static uint8_t cmd_save(void)
{
    if (!motion_idle()) {
        return RESULT_REJECTED;   /* Flash 消去中は CPU が止まるため */
    }
    if (!settings_save(&cfg)) {
        cfg_status |= CFG_ST_SAVE_FAILED;
        return RESULT_FAILED;
    }
    cfg_status &= (uint8_t)~(CFG_ST_DIRTY | CFG_ST_SAVE_FAILED);
    return RESULT_OK;
}

static uint8_t cmd_load(bool factory)
{
    if (!motion_idle()) {
        return RESULT_REJECTED;
    }
    settings_t t;
    if (factory) {
        settings_defaults(&t);
    } else if (!settings_load(&t)) {
        return RESULT_FAILED;     /* 保存設定なし */
    } else if (settings_sanitize(&t)) {
        cfg_status |= CFG_ST_SANITIZED;
    }
    cfg = t;
    if (factory) {
        cfg_status |= CFG_ST_DIRTY;   /* 保存するまで Flash は以前のまま */
    } else {
        cfg_status &= (uint8_t)~CFG_ST_DIRTY;
    }
    apply_settings();
    return RESULT_OK;
}

static uint8_t command(uint8_t c)
{
    switch (c) {
    case CMD_ENABLE:           return cmd_enable();
    case CMD_DISABLE:          return cmd_disable();
    case CMD_STOP:             stepper_stop(); return RESULT_OK;
    case CMD_ESTOP:            stepper_estop(); return RESULT_OK;
    case CMD_CLEAR_FAULT:      return cmd_clear_fault();
    case CMD_RESET_ELEC_ANGLE: return cmd_reset_angle();
    case CMD_SAVE_SETTINGS:    return cmd_save();
    case CMD_LOAD_SETTINGS:    return cmd_load(false);
    case CMD_FACTORY_SETTINGS: return cmd_load(true);
    default:                   return RESULT_INVALID;
    }
}

/* ---------------------------------------------------------------- レジスタ */

static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static void wr16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void wr32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}

typedef struct {
    uint8_t addr;
    uint8_t size;
} reg_def_t;

/* 書き込み可能レジスタ（アドレス昇順。設定 → 動作指令 → COMMAND の順に反映される） */
static const reg_def_t writable[] = {
    {0x20, 2}, {0x22, 2}, {0x24, 1}, {0x25, 1}, {0x26, 2},
    {0x28, 4}, {0x2C, 4}, {0x30, 4}, {0x34, 4}, {0x38, 4}, {0x3C, 2}, {0x3E, 2},
    {0x40, 4}, {0x44, 4}, {0x48, 4}, {0x4C, 4}, {0x50, 1},
};

static bool motion_idle(void)
{
    return !stepper_is_running() && !stepper_has_pending();
}

/* 設定をドライバ・プランナへ反映（何度呼んでもよい） */
static void apply_settings(void)
{
    apply_stepper_params();
    if (drv_is_awake()) {
        drv_wake(cfg.microstep);   /* 起床中は DMODE の書き換えのみ */
        bool run = state == APP_ENABLED && !hold_active;
        drv_set_current_ma(run ? cfg.run_current_ma : cfg.hold_current_ma, mon.vdda_mv);
    }
}

/* 設定レジスタ 1 項目の書き込み。範囲外は反映しない */
static uint8_t write_setting(uint8_t addr, const uint8_t *p)
{
    settings_t t = cfg;
    switch (addr) {
    case 0x20: t.run_current_ma = rd16(p); break;
    case 0x22: t.hold_current_ma = rd16(p); break;
    case 0x24: t.microstep = p[0]; break;
    case 0x25: t.flags = p[0]; break;
    case 0x26: t.comm_timeout_ms = rd16(p); break;
    case 0x28: t.max_speed = rd32(p); break;
    case 0x2C: t.accel = rd32(p); break;
    case 0x30: t.decel = rd32(p); break;
    case 0x34: t.start_speed = rd32(p); break;
    case 0x38: t.jog_speed = rd32(p); break;
    case 0x3C: t.hold_delay_ms = rd16(p); break;
    case 0x3E: t.steps_per_rev = rd16(p); break;
    default: return RESULT_INVALID;
    }
    if (settings_sanitize(&t)) {
        return RESULT_INVALID;
    }
    if (t.microstep != cfg.microstep && !motion_idle()) {
        return RESULT_REJECTED;   /* 位置の単位が変わるため停止中のみ */
    }
    if (memcmp(&t, &cfg, sizeof t) != 0) {
        cfg = t;
        cfg_status |= CFG_ST_DIRTY;
        apply_settings();
    }
    return RESULT_OK;
}

static uint8_t write_reg(uint8_t addr, const uint8_t *p)
{
    switch (addr) {
    case 0x40:
        if (state != APP_ENABLED) return RESULT_REJECTED;
        stepper_move_to((int32_t)rd32(p));
        return RESULT_OK;
    case 0x44:
        if (state != APP_ENABLED) return RESULT_REJECTED;
        stepper_move_by((int32_t)rd32(p));
        return RESULT_OK;
    case 0x48:
        if (state != APP_ENABLED) return RESULT_REJECTED;
        stepper_run((int32_t)rd32(p));
        return RESULT_OK;
    case 0x4C:
        return stepper_set_position((int32_t)rd32(p)) ? RESULT_OK : RESULT_REJECTED;
    case 0x50:
        return command(p[0]);
    default:
        return write_setting(addr, p);
    }
}

/* 完全に含まれるレジスタだけ反映（32bit 値の部分書き込みは無視） */
static void handle_write(const comm_write_t *w)
{
    uint32_t base = w->data[0];
    uint32_t n = (uint32_t)w->len - 1u;
    const uint8_t *d = &w->data[1];
    uint8_t result = RESULT_OK;
    bool any = false;

    for (uint32_t i = 0; i < sizeof writable / sizeof writable[0]; i++) {
        uint32_t a = writable[i].addr;
        if (a >= base && a + writable[i].size <= base + n) {
            uint8_t r = write_reg((uint8_t)a, d + (a - base));
            if (r != RESULT_OK) result = r;
            any = true;
        }
    }
    cmd_result = any ? result : RESULT_INVALID;
}

static uint8_t status_bits(void)
{
    uint8_t s = 0;
    if (stepper_is_running())                      s |= 1u << 0;
    if (drv_is_awake() && drv_mo_low())            s |= 1u << 1;
    if (board_pwr_external())                      s |= 1u << 2;
    if (ui_sw1())                                  s |= 1u << 3;
    if (ui_sw2())                                  s |= 1u << 4;
    if (temp_warn)                                 s |= 1u << 5;
    if (hold_active && state == APP_ENABLED)       s |= 1u << 6;
    if (stepper_at_target())                       s |= 1u << 7;
    return s;
}

static void update_image(void)
{
    uint8_t m[REG_MAP_SIZE];
    memset(m, 0, sizeof m);
    m[0x00] = DEVICE_ID;
    m[0x01] = FW_VERSION_MAJOR;
    m[0x02] = FW_VERSION_MINOR;
    m[0x03] = board_id;
    m[0x04] = (uint8_t)state;
    m[0x05] = fault_bits;
    m[0x06] = status_bits();
    m[0x07] = cmd_result;
    wr32(&m[0x08], (uint32_t)stepper_position());
    wr32(&m[0x0C], (uint32_t)stepper_speed());
    wr16(&m[0x10], (uint16_t)(mon.vm_mv > 0xFFFFu ? 0xFFFFu : mon.vm_mv));
    wr16(&m[0x12], (uint16_t)mon.board_temp_dc);
    wr16(&m[0x14], (uint16_t)mon.mcu_temp_dc);
    wr16(&m[0x16], (uint16_t)mon.ad_in_mv);
    m[0x18] = cfg_status;
    wr16(&m[0x20], cfg.run_current_ma);
    wr16(&m[0x22], cfg.hold_current_ma);
    m[0x24] = cfg.microstep;
    m[0x25] = cfg.flags;
    wr16(&m[0x26], cfg.comm_timeout_ms);
    wr32(&m[0x28], cfg.max_speed);
    wr32(&m[0x2C], cfg.accel);
    wr32(&m[0x30], cfg.decel);
    wr32(&m[0x34], cfg.start_speed);
    wr32(&m[0x38], cfg.jog_speed);
    wr16(&m[0x3C], cfg.hold_delay_ms);
    wr16(&m[0x3E], cfg.steps_per_rev);
    comm_update_image(m, sizeof m);
}

/* ---------------------------------------------------------------- 周期処理 */

static void handle_switches(void)
{
    bool s1 = ui_sw1();
    bool s2 = ui_sw2();

    if (s1 && s2) {
        /* 同時長押し: FAULT → 解除、DISABLED ⇔ ENABLED */
        if (jog_dir != 0) {
            stepper_stop();
            jog_dir = 0;
        }
        if (!chord_done && (now - chord_since) >= CHORD_HOLD_MS) {
            chord_done = true;
            if (state == APP_FAULT) {
                cmd_result = cmd_clear_fault();
            } else if (state == APP_DISABLED) {
                cmd_result = cmd_enable();
            } else if (state == APP_ENABLED) {
                cmd_result = cmd_disable();
            }
        }
        return;
    }
    chord_since = now;
    if (!s1 && !s2) {
        chord_done = false;
    }
    if (chord_done) {
        return;   /* 同時押しの離し途中ではジョグしない */
    }

    int8_t d = s1 ? 1 : (s2 ? -1 : 0);
    if (d == jog_dir) {
        return;
    }
    if (d != 0 && state == APP_ENABLED && (cfg.flags & FLAG_LOCAL_JOG)) {
        stepper_run(d * (int32_t)cfg.jog_speed);
    } else if (jog_dir != 0) {
        stepper_stop();
    }
    jog_dir = d;
}

static void handle_motion(void)
{
    if (state != APP_ENABLED) {
        return;
    }
    bool pending = stepper_has_pending();
    if (pending) {
        if (hold_active) {
            drv_set_current_ma(cfg.run_current_ma, mon.vdda_mv);
            hold_active = false;
            start_allowed_at = now + CURRENT_SETTLE_MS;
        }
        if (time_reached(start_allowed_at)) {
            stepper_service();
        }
    }

    if (stepper_is_running() || pending) {
        idle_since = now;
    } else if (!hold_active && (now - idle_since) >= cfg.hold_delay_ms) {
        drv_set_current_ma(cfg.hold_current_ma, mon.vdda_mv);
        hold_active = true;
    }

    /* 通信タイムアウト（ジョグ中は対象外） */
    if (cfg.comm_timeout_ms != 0 && jog_dir == 0 && (stepper_is_running() || pending) &&
        (now - comm_last_access_ms()) > cfg.comm_timeout_ms) {
        stepper_stop();
    }
}

static void handle_driver_fault(void)
{
    uint32_t pm = __get_PRIMASK();
    __disable_irq();
    uint8_t f = isr_fault;
    isr_fault = 0;
    __set_PRIMASK(pm);

    if (state == APP_FAULT || !drv_is_awake()) {
        return;
    }
    if (f != 0) {
        enter_fault(f);
        return;
    }
    if (!lo_armed) {
        if (time_reached(lo_arm_at)) {
            lo_armed = true;
            drv_fault_irq_arm(true);
        }
        return;
    }
    /* EXTI の取りこぼし・アーム前から Low の場合の保険 */
    drv_fault_t lf = drv_read_fault();
    if (lf != DRV_FAULT_NONE) {
        enter_fault((uint8_t)lf);
    }
}

static uint8_t fault_code(void)
{
    for (uint8_t i = 0; i < 8; i++) {
        if (fault_bits & (1u << i)) {
            return (uint8_t)(i + 1u);
        }
    }
    return 0;
}

void app_init(void)
{
    board_id = board_read_address_id();

    /* 保存設定を読み込む。なければ工場出荷値 */
    settings_defaults(&cfg);
    settings_t t;
    if (settings_load(&t)) {
        cfg_status |= CFG_ST_LOADED;
        if (settings_sanitize(&t)) {
            cfg_status |= CFG_ST_SANITIZED;
        }
        cfg = t;
    }
    apply_stepper_params();
    comm_init((uint8_t)(I2C_ADDR_BASE + board_id));
    state = APP_NO_VM;
}

void app_tick_1ms(uint32_t now_ms)
{
    now = now_ms;
    handle_driver_fault();

    comm_write_t w;
    while (comm_pop_write(&w)) {
        handle_write(&w);
    }

    ui_scan_switches();
    handle_switches();
    handle_motion();

    ui_view_t v = {
        .enabled = state == APP_ENABLED,
        .moving = stepper_is_running(),
        .fault_code = fault_code(),
        .temp_warn = temp_warn,
        .comm_count = comm_access_count(),
    };
    ui_update(now, &v);
    update_image();
}

void app_tick_10ms(uint32_t now_ms)
{
    now = now_ms;
    monitor_sample(&mon);

    /* VM 判定（ヒステリシス + 連続回数） */
    uint32_t lo = vm_ok ? VM_OK_MIN_MV : VM_OK_MIN_MV + VM_HYST_MV;
    uint32_t hi = vm_ok ? VM_OK_MAX_MV : VM_OK_MAX_MV - VM_HYST_MV;
    if (mon.vm_mv >= lo && mon.vm_mv <= hi) {
        vm_bad_count = 0;
        if (!vm_ok) {
            vm_good_ms += 10u;
            if (vm_good_ms >= VM_STABLE_MS) {
                vm_ok = true;
            }
        }
    } else {
        vm_good_ms = 0;
        if (vm_bad_count < 0xFFu) vm_bad_count++;
        if (vm_ok && vm_bad_count >= VM_FAULT_SAMPLES) {
            vm_ok = false;
            vm_bad_reason = mon.vm_mv < lo ? FAULT_VM_UV : FAULT_VM_OV;
        }
    }

    switch (state) {
    case APP_NO_VM:
        if (vm_ok) {
            driver_wake();
            state = APP_DISABLED;
        }
        break;
    case APP_DISABLED:
        if (!vm_ok) {
            driver_sleep();
            state = APP_NO_VM;
        }
        break;
    case APP_ENABLED:
        if (!vm_ok) {
            enter_fault(vm_bad_reason);
        }
        break;
    case APP_FAULT:
        break;
    }

    /* 基板温度 */
    int16_t t = mon.board_temp_dc;
    bool valid = t != TEMP_INVALID;
    temp_warn = valid && t >= BOARD_TEMP_WARN_DC;
    if (valid && t >= BOARD_TEMP_FAULT_DC && (state == APP_DISABLED || state == APP_ENABLED)) {
        enter_fault(FAULT_OVERTEMP);
    }
}
