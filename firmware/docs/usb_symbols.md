# USB 名前表の登録一覧

要求仕様 FR-USB-35〜38。SYM_FIND / SYM_UPLOAD で名前を指定して読み出せる変数。
登録は各 `.c` ファイル末尾の `SYM_ENTRY(...)` 1 行（`App/Inc/symtab.h`）。名前は変数名（または式）そのもの。
INDEX はリンク順で決まるため版ごとに変わる。PC は SYM_FIND（大文字小文字を区別、完全一致）で引くこと。

TYPE：1 u8、2 i8、3 u16、4 i16、5 u32、6 i32、7 f32、0x10 バイト列（NUM = バイト数）。サイズ = 要素サイズ × NUM。
8 バイト以下の変数は割り込みを禁止してコピーする。それより大きい変数（`cfg`・`comm_stats`・`usb_stats`）は 32bit 単位のコピーで、フィールド間の同時性は保証しない。

登録数は 66（2026-10-18 時点。`arm-none-eabi-size -A` の `.sym_table` ÷ 12 で確認できる）。

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
| `mon.ad_in_mv` | u32 | J10 アナログ入力 [mV] |
| `adc_raw.vrefint` | u16 | ADC 生値（12bit）VREFINT |
| `adc_raw.vm` | u16 | ADC 生値 PA0（VM 分圧） |
| `adc_raw.board_temp` | u16 | ADC 生値 PA6（NTC） |
| `adc_raw.ad_in` | u16 | ADC 生値 PA7（J10.5） |
| `adc_raw.mcu_temp` | u16 | ADC 生値 内部温度センサ |

## RAM 上の設定（`App/Src/app.c`、要求仕様 3.7 節）

`cfg` は `settings_t` 全体（32 バイト、TYPE 0x10）。個別のフィールドも登録している。

| 名前 | TYPE | 内容（レジスタ） |
|---|---|---|
| `cfg` | 0x10 ×32 | 設定全体 |
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
| `usb_stats.tx_dropped` | u32 | 50 ms 送信できず捨てた応答・イベント数 |

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
