#include "position.h"
#include "board.h"
#include "config.h"
#include "i2c.h"
#include "symtab.h"

#define AS5600_REG_STATUS   0x0Bu   /* STATUS(0x0B), RAW ANGLE 上位(0x0C)・下位(0x0D) は連続 */
#define AS5600_ST_MH        (1u << 3)
#define AS5600_ST_ML        (1u << 4)
#define AS5600_ST_MD        (1u << 5)

static struct {
    uint16_t raw;          /* 最後に読めた RAW ANGLE（12bit） */
    uint8_t  as_status;    /* 最後に読めた AS5600 STATUS */
    uint8_t  fail_streak;  /* 連続失敗回数（POS_FAIL_LIMIT で飽和）。起動直後は無効扱い */
    bool     magnet;       /* STATUS の MD */
    bool     z_level;      /* PA7_MODE=2 のときの PA7 レベル */
    uint32_t next_try_ms;  /* 無効の間の次の再接続時刻 */
    uint32_t i2c_ok;       /* 成功したトランザクション数 */
    uint32_t i2c_fail;     /* 失敗（タイムアウト・NACK・バスエラー）したトランザクション数 */
} sensor = { .fail_streak = POS_FAIL_LIMIT };

static bool i2c_ready;
static uint8_t pa7_applied = PA7_MODE_AD_IN;   /* リセット直後はアナログ入力（board_init） */

/* ---------------------------------------------------------------- 純粋な計算 */

uint16_t position_axis_count(uint16_t raw, uint16_t offset)
{
    return (uint16_t)((raw + 4096u - offset) & 0x0FFFu);
}

uint16_t position_shaft_angle(uint16_t raw, uint16_t offset)
{
    if (raw > 4095u) {
        return 0xFFFFu;
    }
    uint32_t a = position_axis_count(raw, offset);
    return (uint16_t)((a * 36000u + 2048u) >> 12);
}

int32_t position_sync_steps(uint16_t a, uint16_t gear_ratio, uint16_t steps_per_rev, uint8_t microstep)
{
    uint64_t n = (uint64_t)a * gear_ratio * steps_per_rev * microstep;
    return (int32_t)((n + 2048u) >> 12);
}

/* ---------------------------------------------------------------- I2C1（レジスタ直接、DWT で時間制限） */

static inline uint32_t timeout_cycles(void)
{
    return POS_I2C_TIMEOUT_US * (SystemCoreClock / 1000000U);
}

/* mask のフラグが立つまで待つ。NACK・バスエラー・アビトレーション喪失・時間切れは false */
static bool wait_flag(uint32_t mask, uint32_t t0)
{
    const uint32_t limit = timeout_cycles();
    for (;;) {
        uint32_t isr = I2C1->ISR;
        if (isr & (I2C_ISR_NACKF | I2C_ISR_ARLO | I2C_ISR_BERR)) {
            return false;
        }
        if (isr & mask) {
            return true;
        }
        if ((DWT->CYCCNT - t0) >= limit) {
            return false;
        }
    }
}

/* reg から n バイト（1〜3）を読む。スタート → レジスタ番号 → 再スタート → 読み出し → ストップ */
static bool as5600_read(uint8_t reg, uint8_t *buf, uint8_t n)
{
    const uint32_t t0 = DWT->CYCCNT;
    const uint32_t sadd = AS5600_ADDR7 << 1;

    if (I2C1->ISR & I2C_ISR_BUSY) {
        return false;   /* バス固着 */
    }
    I2C1->ICR = I2C_ICR_NACKCF | I2C_ICR_STOPCF | I2C_ICR_ARLOCF | I2C_ICR_BERRCF;

    I2C1->CR2 = sadd | (1u << I2C_CR2_NBYTES_Pos) | I2C_CR2_START;   /* 書き込み 1 バイト、ストップなし */
    if (!wait_flag(I2C_ISR_TXIS, t0)) {
        return false;
    }
    I2C1->TXDR = reg;
    if (!wait_flag(I2C_ISR_TC, t0)) {
        return false;
    }

    I2C1->CR2 = sadd | I2C_CR2_RD_WRN | ((uint32_t)n << I2C_CR2_NBYTES_Pos) | I2C_CR2_AUTOEND | I2C_CR2_START;
    for (uint8_t i = 0; i < n; i++) {
        if (!wait_flag(I2C_ISR_RXNE, t0)) {
            return false;
        }
        buf[i] = (uint8_t)I2C1->RXDR;
    }
    if (!wait_flag(I2C_ISR_STOPF, t0)) {
        return false;
    }
    I2C1->ICR = I2C_ICR_STOPCF;
    return true;
}

