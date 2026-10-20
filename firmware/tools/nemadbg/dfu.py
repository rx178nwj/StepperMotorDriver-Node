"""`dfu` サブコマンドの補助（STM32_Programmer_CLI による DFU デバイスの検出）。

依存を pyserial・pyelftools のままにするため、検出は外部の STM32_Programmer_CLI が PATH にあるときだけ行う
（requirements.md FR-USB-62）。呼び出しは DfuTools に差し替えられる（テストでは偽物を使う）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional

CLI_NAME = "STM32_Programmer_CLI"
DETECT_TIMEOUT_S = 5.0     # DFU デバイスが現れるまで待つ上限
POLL_INTERVAL_S = 0.5      # `-l usb` の繰り返し間隔（CLI 1 回の実行にも数百 ms かかる）
RUN_TIMEOUT_S = 10.0       # CLI 1 回の実行の上限

# `STM32_Programmer_CLI -l usb` の出力（v2.23.0 で確認）。DFU デバイスが無いとき:
#     =====  DFU Interface   =====
#
#     No STM32 device in DFU mode connected
# DFU デバイスがあるときは、ST の資料・フォーラムで知られている次の形式で列挙される（実機では未確認）:
#     =====  DFU Interface   =====
#
#     Total number of available STM32 device in DFU mode: 1
#
#       Device Index           : USB1
#       USB Bus Number         : 001
#       USB Address Number     : 002
#       Product ID             : DFU in FS Mode @Device ID /0x435, @Revision ID /0x1001
#       ...
# 出力に VID/PID は出ないが、この一覧に載るのは DFU モードの STM32（ROM ブートローダは 0483:DF11）。
# 判定は、「Device Index : USBn」の行があるか、「Total number ... DFU mode: N」で N>=1 のとき。
# 「No STM32 device in DFU mode」は肯定の根拠にしない（Device Index が無ければ未検出）
_DEVICE_RE = re.compile(r"Device Index\s*:\s*USB\d+", re.IGNORECASE)
_TOTAL_RE = re.compile(r"Total number of available STM32 device in DFU mode:\s*([0-9]+)", re.IGNORECASE)


def is_dfu_listed(output: str) -> bool:
    """`-l usb` の出力に DFU デバイスが含まれるか。"""
    if _DEVICE_RE.search(output):
        return True
    m = _TOTAL_RE.search(output)
    return m is not None and int(m.group(1)) >= 1


def find_programmer() -> Optional[str]:
    """PATH から STM32_Programmer_CLI を探す（Windows では .exe を補う）。"""
    return shutil.which(CLI_NAME)


def run_list_usb(cli: str) -> str:
    """`STM32_Programmer_CLI -l usb` を実行して出力（標準出力＋標準エラー）を返す。失敗したら空文字。"""
    try:
        r = subprocess.run([cli, "-l", "usb"], capture_output=True, timeout=RUN_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (r.stdout + b"\n" + r.stderr).decode("utf-8", "replace")


@dataclass
class DfuTools:
    which: Callable[[], Optional[str]] = find_programmer
    run: Callable[[str], str] = run_list_usb
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    timeout_s: float = DETECT_TIMEOUT_S
    interval_s: float = POLL_INTERVAL_S

    def wait_for_dfu(self, cli: str) -> bool:
        """最大 timeout_s の間 `-l usb` を繰り返し、DFU デバイスが現れたら True。"""
        deadline = self.clock() + self.timeout_s
        while True:
            if is_dfu_listed(self.run(cli)):
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(self.interval_s)
