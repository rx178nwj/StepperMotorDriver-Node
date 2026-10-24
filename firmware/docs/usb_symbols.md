# USB 名前表の登録一覧

要求仕様 FR-USB-35〜38。SYM_FIND / SYM_UPLOAD で名前を指定して読み出せる変数。
登録は各 `.c` ファイル末尾の `SYM_ENTRY(...)` 1 行（`App/Inc/symtab.h`）。名前は変数名（または式）そのもの。
INDEX はリンク順で決まるため版ごとに変わる。PC は SYM_FIND（大文字小文字を区別、完全一致）で引くこと。

TYPE：1 u8、2 i8、3 u16、4 i16、5 u32、6 i32、7 f32、0x10 バイト列（NUM = バイト数）。サイズ = 要素サイズ × NUM。
8 バイト以下の変数は割り込みを禁止してコピーする。それより大きい変数（`cfg`・`comm_stats`・`usb_stats`）は 32bit 単位のコピーで、フィールド間の同時性は保証しない。

登録数は 104（2026-10-24 時点。`arm-none-eabi-size -A` の `.sym_table` ÷ 12 で確認できる）。

## 状態遷移（`App/Src/app.c`）

| 名前 | TYPE | 内容 |
|---|---|---|
| `state` | u8 | STATE レジスタ。0 NO_VM、1 DISABLED、2 ENABLED、3 FAULT |
| `fault` | u8 | FAULT レジスタ（実体は `fault_bits`）。bit0 OPD、1 ISD、2 TSD、3 VM 低電圧、4 VM 過電圧、5 基板過熱 |
| `status` | u8 | STATUS レジスタ。1 ms 周期で更新するスナップショット（実体は `status_snapshot`） |
| `cmd_result` | u8 | CMD_RESULT レジスタ |
| `cfg_status` | u8 | CONFIG_STATUS レジスタ |
| `isr_fault` | u8 | LO1/LO2 割り込みで立てた異常ビット（メインが取り込むまで保持） |
| `vm_ok` | u8 | VM が範囲内で安定しているか |
| `hold_active` | u8 | 保持電流中か |
| `temp_warn` | u8 | 基板過熱警告 |
| `lo_armed` | u8 | LO 異常の検出が有効か |

## パルス生成・加減速（`App/Src/stepper.c`）

| 名前 | TYPE | 内容 |
|---|---|---|
| `position` | i32 | 現在位置 [step] |
| `cur_speed` | f32 | 現在速度 [step/s]（符号付き、停止中は 0） |
| `target_pos` | i32 | 目標位置 [step] |
| `target_vel` | f32 | 速度モードの目標速度 [step/s] |
| `mode` | u8 | 0 IDLE、1 POS（位置決め）、2 VEL（速度） |
| `running` | u8 | パルス出力中 |
| `touched` | u8 | 起動後に一度でもパルス出力を開始した、または SET_POSITION 等で位置を再定義した（位置センサの自動同期の判断用） |
| `dir` | i8 | 現在の移動方向（+1/-1） |
| `plan_speed` | f32 | プランナが直前に確定したステップの速度（実体は `v`。加減速の内部状態） |
| `commit_pos` | i32 | 確定済みステップを全て出した後の位置（プランナの先行位置） |
| `prm.max_speed` | f32 | プランナに設定した最高速度 [step/s]（範囲制限後） |
| `prm.accel` | f32 | 加速度 [step/s²] |
| `prm.decel` | f32 | 減速度 [step/s²] |
| `prm.start_speed` | f32 | 開始速度 [step/s] |
| `prm.invert_dir` | u8 | 方向反転 |

## 監視（`App/Src/app.c`、`App/Src/monitor.c`）

