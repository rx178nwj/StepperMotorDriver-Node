# USB インタフェース仕様書（PC アプリ開発者向け）

| 項目 | 内容 |
|------|------|
| 対象 | NEMA23 ステッピングモータードライバ（STM32G4、ファームウェア 0.4、プロトコル 1.0） |
| 目的 | PC 側の任意のアプリ（Python / C# / C++ / Rust 等）が USB 経由で本機を制御・監視できるようにする |
| 版 | 0.1（2026-10-20） |
| 正 | 本書は `requirements.md` 3.8 / 3.9 節と実装（`firmware/App/Src/usb_proto.c` ほか）から起こした。食い違いがあれば実装を正とし、本書を直す |
| 参照実装 | `firmware/tools/nemadbg/protocol.py`（フレーム処理）、`link.py`（要求→応答・再送）、`registers.py`（レジスタ表） |

本書だけで PC 側クライアントを実装できるよう、バイト列・数値・手順・例を省略せずに書く。

---

## 1. 全体像

```
PC アプリ ── 仮想COMポート(CDC ACM) ── フレーム ── 本機
                                        │
                                        ├─ メモリ/名前表の読み出し（デバッグ用）
                                        └─ CAN_REQUEST（CAN と同じレジスタ操作を USB で代行）
```

- 物理層は USB CDC ACM（仮想 COM ポート）1 本。ドライバ不要（Windows は標準 usbser、Linux は cdc_acm、macOS は標準）。
- 上位は独自のバイナリフレーム。**モータの操作・設定はすべて CAN_REQUEST（CMD 0x40）経由でレジスタを読み書きして行う**（8章）。
- 1 要求に対して必ず 1 応答（要求は同時に 1 つだけ。応答を待ってから次を送る）。
- 本機から自発的に送るのはイベント（4章）だけ。

---

## 2. USB デバイス

| 項目 | 値 |
|------|----|
| クラス | CDC ACM（デバイスクラス 0x02/0x02/0x00） |
| VID / PID | 0x0483 / 0x5740 |
| 製品名文字列 | `NEMA23 Stepper Driver` |
| メーカ文字列 | `STMicroelectronics` |
| シリアル番号 | UID 由来の 12 桁 16 進（個体ごとに異なる） |
| エンドポイント | Bulk OUT 0x01 / Bulk IN 0x81（各 64 B）、Interrupt IN 0x82 |
| 速度 | Full Speed |

### 2.1 デバイスの見つけ方

1. 列挙された COM ポートから VID 0x0483 / PID 0x5740 のものを探す。
2. **複数見つかった場合は自動で選ばず、ユーザーに選ばせる**（nemadbg も同じ扱い）。個体の区別にはシリアル番号を使う。
3. 同じ VID/PID は ST の標準 CDC サンプルにも使われるため、製品名 `NEMA23 Stepper Driver` でも絞り込める。接続後の CONNECT 応答の DEVICE_ID（0xA3）で本機であることを最終確認する。

### 2.2 ポートの開き方

- ボーレート・ビット長・パリティ・ストップビット・DTR/RTS は**本機側で無視される**。任意の値でよい（115200 8N1 を推奨。OS によっては 0 を拒否する）。
- フロー制御は使わない。
- 読み取りは非ブロッキングまたは短いタイムアウトで、受信バイト列をストリームとして扱う（1 回の read が 1 フレームに対応するとは限らない。フレームの途中で切れる・複数フレームがまとまることがある）。
- ポートを開いた直後は何も送ってこない。ケーブルを抜く・本機がリセットするとポートが消える。再接続は再列挙後に最初から（CONNECT から）やり直す。

---

## 3. フレーム形式

### 3.1 構造

```
+------+-----+----------------+----------+----------+
| SOF  | LEN |      BODY      | CRC16 lo | CRC16 hi |
| 0xA5 | 1B  |   LEN バイト    |    1B    |    1B    |
+------+-----+----------------+----------+----------+
```

| 項目 | 規則 |
|------|------|
| SOF | 固定 0xA5 |
| LEN | BODY のバイト数。**2〜250**。フレーム全長は最大 254 B |
| BODY | 4章以降。先頭バイトが種別（要求は CMD、応答は 0xFF、イベントは 0xFE） |
| CRC16 | CRC-16/CCITT-FALSE（多項式 0x1021、初期値 0xFFFF、入出力とも反転なし、最終 XOR なし）。**対象は LEN と BODY**（SOF は含まない）。**下位バイトが先**（リトルエンディアン） |
| 多バイト値 | BODY 内の数値はすべてリトルエンディアン |

検査値: ASCII `123456789` の CRC は 0x29B1。

CRC 参考実装（Python）:

```python
def crc16(data: bytes) -> int:
    c = 0xFFFF
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c

def encode_frame(body: bytes) -> bytes:
    assert 2 <= len(body) <= 250
    d = bytes([len(body)]) + body
    c = crc16(d)
    return b"\xA5" + d + bytes([c & 0xFF, c >> 8])
```

### 3.2 BODY の種類

