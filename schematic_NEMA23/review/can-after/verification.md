# 検証記録：上位通信の CAN 化（回路図）

実施：2026-10-17。手順は `schematic/can_change_workplan.md` 1〜5 章。編集は MCP（mcp-server-kicad 0.21.0）で行った。
同じフォルダに回路図 PDF（`schematic.pdf`）、ネットリスト（`schematic.xml`）、ERC レポートを保存した。

## 変更内容

| 区分 | 内容 |
|---|---|
| 削除 | U1 PCA9615DP、R2、R3、R5、R7、R10、R11、R15、R18、R19、SW1（3 極 DIP）、旧 J1/J2（Conn_01x05）。それらにだけつながっていた電源シンボル 9 個（#PWR06/07/010/019/024/025/030/034/039）、ラベル、ワイヤー 44 本 |
| 削除したネット | I2C_PC_SCL、I2C_PC_SDA、PCA9615_EN、DI2C_SDA_P/M、DI2C_SCL_P/M（ネットリストに残っていないことを確認） |
| 付け替え | IC2 pin21 PB10 → DRV_AGC0、pin22 PB11 → DRV_AGC1、pin45 PB8 → CAN_RX、pin46 PB9 → CAN_TX |
| 配線（ユーザー配置の部品） | IC3 TJA1441AT、D7 PEC3824C2A-AU、SW2 KHS42E |
| 追加 | C41、C42（100nF/16V、0402）、R49（120Ω 1%、1206）、J1/J2（Conn_01x03、JST PH B3B-PH-K-S） |
| 変更 | R32 の値を `9.1k 1%/DNP` にした（既存の DNP 表記にそろえた。DNP 属性は未設定。下の「残作業」） |

参照番号：TJA1441AT はユーザーが **IC3** として配置したため、計画の U10 ではなく IC3 とした。

## ネットリストでの確認（すべて計画どおり）

| ネット | 接続 |
|---|---|
| /CAN_TX | IC2.46（PB9）、IC3.1 |
| /CAN_RX | IC2.45（PB8）、IC3.4 |
| /CANH | IC3.7、R49.1、D7.1、J1.1、J2.1 |
| /CANL | IC3.6、SW2.5、D7.2、J1.2、J2.2 |
| /CAN_TERM | R49.2、SW2.4 |
| /DRV_AGC0 | IC1.1、IC2.21（PB10） |
| /DRV_AGC1 | IC1.2、IC2.22（PB11） |
| /ADDR0〜2 | IC2.5/6/2、R23/R24/R26、SW2.1/2/3 |

- IC3：2、8 = GND、3 = /+5V、5 = +3V3
- C41 = /+5V–GND、C42 = +3V3–GND
- D7.3 = GND、SW2.6/7/8 = +3V3、J1.3/J2.3 = GND

## ERC

- 変更前 26 件（うち 19 件は、ユーザーが配置した IC3/D7/SW2 のピンが未接続だという指摘）
- 変更後 **6 件**（エラー 2、警告 4）。すべて以前からある指摘で、新しい違反は 0 件：
  - #PWR01 power_pin_not_driven
  - #FLG03/#FLG06 pin_to_pin
  - IC1/IC2 footprint_link_issues
  - U5 lib_symbol_issues
  - U7 lib_symbol_mismatch

## 残作業・要確認

1. **R32 の DNP 属性**：MCP には DNP 属性を設定するツールがない。KiCad で R32 のプロパティを開き、「実装しない（DNP）」にチェックを入れる。
2. **SW2 のフットプリント**：今は `Package_SO:SSO-8_6.8x5.9mm_P1.27mm_Clearance7mm`（ユーザー設定）。KHS42E の外形図と照合する（作業計画 0-6）。
3. **D7 のピン配置**：シンボルは `Diode:SM712_SOT23`（1 = A1、2 = A2、3 = common）。PEC3824C2A-AU のデータシートのピン図と照合する（作業計画 0-5）。
4. **J1/J2 の Manufacturer プロパティ**：MPN（B3B-PH-K-S）は設定済み。Manufacturer（JST）は未設定。
5. **配置**：CAN 回路（IC3、C41、C42、R49、J1、J2）は回路図の下部（x 480〜600、y 520〜555）にまとめた。D7 と SW2 はユーザーが配置した位置（上部）のままで、ラベルでつないでいる。見やすい位置へ動かすかはユーザーが判断する。
