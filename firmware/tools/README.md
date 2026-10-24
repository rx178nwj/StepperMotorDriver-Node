# nemadbg

NEMA23 ステッピングモータドライバの USB CDC デバッグ用 PC ツール。プロトコルは `docs/requirements.md` 3.9 節、FW 側は `App/Src/usb_proto.c`。

## セットアップ

Python 3.9 以降。`firmware/tools/` で実行する。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

pyserial と pyelftools を使う（`protocol.py` と `tests/` のプロトコル試験は標準ライブラリのみ）。

## 使い方

```powershell
python -m nemadbg [--port COMx] [--elf PATH] [--timeout MS] [--sim] <サブコマンド> ...
```

- `--port` 省略時は VID 0483 / PID 5740 のポートを自動検出する（複数あれば一覧を表示してエラー、無ければエラー）。
- `--sim` を付けると実機の代わりに擬似デバイス（シミュレータ、`nemadbg/sim.py`）を使う。ポート検出はしない（`--port` は無視）。状態は実行ごとに初期化され、電源投入後 200 ms 経った状態（DISABLED、VM 24 V、25 ℃）で始まる。全サブコマンドが動く（`dfu` は ENTER_BOOTLOADER の受付まで）。詳細は下の「シミュレータ」。
- `--timeout` は応答待ち [ms]（既定 200）。タイムアウトしたらフレームごとに最大 3 回再送する。ただし `can-req` の書き込み（OP 0x01）とブロードキャスト（CAN_ID 0x000）は二重実行を防ぐため再送せず、タイムアウトしたら「実行されたかは不明」と表示する（`regs` で状態を確認する）。
- 共通オプションはサブコマンドの前後どちらにも書ける。
- どのサブコマンドも終了時に DISCONNECT を送る。ただし `dfu` は、移行すると COM ポートが消えるため DISCONNECT を送らない。

| サブコマンド | 内容 |
|---|---|
| `info` | CONNECT / GET_VERSION / EXCHANGE_ID を実行して表示。`--elf` 指定時は build-id を照合 |
| `syms` | 名前表（INDEX、名前、アドレス、TYPE、NUM）を全列挙 |
| `get NAME...` | 名前指定で読み出し（TYPE に従って整形） |
| `read ADDR SIZE` | アドレス指定で読み、hex ダンプ（240 B ずつ分割） |
| `read-var SYMBOL...` | ELF のシンボル表でアドレス・サイズを解決して読む（`--elf` 必須、build-id 不一致なら読まない） |
| `can-req CAN_ID [DATA_HEX]` | CAN_REQUEST を実行し、基板が返す CAN フレームを表示 |
| `regs [--start 0x00] [--len N]` | CAN レジスタ（3.8 節）を 0x5F まで読んで名前付きで表示（INPUT_STATUS 0x1E、J10_MODE 0x59、HOME_SPEED 0x5A、ENC_COUNT 0x5C を含む。FLAGS は bit3 AGC・bit4 HOME_ACTIVE_HIGH も表示） |
| `watch NAME... --interval SEC [--count N]` | 周期読み出し（Ctrl-C で終了） |
| `events [--duration SEC]` | イベント（異常通知など）を表示し続ける |
| `dfu [--yes]` | ROM ブートローダ（USB DFU）へ移行する（3.13 節）。確認プロンプトの後に CONNECT → ENTER_BOOTLOADER（KEY `0x544F4F42`）を送る。書き込みは行わない |

### dfu

```powershell
python -m nemadbg dfu            # 確認プロンプトあり（y で実行）
python -m nemadbg dfu --yes      # プロンプトを省略
python -m nemadbg dfu --yes --elf ..\build\Release\driver_fw.elf   # 書き込み例にこの ELF のパスを入れる
```

- ENABLED（励磁中）のときは基板が ERR 0x33 で拒否する。`励磁中（ENABLED）のため移行できません。DISABLE してから再実行してください` と表示するので、DISABLE してからやり直す。NO_VM・DISABLED・FAULT では受け付ける。
- ENTER_BOOTLOADER は再送しない。応答だけ失われると移行したかどうか分からなくなるため、無応答のときは「実行されたかは不明」と表示して終了する（COM ポートが消えていれば移行済み）。
- 受け付けられると基板はリセットして ROM ブートローダに移り、COM ポートが消える。このため DISCONNECT は送らず、COM ポートも先に閉じる。
- `STM32_Programmer_CLI` が PATH にあれば、`STM32_Programmer_CLI -l usb` を最大 5 秒（0.5 秒間隔）繰り返し、DFU デバイス（0483:DF11）が列挙されるのを待って結果を表示する。検出できなければ終了コード 1。PATH に無ければ、検出を省いたことを表示する（依存を pyserial・pyelftools のままにするため）。
- 最後に書き込みの手順例（`STM32_Programmer_CLI -c port=usb1 -w <ELF> -v -g 0x08000000`）を表示する。nemadbg は書き込まない。
- 確認プロンプトで y / yes 以外（Enter のみを含む）を答えると、何も送らずに終了コード 1 で終わる。

