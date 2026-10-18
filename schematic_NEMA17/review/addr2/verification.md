# ADDR2アドレス選択入力の修正 — 2026-10-04

変更前はIC4 PC13 pin2とR58=10kΩのプルダウンだけが接続されており、Highにする手段がなかった。

JP1（Jumper:SolderJumper_2_Open）を追加し、pin1=ADDR2、pin2=+3V3とした。フットプリントはJumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm。R58のGNDへのプルダウンを維持。

- JP1開放：ADDR2=Low、アドレス選択値0。
- JP1はんだ短絡：ADDR2=High、アドレス選択値1。R58電流は公称0.33mA。
- PC13はGPIO入力、内部プルなし。起動時に読み、STM32 I2C2のOwn Address設定へ反映する。
- 具体的な7bitアドレスの値とADDR2のビット位置は未指定なので確定していない。ADDR2の名前だけからbit2とは決めない。
- 本ディレクトリには実装対象のファームウェアがないため、回路図の修正と設定要件の記録まで。ハードウェア変更だけでアドレスが自動変更されるものではない。
- PCA9615はプロトコルに透過的な差動バッファであり、ADDR2はPCA9615のアドレス端子ではない。[NXP資料](https://www.nxp.com/docs/en/data-sheet/PCA9615.pdf)

実ネットをbefore/after XMLで照合し、既存接続の変更・不意の短絡がないことを確認。階層問題0、ERC前後ともエラー0/既存警告1、新規違反0。警告はUSB Protectionライブラリの未登録。
