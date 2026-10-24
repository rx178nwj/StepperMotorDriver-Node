# driver_fw — NEMA23 ステッピングモータドライバ ファームウェア

STM32L433CCU6 + TB67S249FTG。開発環境は STM32CubeMX ＋ VS Code（ST 公式拡張「STM32CubeIDE for Visual Studio Code」）、ビルドは CMake ＋ Ninja。
要求仕様は [docs/requirements.md](docs/requirements.md)。

## フォルダ構成

| パス | 内容 | CubeMX 再生成時 |
|---|---|---|
| `driver_fw.ioc` | ピン・クロック・USB の設定（回路図と照合する資料も兼ねる） | — |
| `Src/`, `Inc/` | CubeMX 生成コード（main、クロック、USB CDC、割り込み、MSP） | **USER CODE 部分以外は上書きされる** |
| `Drivers/`, `Middlewares/`, `cmake/stm32cubemx/` | HAL・CMSIS・USB ライブラリ | 上書きされる |
| `App/` | アプリコード（ドライバ制御、パルス生成、監視、通信（CAN）、設定保存） | 影響なし |
| `App/STM32L433xx_app.ld` | Flash 末尾 4KB を設定保存用に除外したリンカスクリプト | 影響なし |
| `CMakeLists.txt` | アプリのソース追加、リンカスクリプトの差し替え | 生成は初回のみ |
| `.vscode/tasks.json` | USB DFU / ST-LINK 書き込みタスク | 影響なし |

### CubeMX 生成コードとのつなぎ（USER CODE に記述済み）

- `Src/main.c`：USER CODE 2 で `app_main_init()`、ループ内 USER CODE 3 で `app_main_loop()`
- `Src/stm32l4xx_it.c`：NMI で Flash ECC を処理（`app_nmi_handled()`）、HardFault でドライバ出力停止（Release はリセット、Debug は停止）
- TIM1・EXTI2/3・CAN1（RX0/SCE。送信割り込みは使わない）の割り込みハンドラは `App/Src/app_irq.c`（.ioc では NVIC を無効にしてあるので CubeMX は生成しない）
- TIM1・ADC・DAC・CAN・GPIO・IWDG の `MX_xxx_Init()` は呼ばない設定（.ioc の Advanced Settings）。初期化は App 側で行う
- USB 割り込み優先度は 3（パルス生成 0、LO 異常 1、CAN 2 より低い）
- USB CDC：受信は `Src/usbd_cdc_if.c`（USER CODE 6）の `CDC_Receive_FS` から `usb_proto_rx_isr()` でリングバッファへ積むだけ。解析・応答は `app_main_loop()` から呼ぶ `usb_proto_poll()`。バスリセット／サスペンド／クラス初期化・解除（`CDC_Init_FS`・`CDC_DeInit_FS`、`HAL_PCD_SuspendCallback`）でセッションを切断
- 製品文字列は `Src/usbd_desc.c` の USER CODE（PRIVATE_DEFINES）で `NEMA23 Stepper Driver` に上書き。VID/PID は生成値の 0x0483/0x5740、シリアル番号は生成コードの `Get_SerialNum` が UID から作る

> 2026-10-17：要求仕様 0.3 で上位通信を I2C2（PCA9615）から CAN1（PB8/PB9、TJA1441AT）へ変更した。.ioc と App（`App/Src/comm.c`）は CAN1 に対応済み（CAN 2.0A、既定 500 kbit/s で `App/Inc/config.h` の `CAN_BITRATE_BPS` で変更、ID 割り当てとレジスタ仕様は要求仕様 3.8 節）。AGC0/AGC1 は PB10/PB11 へ移動した。

## USB 通信（デバッグ・内部設定）

仕様は要求仕様 3.9 節。USB CDC（仮想 COM ポート）上で、CCP 風の要求・応答プロトコルを使う。実装は `App/Src/usb_proto.c`（フレーム層とコマンド）、`App/Src/symtab.c`（名前表）。

- フレーム：`[0xA5][LEN][BODY][CRC16 下位][CRC16 上位]`（LEN 2〜250、CRC-16/CCITT-FALSE を LEN+BODY にかける）。要求 BODY `[CMD][CTR][PARAM…]`、応答 BODY `[0xFF][ERR][CTR][DATA…]`、イベント BODY `[0xFE][EVT][DATA…]`
- 最初に CONNECT（CMD 0x01）。例：`A5 02 01 00 CD 91`（CTR=0）。未接続では CONNECT と GET_VERSION 以外は ERR 0x35
- CAN_REQUEST（0x40）：CAN の要求フレームを `comm_process_request()` に渡し、CAN バスには送らずに応答フレームを USB で返す。LED_COMM は点灯しない
- メモリ読み出し：SET_MTA＋UPLOAD、SHORT_UP（アドレス指定。Flash・SRAM・OTP・UID 領域のみ）、SYM_UPLOAD（名前指定）
- build-id：リンクオプション `-Wl,--build-id=sha1` と、`App/STM32L433xx_app.ld` の `.note.gnu.build-id`（Flash に配置、`KEEP`）。EXCHANGE_ID（0x17）が SHA1 20 バイトを返すので、PC は ELF の build-id（`arm-none-eabi-readelf -n driver_fw.elf`）と一致を確認してからアドレス指定読み出しに使う
- 名前表：`App/Inc/symtab.h` の `SYM_ENTRY(変数, SYM_U32, 要素数);` 1 行で登録する。エントリは `.sym_table` セクションに置かれ、リンカスクリプトが連結する。static 変数も、その変数を定義した .c の末尾で登録できる。INDEX はリンク順で決まり版ごとに変わるので、PC は SYM_FIND で名前から引く。登録一覧は [docs/usb_symbols.md](docs/usb_symbols.md)
- 統計（`usb_stats`）：受信フレーム数・CRC エラー数・破棄数など。名前表から読める
- 送信は 50 ms 続けて送れなかった応答・イベントを破棄する（ホストが COM ポートを開いていないときもメインループを止めない）。送信が 50 ms 以上詰まって応答を送れないときに受けた要求は実行せず捨てる（`tx_dropped` に計上。PC はタイムアウトで再送する）。未送信のイベントを新しいイベントで上書きしたときも `tx_dropped` に 1 計上する
- 書き込み（DNLOAD 相当）、USB↔CAN バスのブリッジ、VBUS 検出は実装していない
- ホスト単体テスト：`tests/host/` で `usb_proto.c`・`symtab.c` を実物のままホスト PC の gcc でビルドして試験する（FW 本体の ARM ビルドとは別）。実行は `powershell -File tests/host/run_tests.ps1`（Windows）または `sh tests/host/run_tests.sh`。gcc が PATH に必要（`CC` 環境変数で変更可）。`usb_proto.c` のメモリ読み出しだけ `USB_MEM_RD8` / `USB_MEM_RD32` マクロでテスト時に差し替える

