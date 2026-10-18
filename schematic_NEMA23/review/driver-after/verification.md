# STM32L433–TB67S249 接続変更の検証結果

対象: schematic.kicad_sch
仕様: stm32l433_tb67s249_connection.md

既存部品との番号重複を避け、下表の番号で実装した。配置済み IC2 のカスタムシンボルを使用し、新規周辺部品には標準ライブラリを使用した。

| 仕様の参照番号 | 実装の参照番号 | 内容 |
|---|---|---|
| U2 | IC2 | TB67S249FTG |
| R10 | R23 | OSCM 5.1k 1% |
| R11 | R24 | LTH 100k 1% |
| R16 | R25 | ENABLE 10k プルダウン |
| R17/R18/R19 | R26/R27/R28 | LO1/LO2/MO 10k プルアップ |
| R20/R21 | R29/R30 | VREF 10k / 9.1k 分圧 |
| R50–R57 | R50–R57 | AGC設定、下側4本DNP |
| C10 | C15 | 220uF 63V 低ESR |
| C11/C12 | C16/C17 | VMA/VMB 100nF 100V X7R |
| C13 | C18 | 10uF 50V X7R |
| C14 | C19 | VCC 100nF 16V X7R |
| C15 | C20 | OSCM 270pF C0G |
| C16 | C21 | VREF 100nF 16V X7R |
| — | NT1 | PGND–GND 1点接続 |
| — | J4 | モータ端子、1=A+, 2=A-, 3=B+, 4=B- |

PA9 (IC4 pin30) の既存 USB_PC_VBUS_SENSE 配線を除去し、仕様どおり DRV_DIR へ変更した。USB の VBUS 検出分圧回路自体は残っているが、MCUへの検出接続はなくなった。

## 検証

- KiCad出力のXMLネットリストで29ネット、107接続を照合: 不一致0。
- IC2の全49ピンを照合。NCは9,12,25,28,36,37,38の7本。全NCに未接続フラグを付与。
- VREFA/VREFBはDRV_VREF。PA4はDAC_VREF。両ネット間にはR29があり、短絡なし。
- DRV_VCCはIC2のVCC×2、C19、R23、R50/R52/R54/R56のみ。
- LO1/LO2/MOのプルアップは+3V3_D。設定抵抗のプルアップはDRV_VCC。
- NT1の各端子は別ネットPGND/GND。PGNDに接続されるのはドライバの電流リターン、VMコンデンサ、NT1のみ。
- 階層検証: issue_count=0。
- ERC: 115件から54件へ減少（エラー34、警告20）。追加変更に由来する新規指摘なし。残存指摘は既存回路のMCU未接続ピン、未駆動電源、未接続ラベル、既存ネット名重複、フットプリント設定など。
- MCU電源、アナログ電源、クロック、SWD等は仕様対象外で、未接続部分が残る。VM/+3V3_D供給回路も本変更では追加していない。通電・製造可能な完成回路という意味ではない。
- 1点接続の位置、同名ピンの近接配線、サーマルビア、出力配線幅は回路図注記に記載。PCBへの実装とDRCは本変更の対象外。
- C15の電解コンデンサとJ4のフットプリントは標準ライブラリの候補。部品確定時に実部品寸法・定格との照合が必要。

検証データ: connection-check.json / schematic.xml / schematic-erc.json
再照合: python review/driver-after/verify_connections.py
回路図確認用: schematic.pdf

