# STM32L433CCU6 周辺回路の検証・追加（2026-10-03）

対象: schematic.kicad_sch、IC4、UFQFPN48。KiCad MCPで編集・保存。

## 確認と変更

- VDD pin24/48: +3V3、C1/C2各100nF、C3=4.7uFを確認。
- VBAT pin1: +3V3、C38=100nFを追加。外部バックアップ電池なし。
- VDDUSB pin36: +3V3、既設100nFにC40=1uFを追加。
- VDDA/VREF+ pin9: L1からVDDA、既設C4=2.2uF/C5=100nFにC39=10nFを追加。
- VSSA/VREF- pin8: GNDA、NT2を介してGND。GNDAに#FLG010を追加。これはERC電源駆動宣言であり物理的な電源ではない。
- VSS pin23/35/47、EP pin49: GNDを確認。
- NRST pin7: SW1とC6=100nFを確認。内蔵プルアップを使用。J8 pin5へ接続。
- BOOT0 pin44: SW5とR8=10kプルダウンを確認。旧ネット名PB8_BOOTを保持。PB8 pin45はDRV_AGC0であり別信号。
- J8追加: 1=3V3 VTref、2=PA13/SWDIO、3=GND、4=PA14/SWCLK、5=NRST、6=PB3/SWO。1x6 2.54mmヘッダでありARM標準10pinコネクタではない。VTrefは電圧検出に使い、デバッガの給電出力を接続しない。
- R2と孤立VREF+枝線を削除。この48pinパッケージはVDDA/VREF+共用のため、独立したVREF+端子はない。VDDA、DAC_VREF、DRV_VREFの既設接続を保持。
- PC13/ADDR2にR58=10kプルダウンを追加し、初期アドレスビットを0に設定。
- PA0/VM_SENSEに仕様書12.6の回路を追加: R44=180k/0.1%、R45=10k/0.1%、U8=TMUX1511PWR、R46=4.7k、C34=100nF。U8のデカップリングは新規C41=100nF（現在のC37は別用途で既設）。ch2-4のSEL/SはGND、DはNC。
- PH0/PH1は内蔵クロック使用を前提にNC。未割当PA1/PA7/PB2/PA15/PB4/PB5もNC。

## 検証結果

- KiCad XMLネットリストによる15ネット・69接続の照合: 不一致0。
- R2の意図的削除を除き、既存375ノードの接続を保持。既存ネット間の意図しない短絡0。
- 階層検証: issue_count=0。
- ERC: 26件（エラー14/警告12）→8件（エラー1/警告7）。MCU周辺の未接続、浮遊配線、GNDA未駆動、VM_SENSE/ADDR2孤立の指摘を解消。
- 残存: #PWR03の孤立GND（エラー1）、L0/ENABLE/DMODE_EN/nFAULT/LO0/LO1の孤立ラベル（警告6）、LO0とnFAULTの同一ネット上の異なる名前（警告1）。全回路のERCクリーンとはしていない。
- PDFを出力しMCU周辺・電圧測定回路を画像で確認。

再検証: MCPでafter/schematic.xmlを再出力してから `python review/stm32_support/verify_support.py`。

## ファームウェア・実装条件

内蔵HSI/MSIをシステムクロックに使用し、USBはHSI48とCRS/SOFで設定する。PC14/PC15は既設PWR_ST/DRV_MOに使用しているためLSEには使わない。BOOT0の動作はオプションバイトと合わせる。VDDA/VREF+共用パッケージではVREFBUF出力を無効にする。

PA0はADC1_IN5、アナログモード・プルなし。12～24VのVMを1/19に分圧。VM=ADC/4095×VDDA×19、VREFINTからVDDAを補正する。切替後10ms以上待ち、初期サンプル時間247.5サイクル以上。U8の電源OFF保護は信号3.6V以内が条件。分圧の理論フルスケール62.7Vは基板の入力定格ではない。

実機測定とPCB配置・配線は今回の検証に含まない。各デカップリングを対象ピン近傍へ配置する。VDDのC1/C2をそれぞれpin24/48、C38をpin1、C40と専用100nFをpin36、C39/C4/C5をpin9付近に配置する。

参照: [ST DS11449](https://www.st.com/resource/en/datasheet/stm32l433cc.pdf)、[ST AN4555](https://www.st.com/resource/en/application_note/DM00125306-.pdf)、[TI TMUX1511](https://www.ti.com/lit/ds/symlink/tmux1511.pdf)。