static void i2c_pins(uint32_t mode, uint32_t alt)
{
    GPIO_InitTypeDef g = {0};
    g.Pin = I2C_SENS_SCL_PIN | I2C_SENS_SDA_PIN;
    g.Mode = mode;
    g.Pull = GPIO_NOPULL;
    g.Speed = mode == GPIO_MODE_OUTPUT_OD ? GPIO_SPEED_FREQ_LOW : GPIO_SPEED_FREQ_VERY_HIGH;
    g.Alternate = alt;
    HAL_GPIO_Init(I2C_SENS_PORT, &g);
}

/* 失敗後の復帰: ペリフェラルをリセットし、SDA/SCL が Low のままなら SCL を最大 9 クロック出して
 * スレーブが握っている SDA を解放させ、ストップ条件を出す（FR-POS-04）。約 0.15 ms 以内 */
static void i2c_recover(void)
{
    I2C1->CR1 &= ~I2C_CR1_PE;   /* ライン解放 + 内部状態リセット（3 APB クロック以上保持） */
    (void)I2C1->CR1;
    (void)I2C1->CR1;
    (void)I2C1->CR1;

    if (board_pin_read(I2C_SENS_PORT, I2C_SENS_SDA_PIN) && board_pin_read(I2C_SENS_PORT, I2C_SENS_SCL_PIN)) {
        I2C1->CR1 |= I2C_CR1_PE;
        return;
    }

    board_pin_write(I2C_SENS_PORT, I2C_SENS_SCL_PIN | I2C_SENS_SDA_PIN, true);   /* オープンドレイン: 解放 */
    i2c_pins(GPIO_MODE_OUTPUT_OD, 0);
    board_delay_us(5);
    for (uint8_t i = 0; i < 9 && !board_pin_read(I2C_SENS_PORT, I2C_SENS_SDA_PIN); i++) {
        board_pin_write(I2C_SENS_PORT, I2C_SENS_SCL_PIN, false);
        board_delay_us(5);
        board_pin_write(I2C_SENS_PORT, I2C_SENS_SCL_PIN, true);
        board_delay_us(5);
    }
    /* ストップ条件: SCL High 中に SDA を Low → High */
    board_pin_write(I2C_SENS_PORT, I2C_SENS_SDA_PIN, false);
    board_delay_us(5);
    board_pin_write(I2C_SENS_PORT, I2C_SENS_SCL_PIN, true);
    board_delay_us(5);
    board_pin_write(I2C_SENS_PORT, I2C_SENS_SDA_PIN, true);
    board_delay_us(5);

    i2c_pins(GPIO_MODE_AF_OD, GPIO_AF4_I2C1);
    I2C1->CR1 |= I2C_CR1_PE;
}

void position_init(void)
{
    /* CubeMX 生成の MX_I2C1_Init は Timing が 80 MHz 用でなく（約 2 MHz になる）、main からも呼ばれないため、
     * ここで同じハンドル（hi2c1）を正しいタイミングで初期化する。GPIO とクロックは HAL_I2C_MspInit */
    hi2c1.Instance = I2C1;
    hi2c1.Init.Timing = POS_I2C_TIMING;
    hi2c1.Init.OwnAddress1 = 0;
    hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
    hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
    hi2c1.Init.OwnAddress2 = 0;
    hi2c1.Init.OwnAddress2Masks = I2C_OA2_NOMASK;
    hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
    hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
    i2c_ready = HAL_I2C_Init(&hi2c1) == HAL_OK &&
                HAL_I2CEx_ConfigAnalogFilter(&hi2c1, I2C_ANALOGFILTER_ENABLE) == HAL_OK;
}

/* ---------------------------------------------------------------- センサ状態 */

