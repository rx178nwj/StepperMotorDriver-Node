# ファームウェア実装計画 — requirements.md 0.8 への追従

| 項目 | 内容 |
|---|---|
| 対象仕様 | [requirements.md](requirements.md) 版 0.8（2026-10-20） |
| 基準コミット | 73ab552 |
| 作成日 | 2026-10-20 |

## 1. 現状

P1 の要求（FR-SYS / DRV / MOT / MON / UI / NV / COM / USB-01〜61）と、P2 の位置センサ（FR-POS-01〜11）は、ほぼすべてコードに対応箇所がある。未実装の機能は見つかっていない。

| 区分 | 状況 | 主なファイル |
|---|---|---|
| 起動・ドライバ・パルス生成 | 実装済み | `App/Src/app_main.c`, `board.c`, `drv.c`, `stepper.c` |
| 監視・保護・状態遷移 | 実装済み（仕様差 2 件：2.1 A2・A3） | `App/Src/app.c`, `monitor.c` |
| LED・スイッチ | 実装済み | `App/Src/ui.c`, `app.c` |
| 設定の Flash 保存 | 実装済み | `App/Src/settings.c`, `App/STM32L433xx_app.ld` |
| CAN 通信・レジスタマップ | 実装済み | `App/Src/comm.c`, `app.c` |
| USB CDC・名前表 | 実装済み（仕様差 1 件：A1） | `App/Src/usb_proto.c`, `symtab.c` |
| PC ツール nemadbg | 実装済み（再送の注意 1 件：A4） | `tools/nemadbg/` |
| AS5600・PA7 切り替え | 実装済み | `App/Src/position.c`, `app.c` |
| ホスト単体テスト | USB と名前表だけ（21 件） | `tests/host/test_usb.c` |
| 実機試験（6 章の 1〜30） | 未実施 | — |

残る作業は、(A) 仕様との差の解消、(B) 文書の更新、(C) ホスト単体テストの拡充、(D) 実機試験、の 4 つ。P2 の残り（AGC、J10 のエンコーダ・原点・リミット、ブートローダ移行）は requirements.md 0.9 で要求を決め、フェーズ E として 3 章で計画する。

## 2. 作業項目

### 2.1 フェーズ A：仕様との差の解消

A2・A3・A4 は 2026-10-20 にユーザーが推奨案に決定した。

| No | 要求 | 現状 | 対応 | 対象 | 決定 |
|---|---|---|---|---|---|
| A1 | FR-USB-33 | ECC エラー（NMI）が起きても `mem_copy` が最後まで読み、その後に ERR 0x36 を返す（`usb_proto.c:100-112`） | `mem_copy` のループごとに `nv_ecc_error` を確認して中止する。ホストテストに「途中で ECC が立つと読み出しを中止し 0x36」を追加 | `usb_proto.c`, `tests/host/` | — |
| A2 | FR-MON-04 | 励磁中の VM 範囲外を 10 ms 周期で 2 回連続確認してから FAULT（最大約 20 ms 遅れ、`app.c:630-636`、`config.h` の `VM_FAULT_SAMPLES=2`） | **仕様を実装に合わせる**：「10 ms 周期で 2 回連続範囲外のとき」と明記する。1 回で判定すると ADC ノイズ・モータ起動時の電圧低下で誤検出しやすい。実装は変えない | `requirements.md` | 済 |
| A3 | FR-MON-08 | CLEAR_FAULT は温度と VM だけ確認し、LO 異常（OPD/ISD/TSD）の原因は確認しない（`app.c:138-154`） | **仕様を実装に合わせる**：OPD・ISD は出力 ON でないと検出できず、解除前には確認できない。「LO 異常は解除し、原因が残っていれば次の ENABLE 後に LO 検出で再び FAULT になる」と明記する | `requirements.md` | 済 |
| A4 | FR-USB-52 | nemadbg はタイムアウト時に同じ要求を最大 3 回再送する。FW が実行して応答だけ遅れた・失われた場合、`can-req` の書き込み（MOVE_REL など）とブロードキャストが二重実行される（`tools/nemadbg/link.py:111-132`） | **ツール側で対処**：CAN_REQUEST のうち書き込み（OP 0x01）とブロードキャスト（CAN_ID 0x000）は再送しない。タイムアウト時は「実行されたかは不明。regs で確認」と表示する。読み出し系は従来どおり再送する。FR-USB-52 に例外を追記する | `tools/nemadbg/link.py`, `cli.py`, `tools/tests/`, `requirements.md` | 済 |
| A5 | 3.8 節 | ACCEL・DECEL の範囲 1〜10,000,000 は仕様に記載がない（`settings.c:111-112`） | 仕様のレジスタ表に範囲を追記する（実装は変えない） | `requirements.md` | — |
| A6 | FR-COM-10/12 | CAN の異常通知は、送信保留中に次の通知が来ると上書きされるが tx_dropped に計上しない（`comm.c:201-210`）。USB 側は計上する | USB と同じく上書きを tx_dropped に 1 計上する | `comm.c` | — |