| 方向 | BODY | 備考 |
|------|------|------|
| PC→本機（要求） | `[CMD][CTR][PARAM...]` | CTR は PC が付ける 1 バイトの通し番号 |
| 本機→PC（応答） | `[0xFF][ERR][CTR][DATA...]` | CTR は要求の値をそのまま返す。ERR≠0 のとき DATA は付かない |
| 本機→PC（イベント） | `[0xFE][EVT][DATA...]` | 4章 |

- CTR は 0〜255 を循環させてよい。応答の CTR が要求と一致しないものは捨てる（古い応答・再送の残り）。
- CMD の値 0xFF・0xFE は使わない。

### 3.3 エラーコード（応答の ERR）

| ERR | 名前 | 意味 |
|-----|------|------|
| 0x00 | OK | 正常 |
| 0x30 | UNKNOWN_CMD | 未定義のコマンド |
| 0x31 | SYNTAX | BODY 長がそのコマンドの形式と合わない |
| 0x32 | RANGE | 値が範囲外（SIZE・INDEX・CAN_ID・DLC・KEY・名前長・OFFSET など） |
| 0x33 | DENIED | 読み出し許可領域の外、または ENABLED 中の ENTER_BOOTLOADER |
| 0x35 | LOCKED | CONNECT していない |
| 0x36 | NO_RESOURCE | 名前がない・build-id がない・Flash ECC エラー |

0x34 は未定義（受信しても未知のエラーとして扱う）。

### 3.4 受信側の規則（本機の挙動）

PC が守るべきこと、および本機がこう動くという前提。

| 状況 | 本機の動作 |
|------|-----------|
| LEN が 2〜250 の範囲外 | その LEN のバイトを SOF 候補として再探索（同期回復） |
| CRC 不一致 | **応答を返さず捨てる**。フレーム末尾の次から SOF を探す |
| フレームの途中でバイトが 20 ms 途切れる | 受信途中のフレームを捨てる |
| 要求受信から応答開始まで | 10 ms 以内（通常）。例外: SAVE_SETTINGS 実行中は Flash 消去で最大約 25 ms 遅れる |
| 本機の送信が 50 ms 以上詰まる（PC が読んでいない） | 応答・イベントを破棄する。詰まり中に受けた要求は実行せず捨てる（エラー応答もない） |
| USB バスリセット・サスペンド | 受信途中の状態と未送信の応答・イベントを破棄。**CONNECT 済みの状態も解除される** |
| 未 CONNECT | CONNECT と GET_VERSION 以外はすべて ERR=0x35（未定義 CMD も 0x35） |

**重要**: CRC 不一致や化けは応答がない形で現れる。PC 側は必ず**タイムアウトと再送**を実装する（6章）。

---

## 4. イベント

本機が自発的に送る BODY。現在は 1 種類のみ。

### EVT 0x01 CAN_FRAME

```
BODY = [0xFE][0x01][CAN_ID u16][DLC][DATA DLC バイト]
```

- 本機が CAN バスに送るのと同じ内容の通知を、USB にも送る。
- 現在発生するのは**異常通知**のみ: STATE が FAULT に遷移したとき、`CAN_ID = 0x080 + 基板ID`、`DLC = 2`、`DATA = [STATE][FAULT]`（STATE・FAULT の意味は 8.3 章）。
- **CONNECT 済みのときだけ**送られる。保留は 1 個で、未送信のまま次のイベントが来ると上書きされる（取りこぼしあり）。
- PC は応答待ちの最中でもイベントを受け取りうる。**受信したフレームの BODY 先頭が 0xFE ならイベントとして別処理し、応答の待機を続ける**。

---

## 5. コマンド一覧

BODY 長は CMD と CTR を含む。`u16`/`u32` はリトルエンディアン。成功時の応答は `[0xFF][0x00][CTR][DATA]`。

