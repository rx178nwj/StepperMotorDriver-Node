# 電源回路の検証（2026-10-03）

対象: `schematic.kicad_sch`。仕様: `stm32l433_tb67s249_connection.md` 12節。

- J6: 1=12～24V入力、2=GNDPWR。
- F1/Q1/D6/D7: ヒューズ、逆接保護、TVS、VGS制限。
- U5: LMR36510による5V降圧。U6: TPS2116で外部電源優先・USB予備。U7: AP2112K-3.3。
- U8/R44～R46/C34: 電源OFF時に遮断するVM測定、PA0/ADC1_IN5、1/19分圧。
- NRST/BOOT0を既設スイッチ回路に接続。PB8/DRV_AGC0の接続は維持。
- 旧+12VネットをVMへ改名。DRV_VCCは既設ドライバ設定回路専用のまま。

## 接続検証

KiCadが出力したXMLネットリストを `verify_power.py` で照合。

- 指定95接続を検証: 不一致0。
- 既存296接続を維持（VM改名とJ6への入力保護挿入を考慮）: 不一致0。
- VIN_RAW / VIN_FUSED / VM / +5V_EXT / USB_VBUS / +5V / +3V3 は別ネット。
- GNDPWR / GND / GNDA は別ネットで、既存NT1/NT2の接続関係を維持。
- DRV_VCCの全メンバーは変更なし。
- 未使用U5.PG、U7.NC、U8のch2～4出力は未接続。
- 階層検証: status=ok、issue_count=0。
- ERC: 34件（エラー22、警告12）→24件（エラー13、警告11）。新規違反0。

残るERCは既存のMCU未配線ピン（SWD、外部クロック、未割当GPIO）、孤立GNDシンボル、既存の未接続ワイヤー・ラベル、LO0/nFAULTのネット名重複など。追加電源部に未駆動電源・未接続必須ピン・新規ネット名重複はない。24件が残るため、全回路のERCクリーンや製造準備完了とはしない。

## レビューと実機検証の区別

`schematic.pdf` と `power.png` で追加電源部を確認。R/C/ICのフィールドと配線スタブの向きを整理した。`schematic.svg` は同じ最終回路のSVG。

電源回路・ADC・USB切替の接続設計を完了した。PCBは変更していない。実機の起動、切替時電圧降下・突入、ADC精度、USB許容電流、10A級経路の温度、Q1とドライバの放熱、ヒューズのDC遮断/I²t、連続回生への対応は未測定。

TB67S249FTGの4.5Aは絶対最大ピーク値。既設VREFは約1.97A/相に制限されており、この設計は4.5Aの連続運転を保証しない。J6のMSTBA 2.5/2-G-5.08型フットプリント、F1の5x20mmクリップ、L2のXAL6060-223MECを設定した。実ヒューズ、嵌合コネクタ、コンデンサのDCバイアス後容量は調達部品と照合する。

再検証: `python review/power-after/verify_power.py`（XMLの再出力はKiCad MCPで行う）。
