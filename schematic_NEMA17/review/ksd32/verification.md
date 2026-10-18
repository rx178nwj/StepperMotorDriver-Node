# SW4: 秋月電子 KSD32への変更 — 2026-10-04

ユーザー指定の[秋月電子109992](https://akizukidenshi.com/catalog/g/g109992/)を採用。メーカーOTAX、型番KSD32、3極スライドDIP、スルーホール実装。取得した商品ページは税込80円/個、通販購入可能数370個（変動あり）。8台分は640円、予備2個込み10個は800円、送料別。

[メーカー資料](https://akizukidenshi.com/goodsaffix/KSDxx.pdf)で定格5VDC/10mA、接点抵抗50mΩ以下、使用温度−30〜85℃を確認。アドレス入力3.3V、10kΩプルダウンでON時約0.33mA/極のため定格内。

SW4のValue=KSD32、Manufacturer=OTAX、MPN=KSD32、Supplier=Akizuki、Supplier Part Number=109992、Datasheetを回路図へ反映。

フットプリントは標準ライブラリの `Button_Switch_THT:SW_DIP_SPSTx03_Slide_9.78x9.8mm_W7.62mm_P2.54mm` を割当。6端子、2.54mmピッチ、列間7.62mm。メーカー取付孔図の2.54mmピッチ、列間7.6±0.1mm、縦方向約5.1mmに整合する。標準フットプリントの本体外形9.78×9.8mmは実部品7.5×9.6mmより広く、保守的な配置領域になる。厳密な本体外形/高さのモデルではない。

1–6/2–5/3–4の接点ペアを維持し、DIP1=ADDR0（PH0）、DIP2=ADDR1（PH1）、DIP3=ADDR2（PC13）、OFF=0/ON=1、ID0〜7の機能は変更なし。

before/after XMLで全ネットの所属が一致。ERCは前後ともエラー0、既存警告1、新規違反0。USB Protectionライブラリ警告が残る。PCBへの配置・更新・実装チェックは今回未実施。
