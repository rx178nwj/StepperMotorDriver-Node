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
    ("SENSOR_STATUS", "MH"): ORANGE,
    ("SENSOR_STATUS", "ML"): ORANGE,
    ("SENSOR_STATUS", "NO_RESPONSE"): RED,
    ("AS_STATUS", "MH"): ORANGE,       # AS5600 STATUS（磁石が強すぎる・弱すぎる）
    ("AS_STATUS", "ML"): ORANGE,
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
OPS_POLL = "ops"            # 操作タブ表示中だけ。概要に無い J10_MODE を読む
OPS_PERIOD_S = 0.1
OPS_POLL_KEYS = ["reg.J10_MODE"]
SETTINGS_POLL = "settings"  # 設定タブ表示中だけ。設定値と CONFIG_STATUS を読む
SETTINGS_PERIOD_S = 1.0


TAB_OPS = "ops"
TAB_SETTINGS = "settings"
TAB_SYMS = "syms"
TAB_GRAPH = "graph"


@dataclass(frozen=True)
class TabSpec:
    label: str
    group: Optional[str]      # 項目表のグループ。None なら専用の画面かプレースホルダ
    note: str = ""            # プレースホルダの説明
    kind: str = ""            # 専用の画面の種類（TAB_OPS / TAB_SETTINGS / TAB_SYMS / TAB_GRAPH）


def tab_specs() -> List[TabSpec]:
    """表示順: 操作・設定・各内部グループ・名前表・グラフ。"""
    labels = {"motion": "モーション", "state": "状態遷移", "monitor": "監視", "sensor": "センサ",
              "j10": "J10・原点", "comm": "通信統計", "diag": "診断", "other": "その他"}
    out = [TabSpec("操作", None, kind=TAB_OPS), TabSpec("設定", None, kind=TAB_SETTINGS)]
    for g in itemsmod.GROUPS:
        if g in labels:
            out.append(TabSpec(labels[g], g))
    out.append(TabSpec("名前表", None, kind=TAB_SYMS))
    out.append(TabSpec("グラフ", None, kind=TAB_GRAPH))
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


# 書き込みログの値に付ける単位（レジスタ名 -> 単位）。項目を持たないレジスタ（MOVE_REL など）は付けない
_REG_UNITS: Dict[str, str] = {i.reg.name: i.unit for i in itemsmod.build_items() if i.reg is not None and i.unit}


def _reg_name(target: Any) -> str:
    """WriteReg.target（"reg.RUN_CURRENT"・"MOVE_REL"・アドレス）をレジスタ名にする。"""
    if isinstance(target, int):
        for r in regs.REGISTERS:
            if r.addr == target:
                return r.name
        return f"0x{target:02X}"
    return str(target)[4:] if str(target).startswith("reg.") else str(target)


def request_label(req: Any) -> str:
    if isinstance(req, wk.Command):
        return f"COMMAND {_code_name(req.code)}"
    if isinstance(req, wk.Broadcast):
        return f"ブロードキャスト {_code_name(req.code)}"
    if isinstance(req, wk.WriteReg):
        name = _reg_name(req.target)
        unit = _REG_UNITS.get(name, "")
        return f"書き込み {name} = {req.value}" + (f" {unit}" if unit else "")
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


# ---------------------------------------------------------------- 操作の受付条件

# 操作の名前（OpsContext から op_availability で可否を引く）
OP_ENABLE = "ENABLE"
OP_DISABLE = "DISABLE"
OP_CLEAR_FAULT = "CLEAR_FAULT"
OP_MOVE_ABS = "MOVE_ABS"
OP_MOVE_REL = "MOVE_REL"
OP_RUN_SPEED = "RUN_SPEED"              # 速度 != 0
OP_RUN_SPEED_ZERO = "RUN_SPEED_ZERO"    # 速度 0（減速停止）
OP_SET_POSITION = "SET_POSITION"
OP_SYNC_POSITION = "SYNC_POSITION"
OP_RESET_ELEC_ANGLE = "RESET_ELEC_ANGLE"
OP_HOME_NEG = "HOME_NEG"
OP_HOME_POS = "HOME_POS"
ALL_OPS = (OP_ENABLE, OP_DISABLE, OP_CLEAR_FAULT, OP_MOVE_ABS, OP_MOVE_REL, OP_RUN_SPEED, OP_RUN_SPEED_ZERO,
           OP_SET_POSITION, OP_SYNC_POSITION, OP_RESET_ELEC_ANGLE, OP_HOME_NEG, OP_HOME_POS)

STATUS_MOVING = 1 << 0
STATUS_TARGET_REACHED = 1 << 7
INPUT_HOMING = 1 << 2
INPUT_HOMED = 1 << 3
INPUT_HOME_FAILED = 1 << 4
INPUT_LIMIT_STOP = 1 << 5
STATE_DISABLED, STATE_ENABLED, STATE_FAULT = 1, 2, 3
J10_MODE_HOME = 2