A4 は FW 側で重複を除く案（同じ CTR の再送には前回の応答を返す）も検討したが、CCP にない動作で FR-USB-05 も変わるため採らない。

### 2.2 フェーズ B：文書と版数の更新

| No | 内容 | 対象 |
|---|---|---|
| B1 | CubeMX 設定手順を現行の .ioc に合わせる（I2C2 削除、PB8/PB9 = CAN1、PB10/PB11 = AGC、PC15 = CAN_S、USB・HSI48・CRS、I2C1、`src/` などの古いパス） | `docs/cubemx_setup.md` |
| B2 | 電源投入試験の手順を更新する（AGC を PB10/PB11 に、FW 版の記載、CAN_S の初期レベルの確認を追加） | `docs/test_poweron_safe_state.md` |
| B3 | コメントのリンカスクリプト名「stm32l433cc_app.ld」を `STM32L433xx_app.ld` に直す | `App/Src/settings.c:9` |
| B4 | FW 版数を決める（現在 `config.h` の `FW_VERSION` は 0.3）。A の反映後に 0.4 へ上げる案 | `App/Inc/config.h` |
| B5 | requirements.md を 0.10 にし、A2〜A5 の仕様変更を「0.10 での変更」に記載する（0.9 は P2 要求の追加に使用済み） | `docs/requirements.md` |

### 2.3 フェーズ C：ホスト単体テストの拡充

現在のホストテストは `usb_proto.c` と `symtab.c` だけを対象にしている。実機に載せる前に、ハードウェアに依存しない計算と判定を確認できるようにする。既存の `tests/host/` の仕組み（実物の .c をそのままビルドし、HAL をスタブにする）を使う。

| No | 対象 | 確認する内容 | 関連試験 |
|---|---|---|---|
| C1 | `comm.c` の `comm_process_request` | 書き込み・読み出し・不正要求、DLC・LEN の境界、フィルタ（他局・ブロードキャスト）、32bit の部分書き込み拒否 | 試験 9 |
| C2 | `settings.c` | `settings_sanitize` の範囲補正、最新連番の選択、CRC 不正・先頭 8 バイト未書き込みのレコードを無視、ページ切り替え（Flash を RAM 配列で模擬） | 試験 12〜15 |
| C3 | `stepper.c` の `plan_next` / `make_period` | 台形の加速・巡航・減速、目標位置での停止、速度クランプ | 試験 5 |
| C4 | `position.c` の計算関数 | `position_axis_count`・`position_shaft_angle` の四捨五入と境界、`position_sync_steps` の 64bit 計算（ギヤ 1000・分割 32） | 試験 23・25 |

C1・C2 は `app.c` の状態とレジスタに依存するため、スタブの規模を見て、難しければ C3・C4 を先に行う。テストの期待値は requirements.md から決め、実装に合わせて書かない。

### 2.4 フェーズ D：実機試験

requirements.md 6 章の試験を、危険の少ない順に行う。結果は `docs/test_results.md`（新規）に記録する。

