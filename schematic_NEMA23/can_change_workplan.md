# 作業計画：上位通信の CAN 化（PCA9615 → TJA1441AT）

作成：2026-10-17。仕様は `firmware/docs/requirements.md` 0.3 と `schematic/stm32l433_tb67s249_connection.md` の 2026-10-17 追記（4.1 節・13 節）。
参照番号・座標は 2026-10-17 時点の `schematic.kicad_sch`／`schematic.kicad_pcb`（kicad-cli で出力したネットリストで確認）。

---

## 0. 着手前

| No | 作業 | 完了条件 |
|---|---|---|
| 0-1 | KiCad を閉じる。`schematic/~schematic.kicad_sch.lck`・`~schematic.kicad_pro.lck` が残っている（2026-10-17 10:20 頃）。開いたままのエディタから保存すると外部での変更が上書きされる | ロックファイルが消えている |
| 0-2 | `schematic/` の kicad_sch / kicad_pcb / kicad_pro をバックアップする（`schematic.old/` の運用に合わせる） | バックアップがある |
| 0-3 | 変更前の基準を記録する：ERC 6 件（エラー 2：power_pin_not_driven 1・pin_to_pin 1、警告 4）、DRC 違反 114 件・未配線 0、ネットリスト 131 部品・91 ネット | `review/can-before/` に erc/drc/netlist を保存 |
| 0-4 | 部品を決める。2026-10-17 決定：4 極 DIP = OTAX KHS42E、TVS = PANJIT PEC3824C2A-AU。手持ち部品 DB には TJA1441AT・KHS42E・PEC3824C2A-AU・B3B-PH-K-S のどれもない（すべて新規購入） | 残り（120Ω 1206、PH 3 ピン＋ハウジング PHR-3）の入手先が決まっている |
| 0-5 | PEC3824C2A-AU のピン図（データシート 1 ページ目、画像）で 1/2/3 番ピンの役割を目視確認する | 1=ライン 1、2=ライン 2、3=GND であることを確認 |
| 0-6 | KHS42E の公式データシート（外形図・定格）を入手する | フットプリント寸法と接点定格が確認できている |

## 1. 回路図：削除

| 参照 | 内容 | 接続 |
|---|---|---|
| U1 | PCA9615DP（TSSOP-10） | VDD(A)=+3V3、VDD(B)=/+5V、SDA/SCL、EN、差動 4 本、VSS=GND |
| R2 / R3 | 4.7kΩ | +3V3 ↔ /I2C_PC_SCL / /I2C_PC_SDA |
| R5 | 10kΩ | +3V3 ↔ /PCA9615_EN |
| R7 / R15 | 600Ω/DNP | /+5V ↔ /DI2C_SDA_P / /DI2C_SCL_P |
| R11 / R19 | 600Ω/DNP | /DI2C_SDA_M / /DI2C_SCL_M ↔ GND |
| R10 / R18 | 120Ω/DNP | 差動終端（SDA / SCL） |

- ネット `/I2C_PC_SCL`、`/I2C_PC_SDA`、`/PCA9615_EN`、`/DI2C_SDA_P`、`/DI2C_SDA_M`、`/DI2C_SCL_P`、`/DI2C_SCL_M` のラベルとワイヤーも消す。
- +3V3 / +5V の共通のコンデンサ（C5、C10 など）は残す（PCA9615 専用のデカップリングはない）。
- 削除で空く参照番号（U1、R2、R3、R5、R7、R10、R11、R15、R18、R19）は詰め直さない（再アノテーションはしない）。

## 2. 回路図：ネットの付け替え（MCU = IC2、TB67S249 = IC1）

| IC2 ピン | 変更前 | 変更後 |
|---|---|---|
| 21 PB10 | /I2C_PC_SCL | **/DRV_AGC0**（→ IC1 pin1） |
| 22 PB11 | /I2C_PC_SDA | **/DRV_AGC1**（→ IC1 pin2） |
| 45 PB8 | /DRV_AGC0 | **/CAN_RX**（→ IC3 pin4） |
| 46 PB9 | /DRV_AGC1 | **/CAN_TX**（→ IC3 pin1） |

- IC1 側の /DRV_AGC0・/DRV_AGC1 のラベルはそのまま。IC2 側のラベルだけ付け替える。
- pin44 の旧ネット名 `/PB8_BOOT`（BOOT0）は PB8 とは無関係なので変更しない。

## 3. 回路図：追加