| 名前 | TYPE | 内容 |
|---|---|---|
| `mon.vdda_mv` | u32 | VDDA [mV]（VREFINT から算出） |
| `mon.vm_mv` | u32 | VM [mV] |
| `mon.board_temp_dc` | i16 | 基板温度 [0.1 °C]（無効は -32768） |
| `mon.mcu_temp_dc` | i16 | MCU 温度 [0.1 °C] |
| `mon.ad_in_mv` | u32 | J10 アナログ入力 [mV]。PA7_MODE≠0 のときは計測せず 0xFFFF |
| `adc_raw.vrefint` | u16 | ADC 生値（12bit）VREFINT |
| `adc_raw.vm` | u16 | ADC 生値 PA0（VM 分圧） |
| `adc_raw.board_temp` | u16 | ADC 生値 PA6（NTC） |
| `adc_raw.ad_in` | u16 | ADC 生値 PA7（J10.5） |
| `adc_raw.mcu_temp` | u16 | ADC 生値 内部温度センサ |

## RAM 上の設定（`App/Src/app.c`、要求仕様 3.7 節）

`cfg` は `settings_t` 全体（40 バイト、TYPE 0x10。レコード版 2 で 32 → 40 バイトに拡張）。個別のフィールドも登録している。

| 名前 | TYPE | 内容（レジスタ） |
|---|---|---|
| `cfg` | 0x10 ×40 | 設定全体 |
| `cfg.run_current_ma` | u16 | 運転電流 [mA]（0x20） |
| `cfg.hold_current_ma` | u16 | 保持電流 [mA]（0x22） |
| `cfg.microstep` | u8 | 分割（0x24） |
| `cfg.flags` | u8 | FLAGS（0x25） |
| `cfg.comm_timeout_ms` | u16 | 通信タイムアウト [ms]（0x26） |
| `cfg.max_speed` | u32 | 最高速度（0x28） |
| `cfg.accel` | u32 | 加速度（0x2C） |
| `cfg.decel` | u32 | 減速度（0x30） |
| `cfg.start_speed` | u32 | 開始速度（0x34） |
| `cfg.jog_speed` | u32 | ジョグ速度（0x38） |
| `cfg.hold_delay_ms` | u16 | 保持移行時間 [ms]（0x3C） |
| `cfg.steps_per_rev` | u16 | 1 回転のフルステップ数（0x3E） |
| `cfg.gear_ratio` | u16 | ギヤ比（0x54） |
| `cfg.sensor_offset` | u16 | センサ原点オフセット（0x56） |
| `cfg.pa7_mode` | u8 | PA7 の機能（0x58）。0 AD_IN、1 DIR、2 Z |
| `cfg.j10_mode` | u8 | J10 の機能 J10_MODE（0x59）。0 未使用、1 ENC、2 HOME |

## 位置センサ AS5600（`App/Src/position.c`、`App/Src/app.c`、要求仕様 3.10 節 FR-POS-10）

| 名前 | TYPE | 内容 |
|---|---|---|
| `sensor.raw` | u16 | 最後に読めた RAW ANGLE（0〜4095）。無効になっても最後の値を保持する（レジスタ 0x1A は無効時 0xFFFF） |
| `sensor.as_status` | u8 | 最後に読めた AS5600 STATUS（bit3 MH、bit4 ML、bit5 MD） |
| `sensor.fail_streak` | u8 | 連続失敗回数（3 で無効。起動直後は 3） |
| `sensor.z_level` | u8 | Z 入力レベル（PA7_MODE=2 のとき。それ以外は 0） |
| `sensor.i2c_ok` | u32 | I2C トランザクションの成功回数 |
| `sensor.i2c_fail` | u32 | I2C トランザクションの失敗回数（タイムアウト・NACK・バスエラー・バス固着） |
| `pa7_applied` | u8 | PA7 に実際に設定している機能（0 AD_IN、1 DIR、2 Z） |
| `sensor_status` | u8 | SENSOR_STATUS レジスタ（10 ms 周期のスナップショット。実体は `sensor_status_snapshot`） |
| `shaft_angle` | u16 | SHAFT_ANGLE レジスタ [0.01°]（10 ms 周期のスナップショット。無効時 0xFFFF。実体は `shaft_angle_snapshot`） |

## J10・原点復帰・AGC（`App/Src/app.c`、`App/Src/j10.c`、要求仕様 3.12 節）

