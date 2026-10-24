"""起動処理。`python -m nemagui [--port COMx] [--sim] [--timeout MS]`。"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from nemadbg.link import DEFAULT_TIMEOUT_S

from .ui.main_window import AppConfig


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="nemagui", description="NEMA23 ステッピングモータドライバのデバッグ GUI")
    ap.add_argument("--port", help="COM ポート名（省略時は VID 0483 / PID 5740 のポートから選ぶ）")
    ap.add_argument("--sim", action="store_true", help="実機の代わりに擬似デバイスを使う")
    ap.add_argument("--timeout", type=int, default=int(DEFAULT_TIMEOUT_S * 1000), help="応答待ち [ms]")
    return ap


def config_from_args(args: argparse.Namespace) -> AppConfig:
    cfg = AppConfig(port=args.port, timeout_ms=args.timeout)
    if args.sim:
        from nemadbg.sim import SimSerial   # 実機を使わないときだけ読み込む

        cfg.sim = SimSerial(warmup_ms=200)
        cfg.port = None
    return cfg


def setup_font(app) -> None:   # type: ignore[no-untyped-def]
    """日本語が化けないよう、Windows の標準フォントを後ろに並べる。"""
    f = app.font()
    f.setFamilies([f.family(), "Yu Gothic UI", "Meiryo UI", "MS UI Gothic"])
    app.setFont(f)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 が必要です: pip install -r requirements-gui.txt", file=sys.stderr)
        return 1
    from .ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setOrganizationName("nemadbg")
    app.setApplicationName("nemagui")
    setup_font(app)
    win = MainWindow(config_from_args(args))
    win.show()
    return app.exec()