| CMD | 名前 | 要求 PARAM | 応答 DATA | 備考 |
|-----|------|-----------|-----------|------|
| 0x01 | CONNECT | なし（BODY 長 2） | 7 B: `[0x01][0x00][DEVICE_ID][FW_MAJOR][FW_MINOR][BOARD_ID][MAX_LEN]` | 最初に必ず実行。MTA を 0 にリセット。再実行可 |
| 0x07 | DISCONNECT | なし（2） | なし | 未接続なら ERR=0x35。保留イベントを破棄 |
| 0x1B | GET_VERSION | なし（2） | 2 B: `[PROTO_MAJOR=1][PROTO_MINOR=0]` | 未接続でも可 |
| 0x17 | EXCHANGE_ID | なし（2） | 21 B: `[20][build-id 20 B]` | ファームの ELF と照合する用。build-id なしは 0x36 |
| 0x02 | SET_MTA | `[ADDR u32]`（6） | なし | 以後の UPLOAD の読み出し開始アドレス。検査は UPLOAD 時 |
| 0x04 | UPLOAD | `[SIZE u8]`（3） | SIZE バイト | SIZE は 1〜240。MTA から読み、成功すると MTA += SIZE |
| 0x0F | SHORT_UPLOAD | `[SIZE u8][ADDR u32]`（7） | SIZE バイト | MTA を変えずに 1 回で読む |
| 0x20 | SYM_COUNT | なし（2） | `[COUNT u16]` | 名前表の登録数 |
| 0x21 | SYM_INFO | `[INDEX u16]`（4） | `[ADDR u32][TYPE u8][NUM u16][NLEN u8][NAME NLEN B]` | INDEX が範囲外は 0x32 |
| 0x22 | SYM_FIND | `[NLEN u8][NAME]`（3+NLEN） | `[INDEX u16][ADDR u32][TYPE u8][NUM u16]`（9 B） | NLEN は 1〜31、完全一致・大文字小文字区別。未登録は 0x36 |
| 0x23 | SYM_UPLOAD | `[NLEN][NAME][OFFSET u16][SIZE u8]`（NLEN+6） | SIZE バイト | SIZE=0 は OFFSET から変数末尾まで（240 超は 0x32）。8 バイト以下の変数は一貫した値で返る |
| 0x40 | CAN_REQUEST | `[CAN_ID u16][DLC u8][DATA DLC B]`（5+DLC） | `[N u8]` + N×`[CAN_ID u16][DLC][DATA]` | 8章。N は 0 か 1 |
| 0x50 | ENTER_BOOTLOADER | `[KEY u32]`（6） | なし | 9章。KEY は 0x544F4F42（バイト列では `42 4F 4F 54`）。他は 0x32 |

TYPE（SYM_INFO / SYM_FIND）: 名前表の型コード。値と登録一覧は `usb_symbols.md` と `nemadbg/protocol.py` の `SYM_TYPES` を参照。

CONNECT 応答の各値: DEVICE_ID=0xA3（本機の識別）、FW バージョン、BOARD_ID（DIP スイッチ 0〜7）、MAX_LEN=250。

メモリ読み出し（UPLOAD 系）は許可された領域（RAM・Flash 等）のみ。領域外は 0x33、Flash の ECC エラーは 0x36。通常のモータ制御アプリでは使わない（デバッグ・解析用）。

### 5.1 フレーム例

すべて 16 進。CTR=0 の場合。

| 内容 | フレーム |
|------|---------|
| CONNECT 要求 | `A5 02 01 00 CD 91` |
| GET_VERSION 要求（CTR=1） | `A5 02 1B 01 54 6D` |
| CONNECT 応答（FW 0.4、基板ID 3） | `A5 0A FF 00 00 01 00 A3 00 04 03 FA 27 00` |
| ENTER_BOOTLOADER 要求（CTR=4） | `A5 06 50 04 42 4F 4F 54 92 1C` |
| CAN_REQUEST: 基板ID 3 に ENABLE を書く（CTR=3） | `A5 08 40 03 03 01 03 01 50 01 1C C9` |
| CAN_REQUEST: 基板ID 3 の STATE を読む（CTR=2） | `A5 08 40 02 03 01 03 02 04 01 56 EA` |

（CAN_REQUEST の例は、CAN_ID=0x103=0x100+3、DLC=3、DATA は書き込み `01 50 01`／読み出し `02 04 01`。）

実装の自己検査として、これらのフレームを `encode_frame` が出力と一致するか単体テストにすること。

---

## 6. 通信手順

### 6.1 標準シーケンス

```
1. ポートを開く
2. CONNECT                      → DEVICE_ID == 0xA3 を確認、BOARD_ID を控える
3. GET_VERSION（任意）          → PROTO_MAJOR == 1 を確認（違えば接続を中止）
4. CAN_REQUEST でレジスタ操作   （8章）
   ・定期的に状態を読む
   ・イベント(0xFE)は随時受信
5. DISCONNECT → ポートを閉じる
```

### 6.2 要求・応答・再送

- 要求を送り、応答（BODY 先頭 0xFF かつ CTR 一致）を待つ。**既定タイムアウトは 200 ms**（SAVE_SETTINGS を含む要求は余裕を持たせる）。
- タイムアウトまたは CRC 不一致なら**再送してよい（最大 3 回）。ただし次の要求は再送しない**:
  - 副作用がある CAN_REQUEST（書き込み、ブロードキャスト）。本機が実行済みで応答だけ失われた可能性があるため、再送すると二重実行になる。必要ならレジスタを読み戻して確認する。
  - ENTER_BOOTLOADER。
  - 読み出し系（CONNECT、GET_VERSION、EXCHANGE_ID、SET_MTA、UPLOAD 系、SYM_*、読み出し CAN_REQUEST）は再送してよい。ただし UPLOAD は成功で MTA が進むため、再送でなく SHORT_UPLOAD を使うと安全。
- 再送するとき CTR を変えるかは任意。同じ CTR で再送した場合、遅れて届いた最初の応答と区別がつかないが、読み出し系なら問題ない。
- 応答が ERR≠0 なら DATA は無い。エラーコード（3.3 章）で原因を判断する。