### 例

```powershell
python -m nemadbg info --elf ..\build\Release\driver_fw.elf
python -m nemadbg syms
python -m nemadbg get usb_stats.rx_frames usb_stats.crc_errors
python -m nemadbg read 0x20000000 64
python -m nemadbg --elf ..\build\Release\driver_fw.elf read-var usb_stats
python -m nemadbg can-req 0x103 020004          # BOARD_ID=3 の DEVICE_ID 等を 4 B 読む
python -m nemadbg regs
python -m nemadbg regs --start 0x04 --len 3
python -m nemadbg watch position speed --interval 0.5 --count 20
python -m nemadbg events --duration 30
```

`regs` と `can-req` の BOARD_ID は CONNECT 応答から得る。`regs` は 6 B ずつ読み出してから、全体が範囲に入るレジスタだけを表示する。

## シミュレータ（`--sim`）

実機がなくても CLI や PC 側 GUI を試せる USB 擬似デバイス。標準ライブラリだけで動き、プロトコル・レジスタ・状態遷移・コマンドの受付条件は `docs/usb_interface.md`、名前表は `docs/usb_symbols.md` と `App/Src/*.c` 末尾の `SYM_ENTRY`（104 件）に合わせてある。

```powershell
python -m nemadbg --sim info
python -m nemadbg --sim regs
python -m nemadbg --sim watch position --interval 0.2 --count 5
python -m nemadbg --sim can-req 0x103 015001      # ENABLE（BOARD_ID は 3）
```

CLI は 1 回の実行ごとに別のシミュレータになるので、状態は次の実行に残らない。連続した操作は Python から使う。

```python
from nemadbg.link import Link
from nemadbg.sim import SimSerial

sim = SimSerial(board_id=3)          # clock=time.monotonic、noise=True、seed=None が既定
link = Link(sim)
link.connect()
sim.set_vm_mv(5000)                  # 注入 API: VM 低下 -> FAULT とイベント
```

- `SimSerial` は serial 互換（`write` / `read(size)` / `in_waiting` / `close`）。`read` はデータが無いとき最大 `timeout`（既定 0.02 s）だけ待つ。
- 内部状態は、要求・`read`・公開 API が呼ばれたときに、前回からの経過時間ぶんを 1 ms 刻みで進める（一度に最大 10 s）。時計は `SimSerial(clock=...)` で差し替えられ、偽の時計のときは実時間で待たない（`tests/test_sim.py`）。
- スレッド安全（Lock で保護）。GUI スレッドから注入 API を呼べる。
- 注入 API: `set_vm_mv`、`set_board_temp_c` / `set_board_temp_dc`（`None` は NTC 断線）、`set_ad_in_mv`、`inject_fault(0x01 OPD / 0x02 ISD / 0x04 TSD)`、`set_home_switches(home1_pos, home2_pos)`（`None` はスイッチ無し）、`set_home_travel_limit`、`set_sensor_present`、`set_shaft_offset`、`set_switches`、`set_external_5v`、`set_save_fails`、`unplug()` / `replug()`、`reboot()`、`drop_responses`（>0 で先頭からその数だけ応答を捨てる、<0 で以後すべて。タイムアウト試験用）。
- `unplug()` 後の `write` / `read` は `SimUnplugged`（`OSError`）を投げる。`replug()` で CONNECT 状態が解除され、最初の要求は ERR 0x35 になる。`ENTER_BOOTLOADER` を受け付けた後も同様にポートが消え、`replug()` で電源再投入相当に戻る。
- 名前表のアドレスは疑似 RAM（`0x20000040` 以降）に割り付けてあり、同じ構造体（`mon.*`、`cfg.*`、`sensor.*`、`home.*`、`usb_stats.*` など）は連続している。`cfg`・`comm_stats`・`usb_stats` の全体登録は各フィールドと同じ領域を指す。ファームに名前を足すと `tests/test_sim.py` の名前表の照合が落ちるので、`sim.py` の `_GROUPS` に追加する。