| 段階 | 試験 | 前提・機材 |
|---|---|---|
| D1 電源なしで安全 | 1（電源投入の安全状態）、2（USB 給電のみ）、11（IWDG） | ロジアナ、テスタ、`TestPowerOn` プリセット |
| D2 USB | 17〜20、29、30 | nemadbg、ST-LINK（値の照合） |
| D3 計測 | 3（VM ±2%）、6・16（VREF） | 安定化電源 12/24 V、テスタ |
| D4 モータ無負荷 | 4、5、10、12〜15 | モータ、ロジアナ（CLK 波形） |
| D5 保護 | 7（OPD）、8（VM 遮断） | 電源を切れる治具 |
| D6 CAN | 9、9a、9b、21 | CAN アナライザ、2 台以上の基板 |
| D7 負荷と同時動作 | 22、26（100k step/s 中の USB・I2C、NFR-03 の 10 µs） | ロジアナ（LO→EN の時間） |
| D8 位置センサ | 23〜25、27、28 | AS5600、ギヤ（20:1・40:1） |
| D9 熱 | 16 の後半（3A 連続運転の温度） | 熱電対、ヒートシンク |

実機で確かめる必要がある値（コードからは確認できないもの）：

- I2C1 の SCL 周波数：タイミング 0x3071121B の計算値は約 425 kHz で、FR-POS-01 の 400 kHz 以下を超えるおそれがある。D8 で計測し、超えていれば `config.h` の値を変える。
- NFR-03（LO 検出から EN=Low まで 10 µs 以内）、FR-USB-38（割り込み禁止 1 µs 以下）、FR-COM-11（2 ms）。
- ECC の NMI 後の挙動（FR-NV-04、FR-USB-33）。試験 14 で確認する。

ハードウェアの申し送り H-12（PA7 の DIR 用プルダウン）は回路図に未反映。D8 の前に基板に追加しておく。

## 3. フェーズ E：P2 の実装（requirements.md 0.9）

対象は AGC（3.11 節）、J10 のエンコーダ／原点・リミット（3.12 節）、ROM ブートローダ（USB DFU）への移行（3.13 節）。フェーズ A〜C の後に行う（A・C で触る `usb_proto.c`・`settings.c`・`comm.c` と競合させないため）。

### 3.1 方針

- 既存の方針に合わせ、CubeMX の `MX_*_Init` は使わず、`App/` のコードで周辺を直接設定する（`position.c` の I2C1 と同じ）。`driver_fw.ioc` は端子の割り当て表として PA15/PA1（TIM2_CH1/CH2）だけ反映し、コード生成には使わない。
- ハードウェアに依存しない判定（原点復帰の状態遷移、リミットによる移動の可否、設定の版 2→3 の移行、ENTER_BOOTLOADER の受け付け条件）は、ホスト単体テストで確かめられる形に分ける。
- 新しい定数（エンコーダのフィルタ、デバウンス回数、HOME_SPEED の既定値、移行フラグのマジック値）は `config.h` に置く。

### 3.2 作業項目