## 使い方

1. VS Code でこのフォルダ（`driver_fw`）を開く。ST 拡張が CMake プロジェクトとして認識し、必要なツール（GCC 等）を取得する。
2. ビルド：CMake の構成で `Debug` または `Release` プリセットを選んでビルド（出力は `build/<preset>/driver_fw.elf`）。
3. 書き込み：[書き込み方法](#書き込み方法) の 3 通りのいずれか
4. 初回だけ：タスク「Read bootloader version ID (ST-LINK)」でブートローダ版 ID を確認（0x10 なら USB DFU は問題なし。0xFF は AN2606 記載の CRS 不具合あり）。

## 書き込み方法

1. ST-LINK（SWD）：拡張のデバッグ起動、またはタスク「Flash (ST-LINK) - Debug」
2. `nemadbg dfu`（ボタン操作なし）：USB 経由の ENTER_BOOTLOADER で ROM ブートローダ（USB DFU）へ移り、USB で書き込む（要求仕様 FR-BL-01）
3. 従来の手順：S3（BOOT0）を押したまま S1（リセット）で DFU に入り、タスク「Flash (USB DFU) - …」で書き込む

### `nemadbg dfu` による書き込み

1. 基板を DISABLE（励磁 OFF）にする（ENABLED 中の ENTER_BOOTLOADER は ERR 0x33 で拒否される）。
2. `firmware/tools` で `python -m nemadbg dfu` を実行する（確認を省くときは `--yes`）。COM ポートが消え、DFU デバイス（0483:DF11）が現れる。`STM32_Programmer_CLI` が PATH にあれば nemadbg が列挙を待って結果を表示する（PATH に無ければこの検出は省かれる）。詳細は [tools/README.md](tools/README.md)。
3. DFU デバイスが現れたら書き込み・起動する（`firmware` で実行）。

   ```
   STM32_Programmer_CLI -c port=usb1 -w build/Release/driver_fw.elf -v -g 0x08000000
   ```

   `STM32_Programmer_CLI` は `%LOCALAPPDATA%\stm32cube\bundles\programmer\<版>\bin\` にある（版は「固定しているバージョン」の表と `.vscode/tasks.json` のパスに合わせる）。

### 設定領域を消さないこと

設定保存領域（0x0803F000〜0x0803FFFF、FR-NV-02）を消さないため、全消去（`-e all`）やマスイレースはしない。`-w` は書き込む範囲のセクタだけを消去するので、設定は残る（`.vscode/tasks.json` の注記と同じ）。

### 安全上の注意

ROM ブートローダ実行中の DRV_EN などの端子レベルは、実機で確認するまで確定していない（FR-BL-07、H-14）。確認するまでは、DFU での書き込みは VM（モータ電源）を切って行う。

### 実機で確認すべき点（試験 37・38、U-17）

- `nemadbg dfu` の後に DFU デバイス（0483:DF11）が現れること
- 書き込み・起動の後に通常起動に戻り、保存してあった設定が残っていること
- DFU 中に IWDG でリセットされないこと。オプションバイトの IWDG_SW が 1（ソフトウェア起動）であること。0 だとリセット直後から IWDG が動き、DFU 中にリセットされる
- ROM ブートローダの版（0x1FFF6FF2。タスク「Read bootloader version ID (ST-LINK)」）
- ROM ブートローダ中の DRV_EN などの端子レベル（上の安全上の注意）

## 固定しているバージョン（2026-10-11 時点）

| ツール | バージョン |
|---|---|
| STM32CubeMX | 6.18.1 |
| STM32Cube FW_L4 | 1.18.2 |
| VS Code 拡張 STM32CubeIDE for VS Code | 3.11.0 |
| GCC（gnu-tools-for-stm32） | 14.3.1 を想定（初回構成時に拡張が取得したものを記録する） |
| STM32CubeProgrammer | 2.23.0 |

更新するときはまとめて行い、この表と `.vscode/tasks.json` のパスを直す。

## CubeMX で設定を変えるとき

1. `driver_fw.ioc` を CubeMX で開いて変更し、GENERATE CODE。
2. `Src/main.c`・`Src/stm32l4xx_it.c` の USER CODE 部分が残っていることを確認。
3. 新しい周辺回路を App 側で初期化する場合は、Advanced Settings でその `MX_xxx_Init` を「Do Not Generate Function Call」にする。