### 簡略化した点（実機との違い）

- パルスは 1 個ずつ出さず、速度の積分で位置を進める。加減速は時間基準の台形（START_SPEED から ACCEL、MAX_SPEED で頭打ち、DECEL で停止）で、ファームのステップ単位のプランナとは数 step ずれる。位置決めは目標に着いたら即停止する。
- ローカルジョグ（SW1/SW2）は再現しない（STATUS の bit3/bit4 を注入できるだけ）。
- 位置センサ AS5600 は位置から算出する（`SENSOR_RAW = (出力軸角度 + SENSOR_OFFSET)`、軸角度は `POSITION + set_shaft_offset の値` を 1 回転分の step 数で割った剰余）。DIR による反転と I2C 異常は再現しない。起動後 10 ms で有効になり、自動で SYNCED になる。
- 原点復帰は `home.c` の移植。HOME1/HOME2 は片側無限のスイッチ（既定は POSITION >= +20000 で HOME1、<= -20000 で HOME2）。スイッチが無い側へ 100000 step 動くと失敗にする（実機は機械端まで動き続ける）。
- `ENC_COUNT`（J10_MODE=1）は位置に追従する。CAN バスは持たず `comm_stats.*` は常に 0。送信詰まり（50 ms）と SAVE_SETTINGS の応答遅れ（約 25 ms）は再現しない。
- 保存設定はプロセス内のメモリ（既定は保存済みの工場値から始まる。`blank_flash=True` で未保存）。基板温度・MCU 温度・VM には小さな揺らぎが付く（`noise=False` で無効、`seed` で再現可能）。
- `loop.*`・`stack.*` はそれらしい固定値。`reset_csr` は電源投入相当（BORRSTF | PINRSTF）。

## 終了コード

| コード | 意味 |
|---|---|
| 0 | 正常 |
| 1 | エラー（基板が ERR≠0 を返した、無応答、ポート異常、引数の誤り、ELF 異常、`dfu` の中止・DFU デバイス未検出） |
| 2 | build-id 不一致（`info --elf`、`read-var`） |

引数の誤り（argparse）も 2 ではなく 1 で終了する。

## テスト

ハードウェア不要。擬似デバイス（`tests/fakedevice.py`）とシミュレータ（`tests/test_sim.py`）で動かす。

```powershell
python -m unittest discover -s tests
```

# nemagui

`nemadbg` と同じ USB CDC で基板につなぐ PC 側のデバッグ GUI（PySide6）。現状は接続・状態概要・非常停止・操作タブ・設定タブ・内部状態の要点パネルと項目表・名前表ブラウザ・ウォッチ・ログ・統計までを備える。グラフは今後実装する（タブはプレースホルダ）。

## インストールと起動

```powershell
.venv\Scripts\python -m pip install -r requirements-gui.txt   # PySide6 と pyqtgraph（pyserial・pyelftools も入る）
.venv\Scripts\python -m nemagui [--port COMx] [--sim] [--timeout MS]
```

- `--port` を省略すると VID 0483 / PID 5740 のポートを探して、上部のコンボに並べる。1 つなら自動で選んで接続し、複数あれば選ばせる（「再検索」で探し直し）。`--port` 指定時はそのポートに固定する。
- `--sim` は擬似デバイス（`nemadbg/sim.py`）につなぐ。右側に「シミュレータ」ドックが出て、VM・基板温度（NTC 断線）・FAULT 注入（OPD/ISD/TSD）・HOME スイッチ位置・ケーブル抜去／再接続・基板の再起動・応答を捨てる回数を操作できる。
- `--timeout` は応答待ち [ms]（既定 200）。切断されると 1 秒ごとに自動で再接続を試みる（「切断」ボタンで止まる）。
- ウィンドウの位置・サイズ・最後のタブは QSettings に保存する。

## 画面