void position_poll_10ms(uint32_t now_ms)
{
    if (pa7_applied == PA7_MODE_Z) {
        sensor.z_level = board_pin_read(AD_IN_PORT, AD_IN_PIN);
    }

    if (sensor.fail_streak >= POS_FAIL_LIMIT && (int32_t)(now_ms - sensor.next_try_ms) < 0) {
        return;   /* 無効の間は 1 秒ごとに再接続を試す */
    }

    uint8_t b[3];
    if (i2c_ready && as5600_read(AS5600_REG_STATUS, b, 3)) {
        sensor.i2c_ok++;
        sensor.fail_streak = 0;
        sensor.as_status = b[0];
        sensor.magnet = (b[0] & AS5600_ST_MD) != 0;
        sensor.raw = (uint16_t)(((b[1] & 0x0Fu) << 8) | b[2]);
    } else {
        sensor.i2c_fail++;
        if (sensor.fail_streak < POS_FAIL_LIMIT) {
            sensor.fail_streak++;
        }
        if (sensor.fail_streak >= POS_FAIL_LIMIT) {
            sensor.next_try_ms = now_ms + POS_RETRY_MS;
        }
        if (i2c_ready) {
            i2c_recover();
        }
    }
}

bool position_valid(void)
{
    return sensor.fail_streak < POS_FAIL_LIMIT && sensor.magnet;
}

uint16_t position_raw(void)
{
    return position_valid() ? sensor.raw : 0xFFFFu;
}

uint8_t position_status_bits(void)
{
    uint8_t s = 0;
    bool responding = sensor.fail_streak < POS_FAIL_LIMIT;
    if (position_valid())                              s |= 1u << 0;
    if (responding && (sensor.as_status & AS5600_ST_MH)) s |= 1u << 1;
    if (responding && (sensor.as_status & AS5600_ST_ML)) s |= 1u << 2;
    if (!responding)                                   s |= 1u << 3;
    if (pa7_applied == PA7_MODE_Z && sensor.z_level)   s |= 1u << 5;
    return s;
}

/* ---------------------------------------------------------------- PA7（J10.5） */

static void pa7_cfg(uint32_t mode)
{
    GPIO_InitTypeDef g = {0};
    g.Pin = AD_IN_PIN;
    g.Mode = mode;
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(AD_IN_PORT, &g);
}

void position_pa7_set(uint8_t mode, bool dir_level)
{
    if (mode > PA7_MODE_Z) {
        return;
    }
    if (mode == pa7_applied) {
        if (mode == PA7_MODE_DIR) {
            board_pin_write(AD_IN_PORT, AD_IN_PIN, dir_level);   /* FLAGS bit2 の変更を即時に反映 */
        }
        return;
    }

    pa7_cfg(GPIO_MODE_ANALOG);   /* 一度アナログに戻してから新しい機能を設定する */
    sensor.z_level = false;
    if (mode == PA7_MODE_DIR) {
        board_pin_write(AD_IN_PORT, AD_IN_PIN, dir_level);   /* 出力に切り替える前にレベルを決めておく */
        pa7_cfg(GPIO_MODE_OUTPUT_PP);
    } else if (mode == PA7_MODE_Z) {
        pa7_cfg(GPIO_MODE_INPUT);
        sensor.z_level = board_pin_read(AD_IN_PORT, AD_IN_PIN);
    }
    pa7_applied = mode;
}

/* ---------------------------------------------------------------- USB 名前表（位置センサ） */

SYM_ENTRY(sensor.raw, SYM_U16, 1);          /* 最後に読めた RAW ANGLE（無効でも最後の値を保持） */
SYM_ENTRY(sensor.as_status, SYM_U8, 1);     /* AS5600 STATUS（bit3 MH、bit4 ML、bit5 MD） */
SYM_ENTRY(sensor.fail_streak, SYM_U8, 1);   /* 連続失敗回数 */
SYM_ENTRY(sensor.z_level, SYM_U8, 1);       /* Z 入力レベル（PA7_MODE=2） */
SYM_ENTRY(sensor.i2c_ok, SYM_U32, 1);       /* I2C 成功回数 */
SYM_ENTRY(sensor.i2c_fail, SYM_U32, 1);     /* I2C 失敗回数 */
SYM_ENTRY(pa7_applied, SYM_U8, 1);          /* PA7 に設定中の機能 */
