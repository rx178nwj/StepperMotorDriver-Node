# STM32CubeMX 設定手順（.ioc 作成）

対象：STM32L433CCUx（UFQFPN48）。ピン割り当ては [requirements.md](requirements.md) 2章（回路図 2026-10-11 版のネットリスト）に基づく。

## 方針

- CubeMX が生成するのは **プロジェクトの骨組み**（HAL、CMSIS、startup、リンカスクリプト、CMake）、**クロック設定**、**USB CDC** の 3 つ。
- ドライバ制御・ADC・DAC・TIM1・I2C2・IWDG の初期化は、既存のアプリコード（`src/`）が行う。CubeMX ではピンと周辺回路を「割り当てるだけ」にして、初期化関数の呼び出しは生成しない（手順 6）。
- ピン設定は、回路図と照合する資料としても使う。

---

## 1. 新規プロジェクト

1. **File → New Project → MCU/MPU Selector** で `STM32L433CCUx` を選ぶ（パッケージ UFQFPN48）。
2. 「Start Project」。

## 2. System Core

| 項目 | 設定 |
|---|---|
| **RCC** → High Speed Clock (HSE) | **Disable**（PH0/PH1 をアドレス入力に使うため） |
| **RCC** → Low Speed Clock (LSE) | **Disable**（PC14 を入力、PC15 を GPIO 出力 CAN_S に使うため） |
| **SYS** → Debug | **Trace Asynchronous Sw**（SWDIO=PA13、SWCLK=PA14、SWO=PB3） |
| **SYS** → Timebase Source | SysTick |
| **IWDG** | Activated にチェック（LSI を有効にするため。パラメータは既定のまま） |

## 3. ピン割り当て（Pinout view でピンをクリックして設定）

User Label 欄にネット名を入れる。

### 出力（GPIO_Output）

| ピン | User Label | 備考 |
|---|---|---|
| PA9 | DRV_DIR | |
| PA10 | DRV_EN | |
| PB15 | DRV_RESET | |
| PB12 | DRV_DMODE0 | |
| PB13 | DRV_DMODE1 | |
| PB14 | DRV_DMODE2 | |
| PB8 | DRV_AGC0 | |
| PB9 | DRV_AGC1 | |
| PB0 | LED_MCU | |
| PB1 | LED_COMM | |
| PA5 | LED_RUN | |
| PB2 | LED_FAULT | |
| PC15 | CAN_S | TJA1441AT の S。Low＝ノーマル、High＝サイレント。RTC ドメインのピンで駆動能力は小さいが静的入力なので問題なし |

GPIO 設定（System Core → GPIO）：上の出力はすべて **Output level = Low、Push Pull、No pull、Speed = Low**。

### 入力（GPIO_Input、No pull）

| ピン | User Label |
|---|---|
| PC14 | PWR_ST |
| PH0 | ADDR0 |
| PH1 | ADDR1 |
| PC13 | ADDR2 |
| PB4 | SWITCH1 |
| PB5 | SWITCH2 |

### 割り込み入力

| ピン | 機能 | User Label | 設定 |
|---|---|---|---|
| PA2 | GPIO_EXTI2 | DRV_LO1 | Falling edge、No pull |
| PA3 | GPIO_EXTI3 | DRV_LO2 | Falling edge、No pull |

### 周辺機能

| ピン | 機能 | User Label |
|---|---|---|
| PA8 | TIM1_CH1 | DRV_CLK |
| PA4 | DAC1_OUT1 | DAC_VREF |
| PA0 | ADC1_IN5 | VM_SENSE |
| PA6 | ADC1_IN11 | TEMP |
| PA7 | ADC1_IN12 | AD_IN |
| PB10 | I2C2_SCL | I2C_PC_SCL |
| PB11 | I2C2_SDA | I2C_PC_SDA |
| PB6 | I2C1_SCL | I2C_SENS_SCL |
| PB7 | I2C1_SDA | I2C_SENS_SDA |
| PA11 | USB_DM | |
| PA12 | USB_DP | |

PA15・PA1（J10：エンコーダ／原点）は P2 で決めるため、未割り当てのままにする。

## 4. 周辺回路のモード

| 周辺 | 設定 |
|---|---|
| **TIM1** | Channel1 = PWM Generation CH1 |
| **DAC1** | OUT1 mode = Connected to external pin only |
| **ADC1** | IN5 / IN11 / IN12 = Single-ended、**Vrefint Channel** と **Temperature Sensor Channel** にチェック |
| **I2C2** | I2C |
| **I2C1** | I2C |
| **USB** | Device (FS) にチェック |
| **Middleware → USB_DEVICE** | Class For FS IP = **Communication Device Class (Virtual Port Com)** |
| **CRS**（RCC の中、または System Core → CRS） | Synchronization Source = **USB** |

## 5. Clock Configuration

| 項目 | 値 |
|---|---|
| PLL Source Mux | HSI（16 MHz） |
| PLLM | /1 |
| PLLN | ×10 |
| PLLR | /2 |
| System Clock Mux | PLLCLK → **SYSCLK = 80 MHz** |
| AHB / APB1 / APB2 Prescaler | /1、/1、/1（HCLK・PCLK1・PCLK2 = 80 MHz） |
| HSI48 | 有効 |
| CLK48 Mux（USB） | **HSI48**（48 MHz） |

画面上にエラー（赤表示）がないことを確認する。

## 6. Project Manager

### Project タブ

| 項目 | 設定 |
|---|---|
| Project Name | `driver_fw` |
| Project Location | `H:\OneDrive\CraftWork\SteppingMotorDriver_NEMA23` |
| Toolchain / IDE | **CMake** |
| Minimum Heap Size | 0x200 |
| Minimum Stack Size | 0x800 |

### Code Generator タブ

| 項目 | 設定 |
|---|---|
| STM32Cube MCU packages and embedded software packs | Copy only the necessary library files |
| Generate peripheral initialization as a pair of '.c/.h' files per peripheral | チェック |
| Keep User Code when re-generating | チェック |
| Delete previously generated files when not re-generated | チェック |

### Advanced Settings タブ

右側の「Generated Function Calls」で、次の関数の **Do Not Generate Function Call** にチェックを入れる。初期化は既存のアプリコードが行う。

- `MX_GPIO_Init`
- `MX_TIM1_Init`
- `MX_DAC1_Init`
- `MX_ADC1_Init`
- `MX_I2C1_Init`
- `MX_I2C2_Init`
- `MX_IWDG_Init`

`SystemClock_Config`・`MX_USB_DEVICE_Init`・`MX_CRS_Init`（表示される場合）は **チェックしない**（CubeMX の生成コードを使う）。

## 7. 生成

1. **GENERATE CODE**。初回は STM32CubeL4 パッケージのダウンロードを求められるので許可する。
2. `driver_fw/` フォルダに `.ioc`、`CMakeLists.txt`、`Core/`、`Drivers/`、`USB_DEVICE/`、`Middlewares/` などができていれば完了。

生成が終わったら知らせてください。既存コードの組み込み、設定保存領域を確保するリンカ設定、デバッグ・DFU 書き込み設定はこちらで行います。
