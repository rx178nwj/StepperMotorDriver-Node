/*
 * config.h — 回路定数・保護しきい値・既定値
 * 「暫定」と付いたものは docs/requirements.md 5章の未確定事項。
 */
#ifndef CONFIG_H
#define CONFIG_H

#define FW_VERSION_MAJOR        0
#define FW_VERSION_MINOR        3
#define DEVICE_ID               0xA3

/* ---- クロック ---- */
#define SYSCLK_HZ               80000000UL
#define STEP_TIMER_HZ           80000000UL   /* TIM1 (APB2 = 80 MHz) */

/* ---- VREF / 電流設定 ----
 * 分圧なし: PA4(DAC) → R34 10k（直列、C14 と RC フィルタ）→ VREFA/B。R32 は未実装。
 * IOUT = VREF × 1.25。DAC バッファ出力は約 0.2 V〜VDDA−0.2 V（約 250〜3870 mA 相当）。 */
#define VREF_DIV_RATIO          1.0f         /* VREF / V_DAC */
#define DRV_VREF_GAIN_A_PER_V   1.25f
#define DAC_OUT_MIN_MV          200u         /* バッファ有効時の下限 */
#define CURRENT_MIN_MA          250u         /* = DAC_OUT_MIN_MV × 1.25 */
#define CURRENT_LIMIT_MA        3000u        /* 3A モータ、J3(XH) 3A 定格に合わせた上限 */

/* ---- VM 計測（R37 180k / R38 10k → ×19） ---- */
#define VM_DIVIDER_RATIO        19.0f
#define VM_OK_MIN_MV            10500u
#define VM_OK_MAX_MV            28000u
#define VM_HYST_MV              500u
#define VM_STABLE_MS            50u          /* 投入後この時間安定したら起動 */
#define VM_FAULT_SAMPLES        2u           /* 励磁中の範囲外連続回数（10 ms 周期） */

/* ---- 基板温度（+3V3 → R14 4.7k → TEMP → NTC → GND） ---- */
#define NTC_PULLUP_OHM          4700.0f
#define NTC_R25_OHM             10000.0f     /* Murata NCP18XH103F03RB */
#define NTC_BETA                3434.0f      /* B25/85（保護しきい値付近で精度を優先。B25/50 = 3380） */
#define BOARD_TEMP_WARN_DC      700          /* 0.1 °C 単位 */
#define BOARD_TEMP_FAULT_DC     800
#define TEMP_INVALID            ((int16_t)0x8000)

/* ---- CAN（bxCAN、CAN 2.0A）----
 * ビットレートは 125000 / 250000 / 500000 / 1000000 から選ぶ（暫定 500 kbit/s、要求仕様 U-1）。
 * 1 ビット = 16 tq（SYNC 1 + BS1 13 + BS2 2、サンプル点 87.5%）、SJW = 1 tq。 */
#define CAN_BITRATE_BPS         500000UL
#define CAN_PCLK_HZ             80000000UL   /* APB1 = 80 MHz */
#define CAN_TQ_PER_BIT          16UL
#define CAN_PRESCALER           (CAN_PCLK_HZ / (CAN_BITRATE_BPS * CAN_TQ_PER_BIT))
#if (CAN_PRESCALER < 1UL) || (CAN_PRESCALER > 1024UL) || \
    ((CAN_PRESCALER * CAN_BITRATE_BPS * CAN_TQ_PER_BIT) != CAN_PCLK_HZ)
#error "CAN_BITRATE_BPS cannot be generated exactly from CAN_PCLK_HZ with 16 tq/bit"
#endif

/* CAN ID（要求仕様 FR-COM-03）。ID = DIP 値 0〜7 */
#define CAN_ID_BROADCAST        0x000u       /* 上位 → 全台 */
#define CAN_ID_FAULT_BASE       0x080u       /* 基板 → 上位（異常通知）+ ID */
#define CAN_ID_REQUEST_BASE     0x100u       /* 上位 → 基板（要求）+ ID */
#define CAN_ID_RESPONSE_BASE    0x180u       /* 基板 → 上位（応答）+ ID */

#define COMM_LED_ERR_BLINK_MS   500u         /* エラーパッシブ／バスオフ中の LED_COMM 点滅周期（2 Hz） */

/* ---- モーション ---- */
#define STEP_RATE_MAX           100000u      /* step/s */
#define STEP_PULSE_NS           1000u        /* CLK High 幅 (データシート最小 500 ns) */
#define CURRENT_SETTLE_MS       5u           /* 保持→運転電流切替後の待ち（R34×C14 = 1 ms の約 5 倍） */
#define DRV_WAKE_US             20u          /* スタンバイ解除後の待ち（データシート 7.5 µs） */
#define LO_ARM_DELAY_MS         5u           /* 起動直後の LO 判定マスク */

/* ---- 工場出荷値（保存設定がない場合。未知のモータでも安全な電流にしている） ---- */
#define DEF_RUN_CURRENT_MA      1000u
#define DEF_HOLD_CURRENT_MA     500u
#define DEF_MICROSTEP           16u
#define DEF_FLAGS               0x02u        /* bit1 ローカルジョグ有効 */
#define DEF_MAX_SPEED           3200u
#define DEF_ACCEL               16000u
#define DEF_DECEL               16000u
#define DEF_START_SPEED         200u
#define DEF_JOG_SPEED           1600u
#define DEF_HOLD_DELAY_MS       500u
#define DEF_STEPS_PER_REV       200u         /* 1.8°/step */

#endif /* CONFIG_H */
