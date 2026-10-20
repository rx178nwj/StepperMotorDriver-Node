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
python -m nemadbg [--port COMx] [--elf PATH] [--timeout MS] <サブコマンド> ...
```

- `--port` 省略時は VID 0483 / PID 5740 のポートを自動検出する（複数あれば一覧を表示してエラー、無ければエラー）。
- `--timeout` は応答待ち [ms]（既定 200）。タイムアウトしたらフレームごとに最大 3 回再送する。
- 共通オプションはサブコマンドの前後どちらにも書ける。
- どのサブコマンドも終了時に DISCONNECT を送る。

| サブコマンド | 内容 |
|---|---|
| `info` | CONNECT / GET_VERSION / EXCHANGE_ID を実行して表示。`--elf` 指定時は build-id を照合 |
| `syms` | 名前表（INDEX、名前、アドレス、TYPE、NUM）を全列挙 |
| `get NAME...` | 名前指定で読み出し（TYPE に従って整形） |
| `read ADDR SIZE` | アドレス指定で読み、hex ダンプ（240 B ずつ分割） |
| `read-var SYMBOL...` | ELF のシンボル表でアドレス・サイズを解決して読む（`--elf` 必須、build-id 不一致なら読まない） |
| `can-req CAN_ID [DATA_HEX]` | CAN_REQUEST を実行し、基板が返す CAN フレームを表示 |
| `regs [--start 0x00] [--len N]` | CAN レジスタ（3.8 節）を読んで名前付きで表示 |
| `watch NAME... --interval SEC [--count N]` | 周期読み出し（Ctrl-C で終了） |
| `events [--duration SEC]` | イベント（異常通知など）を表示し続ける |

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

## 終了コード

| コード | 意味 |
|---|---|
| 0 | 正常 |
| 1 | エラー（基板が ERR≠0 を返した、無応答、ポート異常、引数の誤り、ELF 異常） |
| 2 | build-id 不一致（`info --elf`、`read-var`） |

引数の誤り（argparse）も 2 ではなく 1 で終了する。

## テスト

ハードウェア不要。擬似デバイス（`tests/fakedevice.py`）で動かす。

```powershell
python -m unittest discover -s tests
```