### 6.3 受信パーサ（クライアント側）

ストリームから次の手順でフレームを取り出す（`nemadbg/protocol.py` の `FrameParser` が参照実装）。

1. バッファ先頭が SOF(0xA5) でなければ、SOF まで読み捨てる。
2. LEN（次の 1 バイト）が 2〜250 でなければ、SOF の次のバイトから SOF を再探索。
3. SOF+1+LEN+2 バイトそろうまで待つ。
4. CRC を検査。不一致なら **SOF の次のバイトから再探索**（1 バイト進める）。
5. 一致したら BODY を取り出して先頭バイトで種別判定（0xFF 応答／0xFE イベント）。

本機は CRC 不一致時にフレーム本体を再走査しないが、PC 側は 1 バイトずつ進める方が堅牢（化けたフレームの途中に本物の SOF がある場合を拾える）。PC 側には時間切れがなく、途中で途切れたバッファが残りうるので、要求ごとのタイムアウト時にパーサのバッファを破棄することを推奨する。

### 6.4 CONNECT 状態

- CONNECT 成功で「接続済み」になる。バスリセット・サスペンド・ケーブル抜去・DISCONNECT で解除される。
- 本機が突然リセットしたり USB が再列挙された場合、以降の要求は ERR=0x35（LOCKED）になる。0x35 を受けたら CONNECT からやり直す。
- 接続済みのときだけイベントが届く。

---

## 7. データ型・単位の規約

| 型 | 内容 |
|----|------|
| u8/u16/u32 | 符号なし、リトルエンディアン |
| i16/i32 | 2 の補数、リトルエンディアン |
| 位置 | step（マイクロステップ後の単位）。符号付き 32 bit |
| 速度 | step/s（符号付き）。加速度は step/s² |
| 電流 | mA |
| 電圧 | mV（VM_MV は 0xFFFF で飽和） |
| 温度 | 0.1 °C（i16）。基板温度の 0x8000 は無効 |
| 角度 | 0.01°（0〜35999）。0xFFFF は無効 |

---

## 8. CAN_REQUEST とレジスタ操作

### 8.1 CAN_REQUEST の仕組み

USB フレーム（CMD 0x40）の PARAM に、CAN バスで送るのと同じフレーム（CAN_ID・DLC・DATA）を入れる。本機は CAN 受信と同じ処理を行い、**応答を CAN には送らず USB 応答の DATA に入れて返す**。したがって CAN 用に定義された全レジスタ・全コマンドが USB で使える。

```
要求 BODY: [0x40][CTR][CAN_ID u16][DLC][DATA DLC バイト]
応答 BODY: [0xFF][0x00][CTR][N][ {CAN_ID u16, DLC, DATA} × N ]
```

- 要求の BODY 長が 5+DLC と合わなければ 0x31。CAN_ID > 0x7FF または DLC > 8 は 0x32。
- N は 0 または 1。応答のない要求（ブロードキャスト等）は `N=0`（DATA は `[0x00]` の 1 バイト）。
- 応答の CAN_ID は `0x180 + 基板ID`（応答フレーム）、中身は 8.2 章。

### 8.2 CAN_ID

基板ID は DIP スイッチで 0〜7（CONNECT 応答の BOARD_ID）。**USB で話しかける相手は、接続している本機 1 台だけ**で、CAN_ID の基板ID はその BOARD_ID に合わせること。

| CAN_ID | 方向 | 用途 |
|--------|------|------|
| 0x000 | PC→本機 | ブロードキャスト（応答なし） |
| 0x080 + ID | 本機→PC | 異常通知（イベントとして届く） |
| 0x100 + ID | PC→本機 | 要求（レジスタ書き込み・読み出し） |
| 0x180 + ID | 本機→PC | 要求への応答 |

### 8.3 要求・応答フレーム（CAN_ID=0x100+ID への DATA）

**書き込み**: `[0x01][REG][DATA 1〜6 B]`（DLC 3〜8）
→ 応答 `[0x81][REG][CMD_RESULT]`（DLC 3）

**読み出し**: `[0x02][REG][LEN 1〜6]`（DLC 3）
→ 応答 `[0x82][REG][DATA LEN B]`（DLC 2+LEN）

**不正要求**（未定義の OP、DLC 不一致、LEN 範囲外）→ `[0xFF][REG][0x02]`（DLC 3）

**ブロードキャスト**（CAN_ID=0x000、DLC=1、DATA 1 バイト）: 2=DISABLE、3=STOP、4=ESTOP のみ実行。応答なし。それ以外は無視。

REG は読み書きを始める先頭アドレス。DATA は連続するレジスタを昇順に並べたもの（例: 0x08 から 4 バイトで POSITION）。1 回で扱えるのは最大 6 B。複数レジスタをまたぐ書き込みは昇順に反映され、32 bit レジスタを途中までしか覆わない書き込みは INVALID。
複数レジスタを書いた場合の CMD_RESULT は、そのうち最後の非 OK の値（すべて OK なら OK）。

**CMD_RESULT**:

| 値 | 意味 |
|----|------|
| 0 | OK |
| 1 | REJECTED（今の状態では受け付けない） |
| 2 | INVALID（値・アドレスが不正。設定値は丸めずに拒否される） |
| 3 | FAILED（実行したが失敗。例: センサ無効での SYNC_POSITION、Flash 書き込み失敗） |

読み出しでマップ外のアドレスは 0xFF、書き込み専用レジスタ（0x40〜0x50）は 0 が返る。

### 8.4 状態とフォールト

**STATE（0x04）**

| 値 | 名前 | 意味 |
|----|------|------|
| 0 | NO_VM | モータ電源（VM）が無い／範囲外 |
| 1 | DISABLED | 電源 OK、出力停止（脱力） |
| 2 | ENABLED | 励磁・運転可能 |
| 3 | FAULT | 異常。CLEAR_FAULT まで運転不可 |

**FAULT（0x05、ビット）**: bit0 OPD（開放負荷）、bit1 ISD（過電流）、bit2 TSD（ドライバ過熱）、bit3 VM_UV（電源低下）、bit4 VM_OV（電源過大）、bit5 OVERTEMP（基板温度 ≥ 80.0 °C）。

状態遷移の要点:
- VM が 10500〜28000 mV に 50 ms 安定すると NO_VM→DISABLED。範囲外が続くと DISABLED→NO_VM。ENABLED 中に VM 異常になると FAULT（VM_UV/VM_OV）。
- 基板温度 70.0 °C 以上で STATUS bit5（警告）、80.0 °C 以上で FAULT（OVERTEMP）。
- FAULT 解除は COMMAND=5（CLEAR_FAULT）。基板温度 ≥ 70 °C の間は REJECTED。

### 8.5 レジスタマップ

R=読み出し、W=書き込み専用、RW=両方。「保存」は設定の不揮発保存の対象。

**状態（読み出し専用）**

| Addr | 名前 | 型 | 内容 |
|------|------|----|------|
| 0x00 | DEVICE_ID | u8 | 0xA3 |
| 0x01 | FW_MAJOR | u8 | 0 |
| 0x02 | FW_MINOR | u8 | 4 |
| 0x03 | BOARD_ID | u8 | 0〜7 |
| 0x04 | STATE | u8 | 8.4 章 |
| 0x05 | FAULT | u8 | 8.4 章 |
| 0x06 | STATUS | u8 | bit0 MOVING（パルス出力中または原点復帰中）、bit1 予約、bit2 外部 5V、bit3 SW1、bit4 SW2、bit5 温度警告、bit6 保持電流中（ENABLED 時）、bit7 目標到達 |
| 0x07 | CMD_RESULT | u8 | 直前の書き込み結果（8.3 章） |
| 0x08 | POSITION | i32 | step |
| 0x0C | SPEED | i32 | step/s（符号付き） |
| 0x10 | VM_MV | u16 | mV |
| 0x12 | BOARD_TEMP | i16 | 0.1 °C、0x8000 は無効 |
| 0x14 | MCU_TEMP | i16 | 0.1 °C |
| 0x16 | AD_IN_MV | u16 | mV。PA7_MODE=0 のときのみ有効、他は 0xFFFF |
| 0x18 | CONFIG_STATUS | u8 | bit0 LOADED、bit1 UNSAVED（RAM に未保存の変更あり）、bit2 SAVE_FAILED、bit3 CORRECTED（読み込み時に値を補正した） |
| 0x19 | SENSOR_STATUS | u8 | bit0 有効、bit1 MH、bit2 ML、bit3 応答なし、bit4 SYNCED、bit5 Z レベル |
| 0x1A | SENSOR_RAW | u16 | 0〜4095、無効は 0xFFFF |
| 0x1C | SHAFT_ANGLE | u16 | 0.01°、無効は 0xFFFF |
| 0x1E | INPUT_STATUS | u8 | bit0 HOME1 作動、bit1 HOME2 作動、bit2 原点復帰中、bit3 原点確定、bit4 原点失敗、bit5 リミット停止 |
| 0x5C | ENC_COUNT | i32 | J10_MODE=1（エンコーダ）のときのカウント。他は 0 |

**設定（読み書き。範囲外は INVALID で拒否）**