| 参照 | 値・型番 | シンボル | フットプリント | 接続 |
|---|---|---|---|---|
| IC3 | TJA1441AT | プロジェクトライブラリに新規作成（3-1） | `Package_SO:SOIC-8_3.9x4.9mm_P1.27mm` | 1 TXD=/CAN_TX、2 GND、3 VCC=/+5V、4 RXD=/CAN_RX、5 VIO=+3V3、6 CANL=/CANL、7 CANH=/CANH、8 S=GND |
| C41 | 100nF/16V X7R | Device:C | `Capacitor_SMD:C_0402_1005Metric` | /+5V – GND（IC3 pin3 直近） |
| C42 | 100nF/16V X7R | Device:C | `Capacitor_SMD:C_0402_1005Metric` | +3V3 – GND（IC3 pin5 直近） |
| R49 | 120Ω 1% | Device:R | `Resistor_SMD:R_1206_3216Metric` | /CANH – /CAN_TERM |
| D7 | PEC3824C2A-AU（PANJIT） | 3-2 | `Package_TO_SOT_SMD:SOT-23` | 1=/CANH、2=/CANL、3=GND（0-5 で確認後） |

### 3-1. TJA1441AT のシンボル

- **2026-10-17 ユーザーが追加し、回路図に配置済み。**
- 確認すること：ピン（1 TXD、2 GND、3 VCC、4 RXD、5 VIO、6 CANL、7 CANH、8 S）、フットプリント（SOIC-8）、参照番号 IC3、5〜8 の接続。

### 3-2. TVS のシンボル

- 標準の `Power_Protection` ライブラリに PESD2CAN 系（1・2 = ライン、3 = 共通）があれば、それをコピーして名前を PEC3824C2A-AU に変える。
- なければ、3 ピンのシンボルを作る（中身は双方向 TVS 2 個の共通端子が pin3）。

## 4. 回路図：変更

| 参照 | 変更前 | 変更後 |
|---|---|---|
| J1 / J2 | Conn_01x05、`JST_PH_B5B-PH-K_1x05_P2.00mm_Vertical`（B.Cu）、1=DI2C_SDA_P、2=DI2C_SDA_M、3=DI2C_SCL_P、4=DI2C_SCL_M、5=GND | **Conn_01x03**、`Connector_JST:JST_PH_B3B-PH-K_1x03_P2.00mm_Vertical`、**1=/CANH、2=/CANL、3=GND**（J1 と J2 は並列） |
| SW1 | SW_DIP_x03、`Library:DMR03V`、値 KSD32。1〜3=ADDR0〜2、4〜6=+3V3 | **削除**（SW2 に置き換え） |
| SW2（新規、2026-10-17 ユーザーが配置済み） | — | **SW_DIP_x04、値 KHS42E（OTAX）**、面実装 1.27mm ピッチのフットプリント（0-6 の外形図から作成）。**1=ADDR0、2=ADDR1、3=ADDR2、8/7/6=+3V3、4=/CAN_TERM、5=/CANL** |
| R32 | 9.1kΩ 1%（/DRV_VREF–GND）、実装扱い | **DNP**（2026-10-17 決定。実機は未実装）。KiCad の「実装しない（DNP）」属性を付け、BOM から外れることを確認する |

- SW2 の値・MPN・Manufacturer のプロパティを KHS42E / OTAX にする（旧 SW1 は値が KSD32 なのにフットプリントが DMR03V で、食い違っていた）。
- **+3V3 側のピン番号が変わる**（3 極では 4〜6、4 極では 8/7/6）。シンボルを差し替えたら、ワイヤーの付け直しを忘れないこと。
- R23/R24/R26（ADDR プルダウン）は変更しない。
- 既存の DNP 部品（R7/R10/R11/R15/R18/R19、R13/R17/R22 など）は、値の文字列「DNP」で表しているだけで、属性は付いていない。R32 は属性で付ける。既存品も属性にそろえるかは別途決める。

## 5. 回路図：確認

| No | 確認 | 完了条件 |
|---|---|---|
| 5-1 | ERC | 変更前の 6 件から増えない（新しい違反 0）。IC3 の電源ピンは /+5V と +3V3 に駆動元があるので、新しい power_pin_not_driven は出ないはず |
| 5-2 | ネットリストの照合 | `connection.md` 11 章のチェックリストの CAN 関連 7 項目をすべて満たす。削除したネットが残っていない |
| 5-3 | 回路図の PDF | `review/can-after/` に ERC・ネットリスト・PDF を保存し、`verification.md` に結果を書く |

## 6. PCB

### 6-1. 削除と配置

- 6-1-1：U1・R2・R3・R5・R7・R10・R11・R15・R18・R19 と、削除したネットのトラックとビアを消す。
  - 削除するのは U1 (111.3, 94.2) と、抵抗群 (104〜107, 91〜96) の周辺。