| No | 内容 | 対象 | 要求 |
|---|---|---|---|
| E1 | **設定・レジスタの土台**：`settings_t` の `reserved[3]` を `j10_mode`（u8）と `home_speed`（u16）に置き換え、大きさ 40 バイトを保つ。`REC_VERSION` を 3 にし、版 1・2 のレコードは新項目を既定値（J10_MODE 0、HOME_SPEED 800）で補う。FLAGS のマスクに bit3・bit4 を加える。`settings_sanitize` に範囲（J10_MODE 0〜2、HOME_SPEED 1〜65535）を加える。`REG_MAP_SIZE` を 0x60 にし、`app_reg_read`／`app_reg_write` と書き込み可能表に 0x1E・0x59〜0x5F を加える | `App/Inc/settings.h`, `App/Src/settings.c`, `App/Inc/comm.h`, `App/Src/app.c`, `App/Inc/config.h` | FR-NV-01、FR-POS-08、3.8 節 |
| E2 | **AGC**：`drv.c` に AGC0/AGC1 を同じレベルで出す関数を追加。`driver_wake` でスタンバイ解除の前に端子を設定する。FLAGS 書き込みで bit3 が変わるとき、ENABLED なら全体を拒否（CMD_RESULT=1）、それ以外は即時反映。ENABLED 中の LOAD・FACTORY で bit3 が変わった場合は「反映待ち」として持ち、`cmd_disable`・`enter_fault` などで EN=Low にした時点で端子へ出す | `App/Src/drv.c`, `App/Inc/drv.h`, `App/Src/app.c` | FR-AGC-01〜06 |
| E3 | **J10 の切り替えとエンコーダ**：新しいモジュール `j10.c` を作る。モード 0（PA15/PA1 をアナログ）、1（TIM2 を AF1・内部プルアップ・エンコーダモード TI12・入力フィルタ）、2（GPIO 入力・内部プルアップ）を切り替える関数。切り替え時は一度アナログに戻す。起動時は設定の読み込み後に設定する。ENC_COUNT は TIM2->CNT を i32 で返す（モード 1 以外は 0） | `App/Src/j10.c`（新規）, `App/Inc/j10.h`（新規）, `App/Src/app.c`, `App/Inc/board.h`, `driver_fw.ioc` | FR-J10-01〜04、FR-ENC-01〜05 |
| E4 | **原点・リミット**：(a) 1 ms 周期で HOME1/HOME2 を読み、2 回一致で確定し、FLAGS bit4 で論理を合わせる。(b) パルス出力中に進行方向側が作動したら `stepper_estop` で即停止し、INPUT_STATUS bit5 を立てる。進行方向を返す関数を `stepper.c` に追加する。(c) MOVE_ABS・MOVE_REL・RUN_SPEED・ジョグの受け付けで、作動中のリミット側への移動を CMD_RESULT=1 で拒否する。(d) 原点復帰の状態機械（探索 → 停止 → 戻り → 非作動で停止 → POSITION=0）を、入力（確定レベル・停止したか・移動量）から次の動作を返す純粋な関数として `home.c` に分け、`app.c` から 1 ms 周期で呼ぶ。中止条件（STOP・ESTOP・DISABLE・通信タイムアウト・FAULT・反対側の作動・戻りが 1 回転を超える）を含める。原点復帰の完了は SET_POSITION と同じく自動 SYNC の対象外にする | `App/Src/j10.c`, `App/Src/home.c`（新規）, `App/Inc/home.h`（新規）, `App/Src/stepper.c`, `App/Inc/stepper.h`, `App/Src/app.c`, `App/Inc/app.h` | FR-HOME-01〜08 |
| E5 | **ブートローダ移行（FW）**：(a) `usb_proto.c` に ENTER_BOOTLOADER（0x50）を追加。未接続 0x35、PARAM 長の不一致 0x31、KEY 不一致 0x32、ENABLED 中 0x33。受け付けたら ACK を送って「移行要求」を立てる。(b) `app_main_loop` で、ACK の送信完了（最大 50 ms）を待ってから、ドライバを安全状態にし、移行フラグ（マジック値）を書いて `NVIC_SystemReset`。(c) リンカスクリプトに、リセットで初期化しない `.noinit` 領域（SRAM1）を追加する。(d) `Src/main.c` の `USER CODE BEGIN 1`（`HAL_Init` の前で、IWDG はまだ動いていない）でフラグを確認し、一致すればフラグを消して、SYSCFG のメモリ再配置をシステムメモリにし、0x1FFF0000 の MSP とリセットベクタで ROM へ飛ぶ | `App/Src/usb_proto.c`, `App/Inc/usb_proto.h`, `App/Src/app_main.c`, `App/Src/bootjump.c`（新規）, `App/STM32L433xx_app.ld`, `Src/main.c`（USER CODE 内のみ） | FR-BL-01〜07 |
| E6 | **nemadbg**：`dfu` サブコマンド（確認プロンプト、`--yes`）。DFU デバイスの検出は、`STM32_Programmer_CLI` が PATH にあれば `-l usb` を最大 5 秒繰り返して 0483:DF11 を探し、なければ検出を省いてその旨と書き込み手順を表示する（依存を pyserial・pyelftools のままにするため）。`registers.py` に 0x1E・0x59〜0x5F を追加し、`regs` の読み出し範囲を 0x5F までにする | `tools/nemadbg/cli.py`, `link.py`, `registers.py`, `tools/tests/`, `tools/README.md` | FR-USB-62、FR-USB-59 |
| E7 | **名前表と文書**：名前表に ENC_COUNT・J10_MODE・HOME1/HOME2 の確定レベル・INPUT_STATUS・原点復帰の内部状態・AGC の端子状態を登録し、`docs/usb_symbols.md` を更新する。`firmware/README.md` に DFU の手順（`nemadbg dfu` → STM32_Programmer_CLI、設定領域を消さない消去方法、確認が済むまで VM を切って行うこと）を書く | 各 `.c` の `SYM_ENTRY`, `docs/usb_symbols.md`, `firmware/README.md` | FR-USB-37、FR-ENC-05、FR-HOME-08、FR-BL-05・07 |
| E8 | **ホスト単体テスト**：(a) ENTER_BOOTLOADER の受け付け条件（試験 39）を `test_usb.c` に追加。(b) `home.c` の状態遷移（正常完了、開始時に作動中、STOP 中止、反対側の作動、戻りの上限超え）。(c) 設定レコードの版 2→3 の移行（フェーズ C2 の仕組みがあれば流用） | `tests/host/` | 試験 29・39 |

