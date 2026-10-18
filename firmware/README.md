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
- TIM1・EXTI2/3・CAN1（RX0/TX/SCE）の割り込みハンドラは `App/Src/app_irq.c`（.ioc では NVIC を無効にしてあるので CubeMX は生成しない）
- TIM1・ADC・DAC・CAN・GPIO・IWDG の `MX_xxx_Init()` は呼ばない設定（.ioc の Advanced Settings）。初期化は App 側で行う
- USB 割り込み優先度は 3（パルス生成 0、LO 異常 1、CAN 2 より低い）

> 2026-10-17：要求仕様 0.3 で上位通信を I2C2（PCA9615）から CAN1（PB8/PB9、TJA1441AT）へ変更した。.ioc と App の CAN 対応は未実施（現行コードは I2C2 のまま）。

## 使い方

1. VS Code でこのフォルダ（`driver_fw`）を開く。ST 拡張が CMake プロジェクトとして認識し、必要なツール（GCC 等）を取得する。
2. ビルド：CMake の構成で `Debug` または `Release` プリセットを選んでビルド（出力は `build/<preset>/driver_fw.elf`）。
3. 書き込み：
   - ST-LINK：拡張のデバッグ起動、またはタスク「Flash (ST-LINK) - Debug」
   - USB：S3（BOOT0）を押したまま S1（リセット）→ タスク「Flash (USB DFU) - …」
4. 初回だけ：タスク「Read bootloader version ID (ST-LINK)」でブートローダ版 ID を確認（0x10 なら USB DFU は問題なし。0xFF は AN2606 記載の CRS 不具合あり）。

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