| Addr | 名前 | 型 | 範囲・単位 | 既定 | 備考 |
|------|------|----|------------|------|------|
| 0x20 | RUN_CURRENT | u16 | 250〜3000 mA | 1000 | 保存 |
| 0x22 | HOLD_CURRENT | u16 | 250〜3000 mA | 500 | 保存 |
| 0x24 | MICROSTEP | u8 | 1,2,4,8,16,32 | 16 | 保存。停止中のみ変更可 |
| 0x25 | FLAGS | u8 | 下表 | 0x02 | 保存。有効ビットは 0x1F |
| 0x26 | COMM_TIMEOUT | u16 | ms、0=無効 | 0 | 保存 |
| 0x28 | MAX_SPEED | u32 | 1〜100000 step/s | 3200 | 保存 |
| 0x2C | ACCEL | u32 | 1〜10,000,000 step/s² | 16000 | 保存 |
| 0x30 | DECEL | u32 | 1〜10,000,000 step/s² | 16000 | 保存 |
| 0x34 | START_SPEED | u32 | 1〜100000 step/s | 200 | 保存 |
| 0x38 | JOG_SPEED | u32 | 1〜100000 step/s | 1600 | 保存 |
| 0x3C | HOLD_DELAY | u16 | ms | 500 | 保存。停止後この時間で保持電流へ |
| 0x3E | STEPS_PER_REV | u16 | 1〜65535 | 200 | 保存 |
| 0x54 | GEAR_RATIO | u16 | 1〜1000 | 20 | 保存 |
| 0x56 | SENSOR_OFFSET | u16 | 0〜4095 | 0 | 保存 |
| 0x58 | PA7_MODE | u8 | 0 AD_IN、1 DIR、2 Z | 1 | 保存 |
| 0x59 | J10_MODE | u8 | 0 未使用、1 ENC、2 HOME | 0 | 保存。停止中のみ変更可 |
| 0x5A | HOME_SPEED | u16 | 1〜65535 step/s | 800 | 保存 |

FLAGS のビット: bit0 方向反転、bit1 ローカルジョグ有効、bit2 センサ DIR、bit3 AGC、bit4 HOME 入力の論理（0=アクティブ Low、1=アクティブ High）。ENABLED 中に bit3（AGC）を変える FLAGS 書き込みは全体が REJECTED になる。

**動作指令（書き込み専用）**

| Addr | 名前 | 型 | 内容 | 受付条件 |
|------|------|----|------|----------|
| 0x40 | MOVE_ABS | i32 | 絶対位置へ移動（step） | ENABLED。原点復帰中でなく、移動方向のリミットが作動していない |
| 0x44 | MOVE_REL | i32 | 相対移動（step） | 同上 |
| 0x48 | RUN_SPEED | i32 | 速度指令（step/s、符号付き）。0 で減速停止 | 同上（0 は原点復帰中でも可） |
| 0x4C | SET_POSITION | i32 | 現在位置の値を書き換える | 停止中、かつ原点復帰中でない（状態は問わない） |
| 0x50 | COMMAND | u8 | 下表 | 下表 |

**COMMAND（0x50）**

| 値 | 名前 | 動作・条件 |
|----|------|------------|
| 1 | ENABLE | DISABLED のときのみ。励磁して運転可能に。起動は約 5 ms 後 |
| 2 | DISABLE | ENABLED のときのみ。即停止し脱力。原点復帰を中止 |
| 3 | STOP | 状態不問、常に OK。減速停止。原点復帰を中止 |
| 4 | ESTOP | 状態不問、常に OK。即停止（励磁は維持）。原点復帰を中止 |
| 5 | CLEAR_FAULT | FAULT のときのみ。基板温度 ≥ 70 °C は REJECTED。VM OK なら DISABLED、そうでなければ NO_VM |
| 6 | RESET_ELEC_ANGLE | 駆動回路起床中かつ停止中・原点復帰中でない |
| 7 | SAVE_SETTINGS | 停止中のみ。Flash に保存（実行中は CPU が最大約 25 ms 止まり USB 応答も遅れる） |
| 8 | LOAD_SETTINGS | 停止中のみ。Flash から読み直す |
| 9 | FACTORY_SETTINGS | 停止中のみ。既定値に戻す（RAM のみ。保存は別途 SAVE_SETTINGS） |
| 10 | SYNC_POSITION | 停止中・原点復帰中でない。センサ角度から POSITION を合わせる。センサ無効は FAILED |
| 11 | HOME_NEG | 原点復帰（−側）。ENABLED・停止中・J10_MODE=2 のみ |
| 12 | HOME_POS | 原点復帰（＋側）。同上 |

上記以外の値は INVALID。

### 8.6 動作上の注意

- **通信タイムアウト（COMM_TIMEOUT≠0）**: ジョグ中を除き、最後の自局宛て要求から指定 ms を超えると減速停止する。計時をリセットするのは **CAN で受信した自局宛て 0x100+ID 要求だけ**で、**USB の CAN_REQUEST は計時を延ばさない**（生存通知にならない。ブロードキャストや CONNECT・GET_VERSION も対象外）。COMM_TIMEOUT≠0 で CAN の要求が来ない状態（USB だけで運転する場合）では、運転を始めるとタイムアウトで減速停止するので、USB だけで試すときは COMM_TIMEOUT=0 のままにする。
- **原点復帰**（J10_MODE=2）: HOME1（＋側）・HOME2（−側）入力。探索速度は min(HOME_SPEED, MAX_SPEED)。完了で POSITION=0・INPUT_STATUS bit3。失敗は bit4。STOP・ESTOP・DISABLE・通信タイムアウト・FAULT で中止。
- **リミット停止**: J10_MODE=2 のとき、作動中の側へ動くと ESTOP し INPUT_STATUS bit5 を立てる（FAULT にはならない）。逆方向へは動ける。
- **設定変更後**: 変更は RAM に反映され CONFIG_STATUS bit1（UNSAVED）が立つ。電源を切っても残すには SAVE_SETTINGS（COMMAND=7）。
- **電源投入後の位置**: センサが有効になった時点で位置が自動同期される（原点復帰完了後は対象外）。