- 左: ESTOP（非常停止）・STOP（減速停止）と、常時表示の状態概要（STATE の色、FAULT / STATUS / INPUT_STATUS / CONFIG_STATUS のビットランプ、位置・速度・VM・温度など）。切断中はグレーアウトする。
- 中央の「操作」タブ: ENABLE / DISABLE / CLEAR_FAULT、位置決め（目標値の絶対移動・相対移動、−6400 / −200 / +200 / +6400 のクイック相対）、速度指令（RUN_SPEED と「速度 0」）、SET_POSITION / SYNC_POSITION / RESET_ELEC_ANGLE、原点復帰（HOME_NEG / HOME_POS）。上部に STATE・位置・速度・目標到達・HOMING / HOMED / HOME_FAILED / LIMIT_STOP・J10_MODE を小さく表示する。
  - ボタンは usb_interface.md 8.5 の受付条件に合わないとき無効になり、ツールチップに理由を出す（ENABLE は DISABLED のみ、移動・速度指令は ENABLED かつ原点復帰中でない、SET_POSITION などは停止中かつ原点復帰中でない、HOME_* は ENABLED・停止中・J10_MODE=2。未接続はすべて無効。移動方向のリミットは基板に任せる）。これは GUI 側の目安で、最終判断は基板（拒否されるとログに REJECTED が出る）。
  - 表示中だけ J10_MODE を 100 ms ごとに読む（ポーリング組 `ops`）。
- 中央の「設定」タブ: RW レジスタ全部を、表示名・基板の値・編集値・単位・範囲・名前・書き込み結果の表で出す。編集値が基板の値と違う行は黄色で強調する。整数はスピンボックス（範囲外は入力できない）、MICROSTEP・PA7_MODE・J10_MODE はコンボ、FLAGS はビットごとのチェックボックス。
  - 「変更を書き込み」は変更行だけを順に送り、行ごとに OK / REJECTED / INVALID / 不明を出し、全結果が返ったら読み直す。「読み直し」は基板の値を取り直して編集値を戻す。編集中の値はポーリングで上書きしない（基板の値の列だけ更新）。
  - CONFIG_STATUS のランプ（UNSAVED のときは橙の帯でも知らせる）。「Flash に保存」「Flash から読み直し」「工場出荷値（RAM のみ）」は停止中のみ有効で、後ろ 2 つは確認ダイアログを出す。実行後に設定を読み直す。
  - 表示中だけ設定値と CONFIG_STATUS を 1 秒ごとに読む（ポーリング組 `settings`）。
- 中央のその他のタブ: 内部状態（モーション・状態遷移・監視・センサ・J10・原点・通信統計・診断・その他）の項目表。表示中のタブの項目だけを読む（ポーリング組 `tab`）。累積カウンタは値の横に `+N/s` を出す。項目表の行を右クリックすると「ウォッチに追加」できる。
  - 監視・その他を除く各タブは、上部に要点パネル、下に詳細の項目表を置く。
    - モーション: mode（IDLE/POS/VEL）、パルス出力中・保持電流のランプ、方向の矢印、現在速度・プランナ速度・目標速度、現在位置・目標位置・目標までの残り step・確定済み先行位置。
    - 状態遷移・異常: STATE の色、fault / isr_fault のビットランプ、vm_ok・temp_warn・lo_armed・hold_active のランプ、cmd_result、cfg_status のビット。
    - センサ: SENSOR_STATUS と AS5600 STATUS（MH / ML / MD）のランプ、RAW ANGLE・軸角度、I2C 成功・失敗と毎秒の失敗数、連続失敗（3 以上で無効）、PA7 の機能。
    - J10・原点: home.state の段階（IDLE→SEEK→SEEK_STOP→BACK→BACK_STOP→DONE の現在段階を強調、FAILED は別表示）、対象の側、INPUT_STATUS のビット、HOME1 / HOME2 の確定・サンプルのランプと連続回数（`HOME_CONFIRM_SAMPLES` = 2 に対する進捗）、enc_count・agc_out・limit_stopped。
    - 通信統計: CAN と USB のカウンタの値と毎秒の増加量。異常系カウンタ（rx_dropped・tx_dropped・err_*・crc_errors・dropped・rx_overflow・rx_timeouts・bad_len）は 0 でなければ橙、増加中なら赤。err_last_code は 16 進。
    - 診断: 稼働時間（`N 日 hh:mm:ss.mmm`）、reset_csr の解読（立っているフラグと主要因）、ループ周期、スタック使用量のバーと %（75% 以上で橙、90% 以上で赤）。
    - reset_csr の主要因は、IWDG → WWDG → 低消費電力 → ソフトウェア（`ENTER_BOOTLOADER` 等）→ ファイアウォール → 電源投入（POR/BOR）→ オプションバイトのロード → NRST ピンの順に最初に立っているものとする（ソフトウェアリセットやウォッチドッグでも PINRSTF が、電源投入でも BORRSTF と PINRSTF が立つため、NRST ピンは最後）。