USB のメモリ読み出しはペリフェラル領域（TIM2・GPIO）を読めない（FR-USB-31）。端子・TIM2 を読む値や関数で組み立てる値は、周期処理で RAM に写したスナップショットを登録している。

| 名前 | TYPE | 内容 |
|---|---|---|
| `input_status` | u8 | INPUT_STATUS レジスタ（0x1E）と同じ値（1 ms 周期のスナップショット）。bit0 HOME1 作動、1 HOME2 作動、2 原点復帰中、3 原点復帰完了、4 原点復帰失敗、5 リミット停止 |
| `enc_count` | i32 | ENC_COUNT レジスタ（0x5C）と同じ値（1 ms 周期のスナップショット。TIM2->CNT。J10_MODE≠1 のときは 0） |
| `agc_out` | u8 | AGC0/AGC1 端子の出力レベル（10 ms 周期のスナップショット。`drv_get_agc()`。1 = 両方 High） |
| `limit_stopped` | u8 | INPUT_STATUS bit5 の元（リミットで停止した。次に受け付けた移動指令で 0） |
| `home.state` | u8 | 原点復帰の状態。0 IDLE、1 SEEK、2 SEEK_STOP、3 BACK、4 BACK_STOP、5 DONE、6 FAILED |
| `home.side` | i8 | 原点復帰の指定側。+1 = HOME1（HOME_POS）、-1 = HOME2（HOME_NEG） |
| `home.seek_speed` | i32 | 探索速度 [step/s]（正） |
| `home.back_speed` | i32 | 戻り速度 [step/s]（正） |
| `home.limit_steps` | u32 | 戻りで動いてよい上限 [step] |
| `home.start_pos` | i32 | 戻りを始めた位置 [step] |
| `home.opp_prev` | u8 | 前回の反対側の入力（SEEK で反対側が新たに作動したことの検出用） |
| `hm1.active` | u8 | HOME1 の確定した作動状態（J10_MODE=2 のときだけ。デバウンス後） |
| `hm1.cand` | u8 | HOME1 の直近のサンプル（作動 = 1。デバウンス中の生の値） |
| `hm1.count` | u8 | HOME1 のサンプルが連続した回数（確定は `HOME_CONFIRM_SAMPLES` 回） |
| `hm2.active` | u8 | HOME2 の確定した作動状態 |
| `hm2.cand` | u8 | HOME2 の直近のサンプル |
| `hm2.count` | u8 | HOME2 のサンプルが連続した回数 |

## CAN 統計（`App/Src/comm.c`）

`comm_stats` は構造体全体（36 バイト、TYPE 0x10）。

| 名前 | TYPE | 内容 |
|---|---|---|
| `comm_stats` | 0x10 ×36 | CAN 統計全体 |
| `comm_stats.rx_frames` | u32 | 受信フレーム数（フィルタ通過後） |
| `comm_stats.rx_dropped` | u32 | 受信リングバッファあふれで捨てた数 |
| `comm_stats.tx_frames` | u32 | 送信メールボックスへ入れた数 |
| `comm_stats.tx_dropped` | u32 | メールボックス満杯で捨てた数 |
| `comm_stats.err_events` | u32 | エラー割り込みの回数 |
| `comm_stats.err_passive` | u32 | エラーパッシブへ遷移した回数 |
| `comm_stats.err_busoff` | u32 | バスオフ回数 |
| `comm_stats.err_rx_overrun` | u32 | 受信 FIFO オーバーラン回数 |
| `comm_stats.err_last_code` | u32 | 直近の HAL_CAN_GetError() 値 |

## USB 統計（`App/Src/usb_proto.c`）

`usb_stats` は構造体全体（32 バイト、TYPE 0x10）。