### 8.7 操作例（基板ID=3）

| 目的 | CAN_ID | DATA（16 進） |
|------|--------|---------------|
| ENABLE | 0x103 | `01 50 01` |
| 絶対 +3200 step へ移動（3200=0x0C80） | 0x103 | `01 40 80 0C 00 00` |
| 速度 1600 step/s で連続回転（1600=0x0640） | 0x103 | `01 48 40 06 00 00` |
| 減速停止 | 0x103 | `01 50 03` |
| STATE と FAULT を読む（2 B） | 0x103 | `02 04 02` |
| POSITION を読む（4 B） | 0x103 | `02 08 04` |
| RUN_CURRENT を 1500 mA に（1500=0x05DC） | 0x103 | `01 20 DC 05` |
| 設定を保存 | 0x103 | `01 50 07` |
| 全局 STOP | 0x000 | `03` |

STATE と FAULT のように隣り合うレジスタは 1 回の読み出しにまとめる（1 回最大 6 B）。0x00〜0x5F 全体を読むときは 6 B ずつ約 16 回（`nemadbg regs` の方式）。

---

## 9. ブートローダ（ファームウェア更新）への移行

ENTER_BOOTLOADER（CMD 0x50）で本機を STM32 の ROM ブートローダ（USB DFU、VID 0x0483 / PID 0xDF11）に移行させる。

```
要求 BODY: [0x50][CTR][0x42][0x4F][0x4F][0x54]    （KEY=0x544F4F42 の LE バイト列）
```

- 条件: CONNECT 済み、KEY 一致、**STATE が ENABLED 以外**（NO_VM・DISABLED・FAULT は可）。違反は KEY 不一致 0x32、ENABLED 中 0x33、未接続 0x35。
- 手順: ACK 応答を返し、送信完了後に出力を安全状態にしてリセット→ROM ブートローダが起動。**CAN からは受け付けない**。
- PC 側: ACK を受けたら COM ポートを閉じる。数秒以内に USB が再列挙され、VID 0483 / PID DF11 の DFU デバイスとして現れる。書き込みは ST の `STM32_Programmer_CLI`（`-c port=USB1`）などで行う。通常ファームには戻る方法として、書き込み後にリセット（電源再投入）する。
- この要求は**再送しない**。応答がなくてもリセットが走っている可能性があるため、DFU デバイスの出現を確認する。
- 本アプリで DFU 書き込みまで実装する必要がない場合は、ENTER_BOOTLOADER を使わなくてよい。

---

## 10. クライアント実装ガイド

### 10.1 推奨アーキテクチャ

層を次のように分け、下位ほど I/O から独立させる。

| 層 | 責務 | 参照 |
|----|------|------|
| frame | `crc16`、`encode_frame`、`FrameParser`（バイト列⇄BODY）。純粋関数・I/O なし | `nemadbg/protocol.py` |
| link | ポート入出力、要求→応答（CTR 照合・タイムアウト・再送）、イベント分離 | `nemadbg/link.py` |
| device | CAN_REQUEST・レジスタ読み書きの API（`read_reg`、`write_reg`、`command`）、型変換、CMD_RESULT の判定 | `nemadbg/registers.py` |
| app | UI・シナリオ | — |

- link は 1 要求ずつ直列に処理する（スレッドから呼ぶなら排他制御）。
- 受信は専用スレッド（または非同期 I/O）で行い、応答（0xFF）は待機中の要求へ、イベント（0xFE）はコールバックまたはキューへ配送する。

### 10.2 エラー処理方針

| 事象 | 対応 |
|------|------|
| 応答タイムアウト | 読み出しなら最大 3 回再送。書き込み系は再送せず上位へ失敗を返し、必要なら読み戻して確認 |
| ERR=0x35 | CONNECT からやり直す |
| ERR=0x31/0x32 | 実装バグ。再送しない |
| CMD_RESULT=1（REJECTED） | 状態を読み直して前提条件を満たしてから再試行（例: ENABLE 前に移動指令を出した） |
| CMD_RESULT=2（INVALID） | 値の範囲違反。再送しない |
| COM ポート消失 | 接続断として通知し、再列挙後に CONNECT からやり直す |
| STATE=FAULT | FAULT レジスタを表示。原因を取り除いたのち CLEAR_FAULT |

### 10.3 典型的な運転シーケンス

```
CONNECT
write COMM_TIMEOUT 等の設定（必要なら）→ 保存
loop: read STATE/STATUS ...                    # USB の読み出しは COMM_TIMEOUT の生存通知にならない
STATE==DISABLED なら COMMAND=ENABLE → STATE==ENABLED を確認
MOVE_ABS / MOVE_REL / RUN_SPEED を書く
STATUS.bit7(目標到達) または POSITION で完了を判定
COMMAND=STOP または DISABLE
DISCONNECT
```

