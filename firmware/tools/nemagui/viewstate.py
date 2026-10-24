"""画面表示の決定（色・文言・タブ構成）。Qt には依存せず、単体で試験できる。

Worker の out_q から取り出したメッセージを、ログ行・状態表示・統計文字列などに変換する。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from nemadbg import registers as regs

from . import items as itemsmod
from . import worker as wk
from .items import Item

# ---------------------------------------------------------------- 色

GRAY = "#9e9e9e"
YELLOW = "#f2c500"
GREEN = "#2e9e44"
RED = "#d32f2f"
ORANGE = "#e67e00"
BLUE = "#1976d2"
UNKNOWN_GRAY = "#cfcfcf"

STATE_COLORS: Dict[int, str] = {0: GRAY, 1: YELLOW, 2: GREEN, 3: RED}


def state_color(state: Optional[int]) -> str:
    """STATE 表示の背景色。NO_VM 灰、DISABLED 黄、ENABLED 緑、FAULT 赤。未取得・不明は薄い灰。"""
    if state is None:
        return UNKNOWN_GRAY
    return STATE_COLORS.get(state, UNKNOWN_GRAY)


def state_text_color(state: Optional[int]) -> str:
    """背景に対して読める文字色。"""
    return "#000000" if state_color(state) in (YELLOW, UNKNOWN_GRAY) else "#ffffff"


def state_name(state: Optional[int]) -> str:
    if state is None:
        return "—"
    return regs.STATE_NAMES.get(state, f"不明({state})")


# ビットランプ: 立っているときの色。既定は緑、異常を表すビットは赤・橙
_LAMP_COLORS: Dict[Tuple[str, str], str] = {
    ("FAULT", "*"): RED,
    ("STATUS", "OVERTEMP_WARN"): ORANGE,
    ("CONFIG_STATUS", "UNSAVED"): ORANGE,
    ("CONFIG_STATUS", "CORRECTED"): ORANGE,
    ("CONFIG_STATUS", "SAVE_FAILED"): RED,
    ("INPUT_STATUS", "HOME_FAILED"): RED,
    ("INPUT_STATUS", "LIMIT_STOP"): ORANGE,
}


def lamp_color(reg_name: str, bit_name: str) -> str:
    return _LAMP_COLORS.get((reg_name, bit_name)) or _LAMP_COLORS.get((reg_name, "*")) or GREEN


def lamp_states(item: Item, value: Optional[int]) -> List[Tuple[str, bool]]:
    """(ビット名, 立っているか) の並び。値が無ければすべて False。"""
    v = int(value) if value is not None else 0
    return [(name, bool(v & (1 << b))) for b, name in item.bits]


# ---------------------------------------------------------------- 接続状態

CONN_DISCONNECTED = "disconnected"
CONN_CONNECTED = "connected"
CONN_WAITING = "waiting"

CONN_TEXT = {CONN_DISCONNECTED: "切断", CONN_CONNECTED: "接続中", CONN_WAITING: "再接続待ち"}
CONN_COLORS = {CONN_DISCONNECTED: GRAY, CONN_CONNECTED: GREEN, CONN_WAITING: ORANGE}


def conn_kind(msg: Any, current: str = CONN_DISCONNECTED) -> str:
    """Connected / Disconnected から接続状態を決める。それ以外のメッセージでは current のまま。"""
    if isinstance(msg, wk.Connected):
        return CONN_CONNECTED
    if isinstance(msg, wk.Disconnected):
        return CONN_WAITING if msg.will_retry else CONN_DISCONNECTED
    return current


def device_info_text(msg: wk.Connected) -> str:
    i = msg.info
    bid = msg.build_id.hex()[:8] if msg.build_id else "なし"
    return (f"FW {i.fw_major}.{i.fw_minor}   BOARD_ID {i.board_id}   "
            f"プロトコル {i.proto_major}.{i.proto_minor}   build-id {bid}")


# ---------------------------------------------------------------- 値の表示

def format_vm(mv: Optional[float]) -> str:
    """VM を V 表示にする（小数 2 桁）。"""
    return "—" if mv is None else f"{mv / 1000.0:.2f} V"


def plain_text(item: Item, value: Any) -> str:
    """表の値セル用。format_value から末尾の単位を除く（単位は別の列に出す）。"""
    text = itemsmod.format_value(item, value)
    suffix = " " + item.unit
    if item.unit and not item.enum and not item.bits and item.ctype != "bytes" and text.endswith(suffix):
        return text[: -len(suffix)]
    return text


def counter_suffix(rate: Optional[float]) -> str:
    return "" if rate is None else f"  (+{rate:.0f}/s)"


# ---------------------------------------------------------------- タブ構成

# 周期の既定 [s]
POLL_PERIODS: Dict[str, float] = {
    "motion": 0.05, "j10": 0.05, "state": 0.1, "sensor": 0.1, "monitor": 0.2,
    "comm": 0.5, "diag": 0.5, "other": 0.5,
}
OVERVIEW_POLL = "overview"
OVERVIEW_PERIOD_S = 0.05
TAB_POLL = "tab"


@dataclass(frozen=True)
class TabSpec:
    label: str
    group: Optional[str]      # 項目表のグループ。None ならプレースホルダ
    note: str = ""            # プレースホルダの説明


def tab_specs() -> List[TabSpec]:
    """表示順: 操作・設定・各内部グループ・グラフ。"""
    labels = {"motion": "モーション", "state": "状態遷移", "monitor": "監視", "sensor": "センサ",
              "j10": "J10・原点", "comm": "通信統計", "diag": "診断", "other": "その他"}
    out = [TabSpec("操作", None, "操作タブは G3 で実装予定です"),
           TabSpec("設定", None, "設定タブは G3 で実装予定です")]
    for g in itemsmod.GROUPS:
        if g in labels:
            out.append(TabSpec(labels[g], g))
    out.append(TabSpec("グラフ", None, "グラフは G5 で実装予定です"))
    return out


def group_keys(items: List[Item], group: str) -> List[str]:
    return [i.key for i in items if i.group == group]


# ---------------------------------------------------------------- ログ

LEVEL_INFO = "info"
LEVEL_WARN = "warn"
LEVEL_ERROR = "error"
LEVEL_COLORS = {LEVEL_INFO: "#202020", LEVEL_WARN: ORANGE, LEVEL_ERROR: RED}


@dataclass(frozen=True)
class LogEntry:
    text: str
    level: str = LEVEL_INFO


def stamp(t: Optional[float] = None) -> str:
    return datetime.fromtimestamp(time.time() if t is None else t).strftime("%H:%M:%S.%f")[:-3]


def _fault_names(fault: int) -> str:
    names = [n for b, n in next(r for r in regs.REGISTERS if r.name == "FAULT").bits if fault & (1 << b)]
    return ", ".join(names) if names else "なし"


def describe_event(ev: wk.Event) -> LogEntry:
    """Event を解読する。FAULT 通知（CAN ID 0x080+BOARD_ID）は状態と要因のビット名を出す。"""
    if ev.state is not None and ev.fault is not None:
        return LogEntry(f"異常通知: 状態 {state_name(ev.state)}、要因 {_fault_names(ev.fault)} (0x{ev.fault:02X})",
                        LEVEL_ERROR)
    return LogEntry(ev.text or f"イベント 0x{ev.evt:02X}: {ev.data.hex(' ')}", LEVEL_WARN)


def _code_name(code: Any) -> str:
    if isinstance(code, str):
        return code.upper()
    try:
        return wk.Cmd(int(code)).name
    except ValueError:
        return str(code)


def request_label(req: Any) -> str:
    if isinstance(req, wk.Command):
        return f"COMMAND {_code_name(req.code)}"
    if isinstance(req, wk.Broadcast):
        return f"ブロードキャスト {_code_name(req.code)}"
    if isinstance(req, wk.WriteReg):
        return f"書き込み {req.target} = {req.value}"
    return type(req).__name__


def describe_write_result(r: wk.WriteResult) -> LogEntry:
    label = request_label(r.request)
    if r.unknown:
        return LogEntry(f"{label}: 実行されたか不明（{r.error}）", LEVEL_ERROR)
    if r.error:
        return LogEntry(f"{label}: 失敗（{r.error}）", LEVEL_ERROR)
    if r.result is None:
        return LogEntry(f"{label}: {r.result_name or '送信済み'}", LEVEL_INFO)
    if r.result == 0:
        return LogEntry(f"{label}: OK", LEVEL_INFO)
    meaning = regs.CMD_RESULT_NAMES.get(r.result, "")
    return LogEntry(f"{label}: {r.result_name}（{meaning}）" if meaning else f"{label}: {r.result_name}", LEVEL_WARN)


def log_entries(msg: Any) -> List[LogEntry]:
    """out_q のメッセージからログ行を作る。ログに出さない種類（Values・Stats など）は空。"""
    if isinstance(msg, wk.Connected):
        return [LogEntry("接続しました: " + device_info_text(msg))]
    if isinstance(msg, wk.Disconnected):
        tail = "（再接続を待っています）" if msg.will_retry else ""
        return [LogEntry(f"切断: {msg.reason}{tail}", LEVEL_WARN)]
    if isinstance(msg, wk.Event):
        return [describe_event(msg)]
    if isinstance(msg, wk.WriteResult):
        return [describe_write_result(msg)]
    if isinstance(msg, wk.Notice):
        return [LogEntry("注意: " + msg.text, LEVEL_WARN)]
    return []


# ---------------------------------------------------------------- 統計

def format_stats(s: wk.Stats) -> str:
    ov = s.poll_periods_s.get(OVERVIEW_POLL)
    ovt = f"{ov * 1000:.0f} ms" if ov else "—"
    return (f"要求 {s.requests}   タイムアウト {s.timeouts}   再送 {s.retransmits}   エラー {s.errors}   "
            f"往復 平均 {s.rtt_avg_ms:.1f} / 最大 {s.rtt_max_ms:.1f} ms   概要の周期 {ovt}")