| 名前 | TYPE | 内容 |
|---|---|---|
| `usb_stats` | 0x10 ×32 | USB 統計全体 |
| `usb_stats.rx_frames` | u32 | CRC まで正しく受信したフレーム数 |
| `usb_stats.crc_errors` | u32 | CRC 不一致で捨てたフレーム数 |
| `usb_stats.dropped` | u32 | 捨てた回数の合計（受信リングあふれの発生回数 + `rx_timeouts` + `tx_dropped`） |
| `usb_stats.rx_overflow` | u32 | 受信リングバッファあふれで捨てたバイト数 |
| `usb_stats.rx_timeouts` | u32 | フレーム途中で 20 ms 途切れて捨てた数 |
| `usb_stats.bad_len` | u32 | LEN が範囲外で SOF を探し直した回数 |
| `usb_stats.tx_frames` | u32 | 送信を USB ミドルウェアへ渡したフレーム数 |
| `usb_stats.tx_dropped` | u32 | 50 ms 送信できず捨てた応答・イベント数。送信が詰まって応答を送れないため実行せず捨てた要求、未送信のまま新しいイベントで上書きされたイベントも含む |

## 診断（`App/Src/diag.c`）

デバッグ用。PC 側でファーム内部の状態（稼働時間・リセット要因・ループ周期・スタック使用量）を見るための値。

| 名前 | TYPE | 内容 |
|---|---|---|
| `uwTick` | u32 | 稼働時間 [ms]（HAL の SysTick カウンタ。約 49.7 日で 0 に戻る） |
| `reset_csr` | u32 | 起動時の `RCC->CSR` の生値（読んだ直後にフラグをクリアするので、次回の起動では今回の要因が混ざらない） |
| `loop.max_us` | u32 | メインループ 1 周の最大周期 [µs]（起動後。リセットするコマンドは無い） |
| `loop.max_1s_us` | u32 | 直前に完了した 1 秒窓（`uwTick` で 1000 ms ごと）の最大周期 [µs] |
| `loop.rate_hz` | u32 | 直前の 1 秒窓のメインループ回数 |
| `stack.used_max` | u32 | スタック使用量の最大値 [バイト]（起動後） |
| `stack.size` | u32 | 計測対象のスタック領域の大きさ [バイト]（`_end` から RAM 上端まで） |

`reset_csr` の主なビット（`stm32l433xx.h` の `RCC_CSR_*RSTF`）：

| ビット | 名前 | 意味 |
|---|---|---|
| 31 | LPWRRSTF | 低消費電力モード移行によるリセット |
| 30 | WWDGRSTF | ウィンドウウォッチドッグ |
| 29 | IWDGRSTF | 独立ウォッチドッグ（IWDG） |
| 28 | SFTRSTF | ソフトウェアリセット（`ENTER_BOOTLOADER` による `NVIC_SystemReset` を含む） |
| 27 | BORRSTF | ブラウンアウト（電源投入でも立つ） |
| 26 | PINRSTF | NRST ピン |
| 25 | OBLRSTF | オプションバイトのロード |
| 24 | FWRSTF | ファイアウォール |

ループ周期の注意：周期は `DWT->CYCCNT` の差分から求める。1 周にはスタック走査（1 ms ごとに最大 64 語）や `SAVE_SETTINGS` の Flash 書き込み（約 25 ms）が含まれうるので、`loop.max_us` はそれらの影響で大きくなる。

スタックの注意：`stack.used_max` は割り込み（ISR）で使った分も含む。起動直後に現在の SP より 512 バイト下までを `0xA5A5A5A5` で塗り、塗りが崩れた最も低い位置から求める（1 回に 64 語ずつ走査するので反映は遅れる）。それより上は塗らないため、最小値は塗り残し分（初期化時点の SP から 512 バイト下より上の使用分）になる。

## 登録の追加方法

```c
#include "symtab.h"
...
SYM_ENTRY(my_var, SYM_U32, 1);               // 名前 "my_var"
SYM_ENTRY(my_struct.field, SYM_I16, 1);      // 名前 "my_struct.field"
SYM_ENTRY(my_array, SYM_U8, 16);             // 配列（NUM = 要素数）
SYM_ENTRY(my_struct, SYM_BYTES, sizeof(my_struct));
SYM_ENTRY_AS("short_name", long_variable_name, SYM_F32, 1);
```

`sizeof(変数)` と 要素サイズ × NUM が一致しない、または名前が 31 文字を超えるとコンパイルエラーになる。
