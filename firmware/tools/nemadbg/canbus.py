"""python-can の小さなラッパ（selftest の E グループ用）。python-can は open_bus() を呼んだときだけ import する。

標準 ID・クラシック CAN のみ。selftest.py は CanBus と同じメソッド（request / drain / close）を持つ偽物を差し替えて試験できる。
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

DEFAULT_INTERFACE = "gs_usb"     # DSD TECH SH-C30L は candleLight ファームなら gs_usb、slcan ファームなら slcan
DEFAULT_BITRATE = 500000         # docs/can_interface.md 2 章


class CanUnavailable(Exception):
    """python-can が無い、またはアダプタを開けない。"""


class CanBus:
    """bus は python-can の Bus（send / recv / shutdown）。"""

    def __init__(self, bus: Any) -> None:
        self._bus = bus

    def drain(self) -> None:
        """受信済みで未処理のフレームを捨てる。"""
        while self._bus.recv(0) is not None:
            pass

    def request(self, can_id: int, data: bytes, rsp_id: int, timeout: float = 0.05) -> Optional[bytes]:
        """標準 ID のデータフレームを送り、CAN_ID が rsp_id のフレームを timeout 秒まで待つ。無ければ None。
        他の ID（異常通知 0x080+ID など）は読み捨てる。"""
        import can   # open_bus() 済みなら import できる

        self.drain()
        self._bus.send(can.Message(arbitration_id=can_id, data=bytes(data), is_extended_id=False))
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return None
            m = self._bus.recv(left)
            if m is None:
                return None
            if m.arbitration_id == rsp_id and not m.is_extended_id:
                return bytes(m.data)

    def close(self) -> None:
        try:
            self._bus.shutdown()
        except Exception:
            pass


def _add_libusb_to_path() -> None:
    """Windows の gs_usb 用。python-can(gs-usb) は libusb-1.0.dll のパスを渡さないため、
    libusb-package 同梱の DLL のあるフォルダを PATH の先頭へ足す。無ければ何もしない（Linux はシステムの libusb）。"""
    try:
        import libusb_package   # 遅延 import
        lib = libusb_package.get_library_path()
    except Exception:
        return
    if not lib:
        return
    d = os.path.dirname(str(lib))
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if d in parts:
        return
    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def open_bus(interface: str = DEFAULT_INTERFACE, channel: Optional[str] = None,
             bitrate: int = DEFAULT_BITRATE) -> CanBus:
    """アダプタを開く。python-can が無い・開けないときは CanUnavailable。

    gs_usb: channel は省略可（数字ならアダプタの index、既定 0）。slcan: channel（COMx）が必須。
    """
    try:
        import can   # 遅延 import（selftest の E グループだけが使う）
    except ImportError:
        raise CanUnavailable("python-can が未インストールです（pip install -r requirements-selftest.txt）")
    kwargs: dict = {"interface": interface, "bitrate": bitrate}
    if interface == "gs_usb":
        idx = int(channel) if channel is not None and str(channel).isdigit() else 0
        kwargs.update(channel=channel if channel is not None else "gs_usb", index=idx)
    elif channel is None:
        raise CanUnavailable(f"{interface} には --can-channel（例: COM5）が必要です")
    else:
        kwargs["channel"] = channel
    if interface == "gs_usb":
        _add_libusb_to_path()
    try:
        bus = can.Bus(**kwargs)
    except Exception as e:
        raise CanUnavailable(f"CAN アダプタ {interface} を開けません: {e}")
    return CanBus(bus)