@dataclass(frozen=True)
class OpsContext:
    """操作の可否を決める現在値。取得できていない値は None。"""

    connected: bool
    state: Optional[int] = None
    status: Optional[int] = None
    input_status: Optional[int] = None
    j10_mode: Optional[int] = None

    @property
    def moving(self) -> Optional[bool]:
        return None if self.status is None else bool(self.status & STATUS_MOVING)

    @property
    def homing(self) -> Optional[bool]:
        return None if self.input_status is None else bool(self.input_status & INPUT_HOMING)


@dataclass(frozen=True)
class OpState:
    enabled: bool
    reason: str = ""          # 無効のときの理由（ツールチップ用）


def ops_context(model: Any, connected: bool) -> OpsContext:
    """Model（value(key) を持つもの）から OpsContext を作る。"""
    def val(key: str) -> Optional[int]:
        v = model.value(key)
        return None if v is None else int(v)

    return OpsContext(connected, val("reg.STATE"), val("reg.STATUS"), val("reg.INPUT_STATUS"), val("reg.J10_MODE"))


def _need_enabled(ctx: OpsContext) -> str:
    if ctx.state is None:
        return "状態（STATE）を取得できていません"
    if ctx.state != STATE_ENABLED:
        return f"ENABLED のときだけ実行できます（現在 {state_name(ctx.state)}）"
    return ""


def _need_stopped(ctx: OpsContext) -> str:
    if ctx.moving is None:
        return "ステータス（STATUS）を取得できていません"
    if ctx.moving:
        return "動作中は実行できません（停止してから）"
    return ""


def _need_not_homing(ctx: OpsContext) -> str:
    if ctx.homing is None:
        return "入力状態（INPUT_STATUS）を取得できていません"
    if ctx.homing:
        return "原点復帰中は実行できません"
    return ""


def op_availability(ctx: OpsContext) -> Dict[str, OpState]:
    """操作ごとの可否と、無効のときの理由。usb_interface.md 8.5 の受付条件に基づく。

    あくまで GUI 側の目安であり、最終判断は基板が行う（合わなければ REJECTED が返りログに出る）。
    移動方向のリミット作動による拒否は基板に任せ、ここでは無効にしない。
    """
    if not ctx.connected:
        return {op: OpState(False, "未接続です") for op in ALL_OPS}

    def first(*reasons: str) -> OpState:
        for r in reasons:
            if r:
                return OpState(False, r)
        return OpState(True)

    def only_state(want: int, label: str) -> OpState:
        if ctx.state is None:
            return OpState(False, "状態（STATE）を取得できていません")
        if ctx.state != want:
            return OpState(False, f"{label} のときだけ実行できます（現在 {state_name(ctx.state)}）")
        return OpState(True)

    motion = first(_need_enabled(ctx), _need_not_homing(ctx))
    standing = first(_need_stopped(ctx), _need_not_homing(ctx))
    home_reasons = [_need_enabled(ctx), _need_stopped(ctx)]
    if ctx.j10_mode is None:
        home_reasons.append("J10_MODE を取得できていません")
    elif ctx.j10_mode != J10_MODE_HOME:
        home_reasons.append(f"J10_MODE が HOME（2）のときだけ実行できます（現在 {ctx.j10_mode}）")
    home = first(*home_reasons)
    return {
        OP_ENABLE: only_state(STATE_DISABLED, "DISABLED"),
        OP_DISABLE: only_state(STATE_ENABLED, "ENABLED"),
        OP_CLEAR_FAULT: only_state(STATE_FAULT, "FAULT"),
        OP_MOVE_ABS: motion,
        OP_MOVE_REL: motion,
        OP_RUN_SPEED: motion,
        OP_RUN_SPEED_ZERO: first(_need_enabled(ctx)),
        OP_SET_POSITION: standing,
        OP_SYNC_POSITION: standing,
        OP_RESET_ELEC_ANGLE: first(_need_enabled(ctx), _need_stopped(ctx), _need_not_homing(ctx)),
        OP_HOME_NEG: home,
        OP_HOME_POS: home,
    }


def settings_cmd_availability(connected: bool, status: Optional[int]) -> OpState:
    """SAVE / LOAD / FACTORY_SETTINGS の可否（停止中のみ）。GUI 側の目安。"""
    if not connected:
        return OpState(False, "未接続です")
    if status is None:
        return OpState(False, "ステータス（STATUS）を取得できていません")
    if status & STATUS_MOVING:
        return OpState(False, "動作中は実行できません（停止中のみ）")
    return OpState(True)


# ---------------------------------------------------------------- 設定タブの表示

def range_text(item: Item) -> str:
    """設定の範囲の表示。離散値なら列挙、ビットなら 0〜最大、それ以外は 最小〜最大。"""
    if item.choices:
        return " / ".join(str(c) for c in item.choices)
    if item.enum:
        return " / ".join(f"{k}={v}" for k, v in item.enum.items())
    if item.min is None or item.max is None:
        return ""
    if item.bits:
        return f"0x{int(item.min):02X}〜0x{int(item.max):02X}"
    return f"{int(item.min)}〜{int(item.max)}"


def write_result_text(r: wk.WriteResult) -> str:
    """設定の行ごとに出す書き込み結果。"""
    if r.unknown:
        return f"不明（{r.error}）"
    if r.error:
        return f"失敗（{r.error}）"
    if r.result == 0:
        return "OK"
    return r.result_name or "不明"
