"""RIGOL DS1054Z を pyvisa で操作する小さなラッパ（selftest の D グループ用）。

SCPI は docs/oscilloscope_ds1054z.md に従う。pyvisa は open_scope() を呼んだときだけ import する
（未インストールなら ScopeUnavailable）。selftest.py は Scope と同じメソッドを持つ偽物を差し替えて試験できる。

チャンネル割り当て: CH1=DRV_VREF、CH2=DRV_CLK、CH3=DRV_DIR、CH4 は使わない。
プローブ倍率（:CHANn:PROB）は触らない（プローブ本体のスイッチに合わせて利用者が設定しておく）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

DEFAULT_VISA = "USB0::0x1AB1::0x04CE::DS1ZA221001740::INSTR"
INVALID_MEAS = 9.0e37        # :MEAS:ITEM? が返す「測定できない」（9.9E37）
RAW_CHUNK = 250000           # BYTE 形式で 1 回に読める最大点数
MEM_DEPTH = "12M"            # 単発取り込みのメモリ深さ（CH2 だけ表示するので 12M が使える）
TIMEBASES = [1, 2, 5]        # 1-2-5 の並び


class ScopeUnavailable(Exception):
    """pyvisa が無い、または機器に接続できない。"""


class ScopeError(Exception):
    """機器とのやり取りの失敗（タイムアウト・取り込み不能など）。"""


@dataclass
class Waveform:
    """RAW 取り込みの 1 チャンネル分。raw は 8bit 値、電圧 = (raw - yorg - yref) * yinc、時刻 = xorg + i * xinc。"""

    raw: bytes
    xorg: float
    xinc: float
    yinc: float
    yorg: float = 0.0
    yref: float = 0.0

    @staticmethod
    def from_volts(volts: Sequence[float], xorg: float, xinc: float, yinc: float = 0.02) -> "Waveform":
        """電圧列から作る（偽オシロ用）。0 以上の電圧だけを想定。"""
        raw = bytes(max(0, min(255, int(round(v / yinc)))) for v in volts)
        return Waveform(raw, xorg, xinc, yinc)

    def volts(self, i: int) -> float:
        return (self.raw[i] - self.yorg - self.yref) * self.yinc

    def time_of(self, i: int) -> float:
        return self.xorg + i * self.xinc

    @property
    def duration(self) -> float:
        return len(self.raw) * self.xinc


def rising_edges(wave: Waveform, lo_v: float = 0.8, hi_v: float = 2.0) -> List[int]:
    """ヒステリシス付き（lo_v 以下に落ちてから hi_v 以上に上がったら 1 回）で立ち上がりのサンプル番号を返す。"""
    if wave.yinc <= 0:
        raise ScopeError("yinc が不正です")
    lo = lo_v / wave.yinc + wave.yorg + wave.yref
    hi = hi_v / wave.yinc + wave.yorg + wave.yref
    table = bytearray(256)
    for x in range(256):
        table[x] = ord("L") if x <= lo else (ord("H") if x >= hi else ord("M"))
    cls = wave.raw.translate(bytes(table))
    # L のあとに M が続き、H に達したところを 1 回と数える（途中で L に戻れば数え直す）
    return [m.end() - 1 for m in re.finditer(rb"LM*H", cls)]


def pick_timebase(span_s: float) -> float:
    """画面全体（12 div）が span_s 以上になる最小の 1-2-5 の s/div。"""
    need = span_s / 12.0
    exp = -9
    while True:
        for m in TIMEBASES:
            v = m * 10.0 ** exp
            if v >= need * (1 - 1e-9):
                return v
        exp += 1


class Scope:
    """DS1054Z。res は pyvisa の MessageBasedResource（write / query / read_raw / close）。"""

    def __init__(self, res: Any) -> None:
        self._s = res
        self._tb = 1e-3

    # ---- 基本 ----

    def close(self) -> None:
        try:
            self._s.close()
        except Exception:
            pass

    def idn(self) -> str:
        return str(self._s.query("*IDN?")).strip()

    def _w(self, cmd: str) -> None:
        self._s.write(cmd)

    def _q(self, cmd: str) -> str:
        return str(self._s.query(cmd)).strip()

    # ---- 設定 ----

    def setup_live(self) -> None:
        """連続表示に戻す。CH1=0.5 V/div（VREF）、CH2/CH3=1 V/div（3.3 V ロジック）、CH4 は消す。1 ms/div、AUTO。"""
        self._w(":STOP")
        self._w(":ACQ:TYPE NORM")
        for ch, scale, offs in ((1, 0.5, -1.5), (2, 1.0, -1.65), (3, 1.0, -1.65)):
            self._w(f":CHAN{ch}:DISP ON")
            self._w(f":CHAN{ch}:COUP DC")
            self._w(f":CHAN{ch}:SCAL {scale}")
            self._w(f":CHAN{ch}:OFFS {offs}")
        self._w(":CHAN1:BWL 20M")
        self._w(":CHAN4:DISP OFF")
        self._tb = 1e-3
        self._w(f":TIM:MAIN:SCAL {self._tb:g}")
        self._w(":TIM:MAIN:OFFS 0")
        self._w(":TRIG:EDG:SOUR CHAN2")
        self._w(":TRIG:EDG:SLOP POS")
        self._w(":TRIG:EDG:LEV 1.65")
        self._w(":TRIG:SWE AUTO")
        self._w(":RUN")

    def measure(self, ch: int, item: str) -> Optional[float]:
        """:MEAS:ITEM? の値。測定できない（9.9E37）ときは None。"""
        v = float(self._q(f":MEAS:ITEM? {item},CHAN{ch}"))
        return None if v >= INVALID_MEAS else v

    # ---- 単発取り込み ----

    def arm_capture(self, ch: int, duration_s: float, wait_s: float = 3.0) -> float:
        """ch の立ち上がりで 1 回だけ取り込む準備をする。duration_s を左端付近のトリガから収める。s/div を返す。"""
        import time

        for c in (1, 2, 3):
            self._w(f":CHAN{c}:DISP {'ON' if c == ch else 'OFF'}")
        tb = pick_timebase(duration_s * 1.2 / 11.0)   # トリガを左端から 0.5 div の位置に置く
        self._tb = tb
        self._w(":STOP")
        self._w(f":ACQ:MDEP {MEM_DEPTH}")
        self._w(f":TIM:MAIN:SCAL {tb:g}")
        self._w(f":TIM:MAIN:OFFS {5.5 * tb:g}")
        self._w(f":TRIG:EDG:SOUR CHAN{ch}")
        self._w(":TRIG:EDG:SLOP POS")
        self._w(":TRIG:EDG:LEV 1.65")
        self._w(":TRIG:SWE SING")
        self._w(":SING")
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            if self._q(":TRIG:STAT?") == "WAIT":
                return tb
            time.sleep(0.05)
        raise ScopeError("単発トリガが待機状態（WAIT）になりません")

    def read_capture(self, ch: int, wait_s: float = 10.0) -> Waveform:
        """トリガが掛かって停止するのを待ち、RAW メモリを読み出す。"""
        import time

        end = time.monotonic() + wait_s
        while self._q(":TRIG:STAT?") != "STOP":
            if time.monotonic() >= end:
                raise ScopeError("トリガが掛かりませんでした（DRV_CLK にパルスが出ていない）")
            time.sleep(0.05)
        self._w(f":WAV:SOUR CHAN{ch}")
        self._w(":WAV:MODE RAW")
        self._w(":WAV:FORM BYTE")
        points = int(float(self._q(":ACQ:MDEP?")))
        pre = [float(x) for x in self._q(":WAV:PRE?").split(",")]
        xinc, xorg, xref, yinc, yorg, yref = pre[4:10]
        buf = bytearray()
        i = 1
        while i <= points:
            j = min(points, i + RAW_CHUNK - 1)
            self._w(f":WAV:STAR {i}")
            self._w(f":WAV:STOP {j}")
            self._w(":WAV:DATA?")
            buf += _parse_block(self._s.read_raw())
            i = j + 1
        return Waveform(bytes(buf), xorg - xref * xinc, xinc, yinc, yorg, yref)


def _parse_block(raw: bytes) -> bytes:
    """IEEE 488.2 ブロック（#<桁数><バイト数><データ>）のデータ部。"""
    if raw[:1] != b"#":
        raise ScopeError("波形データの形式が不正です")
    n = int(raw[1:2])
    size = int(raw[2 : 2 + n])
    return raw[2 + n : 2 + n + size]


def open_scope(resource: str = DEFAULT_VISA, timeout_ms: int = 20000) -> Scope:
    """pyvisa で接続する。pyvisa が無い・接続できないときは ScopeUnavailable。"""
    try:
        import pyvisa   # 遅延 import（selftest の D グループだけが使う）
    except ImportError:
        raise ScopeUnavailable("pyvisa が未インストールです（pip install -r requirements-selftest.txt。NI-VISA も必要）")
    try:
        rm = pyvisa.ResourceManager()
        res = rm.open_resource(resource)
        res.timeout = timeout_ms
        res.chunk_size = 1 << 20
        sc = Scope(res)
        sc.idn()
    except Exception as e:
        raise ScopeUnavailable(f"オシロ {resource} に接続できません: {e}")
    return sc