移動指令は書き込みの応答（CMD_RESULT）が OK でも完了ではない。完了は STATUS の bit0（MOVING）が落ちる／bit7（目標到達）が立つことで判断する。

### 10.4 推奨ポーリング周期

状態読み出しは 20〜100 ms 周期を推奨。COMM_TIMEOUT の生存通知は CAN 側の要求だけが担い、USB の CAN_REQUEST は計時を延ばさない。応答を読み続けること（読まないと本機の送信が詰まり、50 ms 後に応答を破棄し要求も無視される）。

---

## 11. 受け入れ試験（クライアント実装の確認項目）

実機がなくても、`firmware/tools/tests/fakedevice.py`（擬似デバイス、BOARD_ID=3）で frame・link 層を試験できる。

| # | 項目 | 期待結果 |
|---|------|----------|
| 1 | `crc16(b"123456789")` | 0x29B1 |
| 2 | 5.1 章の各フレームを `encode_frame` で生成 | 表と完全一致 |
| 3 | 1 バイトずつ・複数フレーム連結・途中切れのストリームをパース | すべて正しく BODY を復元 |
| 4 | CRC を 1 ビット壊したフレームの後に正常フレーム | 壊れた方は捨て、正常な方を取り出せる |
| 5 | LEN=1 や 251 のフレーム | 捨てて再同期できる |
| 6 | CONNECT | DEVICE_ID=0xA3、MAX_LEN=250 |
| 7 | 未 CONNECT で CAN_REQUEST | ERR=0x35 |
| 8 | 未定義 CMD（CONNECT 後） | ERR=0x30 |
| 9 | 応答なし（擬似デバイスが無応答） | 読み出しは 3 回再送後に失敗。書き込みは再送なし |
| 10 | 応答の前にイベント(0xFE)が割り込む | イベントを別配送し、応答待ちを継続 |
| 11 | CTR 不一致の応答 | 捨てて待機を続ける |
| 12 | 実機: ENABLE→MOVE_REL→STOP→DISABLE | STATE と POSITION が 8章どおりに変化 |
| 13 | 実機: 電源 OFF（NO_VM）で ENABLE | CMD_RESULT=REJECTED |
| 14 | 実機: ケーブル抜去→再接続 | 再列挙後に CONNECT からやり直せる |

---

## 12. 既知の仕様上の注意・未規定事項

1. **GET_LINE_CODING**: 本機は応答内容を埋めないため、ホストが読んだボーレート等は信用できない。
2. **ERR 0x32 の用途**: SIZE・INDEX・CAN_ID・DLC・KEY に加え、SYM_FIND/SYM_UPLOAD の名前長（0 または 31 超）・OFFSET にも使われる。
3. **給電表示**: USB 設定ディスクリプタは self-powered（0xC0）。実際の給電構成は基板（電源 OR 回路）に依存する。
4. **同時操作**: CAN と USB から同時に操作した場合の優先順位は未規定（`requirements.md` U-9）。同時には使わないこと。
5. **イベントは取りこぼしうる**（保留 1 個）。状態の正は常にレジスタの読み出しとする。
6. 本書の USB 層の値（VID/PID 等）は暫定（`requirements.md` U-8）。変更される場合は本書も更新する。

---

## 付録 A. 関連ファイル

| ファイル | 内容 |
|----------|------|
| `firmware/docs/requirements.md` 3.8 / 3.9 | 要求仕様（FR-COM、FR-USB） |
| `firmware/docs/usb_symbols.md` | 名前表（SYM_*）の登録一覧（104 件） |
| `firmware/App/Inc/usb_proto.h` | CMD・ERR・定数の定義 |
| `firmware/App/Src/usb_proto.c` | フレーム処理の実装 |
| `firmware/App/Src/comm.c` / `app.c` | CAN 要求処理・レジスタの実装 |
| `firmware/tools/nemadbg/` | Python 参照実装（`python -m nemadbg`、`tools/README.md` 参照） |
| `firmware/tests/host/test_usb.c` | ファーム側のプロトコル試験 |

## 付録 B. Python での最小利用例（pyserial）

```python
import serial, struct
from nemadbg.protocol import encode_frame, FrameParser   # 参照実装を流用する場合

ser = serial.Serial("COM5", 115200, timeout=0.05)
parser = FrameParser()
ctr = 0

def request(cmd, param=b"", timeout=0.2):
    global ctr
    ctr = (ctr + 1) & 0xFF
    ser.write(encode_frame(bytes([cmd, ctr]) + param))
    # 応答(0xFF, CTR一致)が来るまで読み続ける。0xFE はイベントとして別処理
    ...

def can_request(can_id, data):
    return request(0x40, struct.pack("<HB", can_id, len(data)) + data)

request(0x01)                              # CONNECT
can_request(0x100 + 3, bytes([0x01, 0x50, 0x01]))   # ENABLE（基板ID 3）
```