### 3.3 仕様の補足が要る点

実装の前に、次を requirements.md 0.10（フェーズ B5）に含める。

- FR-USB-62：DFU デバイスの検出に STM32_Programmer_CLI を使い、無ければ検出を省くこと（依存を増やさないため）。
- FR-HOME-03：「進行方向」は、パルス出力中の POSITION の増減方向で判定すること（FLAGS bit0 の反転とは無関係）。
- FR-BL-04：移行フラグは SRAM1 の `.noinit` 領域に置くこと（SRAM2 は、オプションバイト SRAM2_RST の設定によってはリセットで消えるため使わない）。

### 3.4 進め方と受け入れ条件

1. E1 → E2 を 1 つの作業単位として `implementer` に渡す。E3 → E4、E5 → E6、E7・E8 の順に続ける。E8 の (b)(c) は、それぞれ E4・E1 と同じ単位に含める。
2. 各単位の受け入れ条件：
   - `cmake --preset Debug` → `cmake --build --preset Debug` が警告なしで通る
   - `powershell -File firmware/tests/host/run_tests.ps1` がすべて成功する
   - `firmware/tools/` で `python -m unittest discover -s tests` がすべて成功する（E6 以降）
   - Flash の使用量がリンカの上限（252 KB）に収まっている
3. コミットは作業単位ごとに分ける（設定と AGC、J10、ブートローダ、ツール、文書）。
4. 実機試験は 2.4 節に段階 D10（試験 31〜39）として加え、D8 の後に行う。D10 の中ではブートローダ（37・38）を最後にし、試験 38 で H-14（DFU 中の EN の電位）を確かめるまでは DFU を VM なしで行う。

### 3.5 リスク

| リスク | 対策 |
|---|---|
| ROM ブートローダの端子設定で DRV_EN が High 寄りになる（H-14） | 試験 38 で測定するまで、DFU は VM を切って行う手順にする。必要なら R28 を小さくする |
| ROM の USB DFU が HSE なしで動かない（U-17） | 現在の BOOT0 押下での DFU 書き込みが動いていれば同じ条件。E5 の前に、ブートローダ版（0x1FFF6FF2）を確認する |
| J10 の配線のノイズで誤カウント・誤停止 | 入力フィルタとデバウンスの定数を `config.h` にまとめ、実機で調整する。H-13 の保護部品を検討する |
| 設定レコードの版上げで、旧版の保存値を読み落とす | E8 (c) のホストテストで、版 1・2・3 のどれからでも読めることを確かめる |

## 4. 進め方

CLAUDE.md の作業フローに従う。

1. （済）2.1 の A2・A3・A4 はユーザーが決定した。
2. フェーズ A・B を 1 つの作業単位として `implementer` に渡す。受け入れ条件は次のとおり。
   - `cmake --preset Debug` → `cmake --build --preset Debug` が警告なしで通る
   - `powershell -File firmware/tests/host/run_tests.ps1` がすべて成功する（A1 の追加テストを含む）
   - `firmware/tools/` で `python -m unittest discover -s tests` がすべて成功する（A4 の追加テストを含む）
3. 差分をレビューしてコミットする。コミットは A（FW）、A4（ツール）、B（文書）に分ける。
4. フェーズ C を 1 モジュールずつ `implementer` に渡す（C4 → C3 → C2 → C1 の順）。
5. フェーズ D は実機で行い、結果を `docs/test_results.md` に記録する。不具合があれば修正項目として追加する。
6. フェーズ E（P2）は A〜C の後に、3.4 節の手順で進める。