- 6-1-2：J1/J2 のフットプリントを PH 3 ピンに差し替える。今と同じく B.Cu 面で、J1 (95.4, 97.6)、J2 (101, 97.6)。
- 6-1-3：IC3・C41・C42・R49・D7 は、空いた U1／抵抗群の場所（J1/J2 の近く）に置く。並べる順は、コネクタ → D7 → R49 → IC3 とする。
- 6-1-4：SW1（DMR03V）を削除し、SW2 KHS42E（面実装、1.27mm ピッチ）を置く。
  - 置き場所は今の位置 (108.4, 86) の周辺。
  - 今の DMR03V（スルーホール）より小さくなる見込み。スルーホールの穴がなくなるので、裏面の配線にも使える。
  - シルクの番号表示（1〜3 = ID、4 = TERM）を追加する。

### 6-2. 配線

- 6-2-1：CANH/CANL を差動ペアで引く。
  - J1–J2 間はまっすぐつなぎ、IC3・R49・D7 への分岐は最短にする。
  - 方針は `pcb_routing_sizes.md` の「CANバスの差動配線」。
- 6-2-2：/DRV_AGC0・/DRV_AGC1 を引き直す。
  - 経路は IC2 pin21/22 → IC1 pin1/2。
  - PB8/PB9（pin45/46）からの旧トラック（AGC0 は 14 セグメント、AGC1 は 15 セグメント）は消す。
- 6-2-3：/CAN_TX・/CAN_RX を引く。経路は IC2 pin45/46 → IC3 pin1/4。

### 6-3. 設定と確認

- 6-3-1：ネットクラスを変える。
  - PCA9615_DIFF を CAN_DIFF（/CANH、/CANL）にする。
  - DIGITAL に /CAN_TX、/CAN_RX、/CAN_TERM を加え、/I2C_PC_SCL・/I2C_PC_SDA・/PCA9615_EN を外す。
  - 変えたら `pcb_routing_sizes.md` の手順で読み戻して確認する。
- 6-3-2：DRC を通す。完了条件は、未配線 0 で、変更前の 114 件から新しい違反が増えないこと。

## 7. BOM

- 7-1：削除する。
  - PCA9615DP
  - 4.7kΩ ×2（R2/R3）
  - 10kΩ ×1（R5）
  - 600Ω・120Ω の DNP 枠 6 個
  - JST PH 5 ピン ×2
  - 3 極 DIP
- 7-2：追加する。
  - TJA1441AT ×1
  - 100nF 0402 ×2
  - 120Ω 1206 ×1
  - TVS PEC3824C2A-AU ×1
  - 4 極 DIP KHS42E ×1
  - JST PH 3 ピン（B3B-PH-K-S）×2
- 7-3：`schematic/out/raw_bom.csv`、`mouser_bom_import.csv`、`BOM_mouser_search.csv` を出し直す。
- 7-4：R32（9.1kΩ）は DNP にする（2026-10-17 決定、4 章）。BOM を出し直したら、実装部品から外れていることを確認する。

## 8. ファームウェア（回路図確定後）

| No | ファイル | 作業 |
|---|---|---|
| 8-1 | `driver_fw.ioc` | I2C2 を無効化する。CAN1 を有効にする（PB8/PB9、AF9、NVIC 無効、MX_CAN1_Init は呼ばない）。PB10/PB11 を GPIO 出力にする。PCLK1 から 500 kbit/s のビットタイミングを計算する |
| 8-2 | `App/Inc/board.h` | AGC0/1 のピン定義を PB10/PB11 に、CAN のピン定義を追加、I2C2 の定義を削除する |
| 8-3 | `App/Inc/config.h` | I2C の BASE 0x30 を消し、CAN の ID ベース（0x000/0x080/0x100/0x180）とビットレートを定義する |
| 8-4 | `App/Src/comm.c`・`comm.h` | レジスタの読み書き処理（REG_MAP、スナップショット、32bit の全バイト書き込み判定）を転送層から分けて残す。I2C の転送層を CAN（FR-COM-01〜13）に置き換える |
| 8-5 | `App/Src/app_irq.c` | I2C2 の割り込みを CAN1 RX0/TX/SCE に替える（優先度 2） |
| 8-6 | `firmware/README.md` | 「CAN 対応は未実施」の注記を消す |
| 8-7 | 試験 | `requirements.md` 6 章の 9 / 9a / 9b と 10（通信タイムアウト）を行う |

## 9. 進め方

1. 0 → 1〜5（回路図）→ 6（PCB）→ 7（BOM）→ 8（ファームウェア）の順で進める。
2. 回路図（5）が終わったら一度止め、ネットリストを確認する。
3. PCB はそれから着手する。
4. 回路図の変更には `kicad-schematic-designer` スキルを使う。