- 中央の「名前表」タブ: 接続時に得た名前表の全件を、名前・アドレス・TYPE・NUM・サイズ・グループの表で出す。名前の部分一致で絞り込み、選んで「読む」（1 回読んで値を表示。bytes 型は 16 進ダンプ）、「ウォッチに追加」ができる。
- ウォッチ（右のドック。「表示」メニューで出し入れ）: 名前表の項目・レジスタ項目を集めて、表示名・値・単位・毎秒の増加量を常時表示する。ドックのコンボから追加でき、削除ボタンと周期（既定 200 ms）がある。ポーリング組 `watch` で読み、空なら組を外す。一覧と周期は QSettings に保存して次回起動で復元する（接続後に存在しない項目は無視）。値列は表示内容に合わせて広がる（切れない）。グラフは `MainWindow.watch_dock.keys()` と `changed` シグナルを系列の候補に使う。
- 中央の「グラフ」タブ（pyqtgraph）: 左の一覧（ウォッチ中の項目が先頭で ★、続いて全数値項目。検索フィルタ付き、bytes は対象外）でチェックした項目を描く。プリセット（運動・速度プロファイル・電源・温度・原点復帰・ループ・診断。存在しない項目は除く）で一括選択できる。単位ごとに縦に積んだプロット（X 軸は時刻の相対秒で連動、右端が現在）に色分けして凡例付きで描き、単位のない状態・列挙・ビット値は最下段の「状態」プロットに階段状で描く。Y は各プロットで自動スケール。
    - 時間窓は 10 / 30（既定）/ 60 / 120 s。「一時停止」で表示を止める（データ取得は続く。停止中はドラッグ・ホイールで移動・拡大縮小、最大 120 s 前まで）。「クリア」は表示の開始時刻を今にする（model の履歴は消さない）。マウスを重ねると縦線のカーソルと各系列の値を下に表示する。「PNG 保存」でグラフ部分を画像にする。
    - 描画は 20 fps 以下（QTimer）で、タブを表示していない間と一時停止中は描かない。履歴は `Model.history`（120 s・最大 20000 点）をそのまま使うので、他のポーリング組で読まれていた項目は選んだ時点より前も表示される。
    - ポーリング組 `graph`: 選択中の系列を周期（既定 50 ms、20〜1000 ms）で読む。グラフタブの表示中、または CSV 記録中に登録し、表示も記録もなくなる（または系列が空になる）と外す。タブを離れても記録中は続ける。
    - CSV 記録: 「記録開始」で保存先を尋ね（既定名 `nemagui_YYYYmmdd_HHMMSS.csv`）、`Model.start_csv` で選択中の系列を記録する（UTF-8 BOM、Values を取り込むたびに 1 行、書くたびに flush）。記録中は経過時間・行数・ファイル名を左下とステータスバー（● 記録中）に出し、系列は変更できない。「記録停止」・切断（再接続待ちを含む）・ウィンドウ終了で閉じる。保存先ダイアログは `AppConfig.save_dialog`（または `GraphTab._save_dialog`）で差し替えられる。判断ロジック（系列の振り分け・プリセット・時間窓・階段状データ・ファイル名）は `nemagui/graphmodel.py`（`tests/test_gui_graphmodel.py`）。
- 下: ログ（接続・切断・異常通知・書き込み結果・注意。最大 5000 行）。ステータスバーに通信統計。

## ショートカット

| キー | 動作 |
|---|---|
| `Esc` / `Space` | ESTOP（どのウィジェットにフォーカスがあっても有効。ただしテキスト入力欄での Space は入力として扱われる） |

## テスト

`tests/test_gui_qt.py` は `QT_QPA_PLATFORM=offscreen` で動く（PySide6 が無ければスキップ）。画面を介さない表示ロジック（操作の受付条件を含む）は `nemagui/viewstate.py`、要点パネル・名前表ブラウザ・ウォッチの判定と整形は `nemagui/summary.py`（`tests/test_gui_summary.py`）、設定タブの編集状態は `nemagui/settings_edit.py` にあり、`tests/test_gui_viewstate.py`・`tests/test_gui_settings_edit.py` で試験する。設定タブの確認ダイアログは `AppConfig.confirm`（または `SettingsTab.confirm`）で差し替えられる。
