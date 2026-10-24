"""設定タブの編集状態。基板の値・編集値・書き込み中の要求・行ごとの結果を持つ。Qt には依存しない。

編集値は「基板の値と違う値をユーザーが入れた行」だけ持つ。編集していない行は基板の値に追従し、
ポーリングで基板の値が変わっても編集中の値は上書きしない。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .items import Item


class SettingsEdit:
    def __init__(self, items: Iterable[Item]) -> None:
        self.items: List[Item] = []
        self._board: Dict[str, Any] = {}
        self._edits: Dict[str, Any] = {}
        self._pending: Set[str] = set()
        self.results: Dict[str, str] = {}
        self.set_items(items)

    def set_items(self, items: Iterable[Item]) -> None:
        """書き込める項目だけを残す。項目の並びが変わったら編集状態を捨てる。"""
        new = [i for i in items if i.writable]
        if [i.key for i in new] != [i.key for i in self.items]:
            self._board.clear()
            self.clear_edits()
        self.items = new

    # ---- 値 ----

    def board(self, key: str) -> Any:
        return self._board.get(key)

    def update_board(self, values: Dict[str, Any]) -> None:
        """基板の値を取り込む（None は未取得・無効）。基板の値と同じになった編集は捨てる。"""
        keys = {i.key for i in self.items}
        for k, v in values.items():
            if k not in keys:
                continue
            if v is None:
                self._board.pop(k, None)
                continue
            self._board[k] = v
            if k in self._edits and self._edits[k] == v:
                del self._edits[k]

    def value(self, key: str) -> Any:
        """編集欄に出す値。編集中ならその値、そうでなければ基板の値（未取得なら None）。"""
        return self._edits.get(key, self._board.get(key))

    def edit(self, key: str, value: Any) -> None:
        """ユーザーが編集欄を変えた。基板の値に戻したら編集なしに戻す。"""
        if key in self._board and self._board[key] == value:
            self._edits.pop(key, None)
        else:
            self._edits[key] = value

    def is_changed(self, key: str) -> bool:
        return key in self._edits and key in self._board and self._edits[key] != self._board[key]

    def is_editing(self, key: str) -> bool:
        return key in self._edits

    def changed(self) -> List[Tuple[Item, Any]]:
        """基板の値と違う行 (項目, 編集値)。項目の並び順。"""
        return [(i, self._edits[i.key]) for i in self.items if self.is_changed(i.key)]

    def clear_edits(self) -> None:
        """編集値を捨てて基板の値に戻す。結果表示も消す。"""
        self._edits.clear()
        self._pending.clear()
        self.results.clear()

    # ---- 書き込み ----

    @property
    def busy(self) -> bool:
        return bool(self._pending)

    def begin_write(self) -> List[Tuple[Item, Any]]:
        """変更行を書き込み対象にして返す（送る順）。前回の結果表示は消す。書き込み中は何も返さない。"""
        if self._pending:
            return []
        self.results.clear()
        todo = self.changed()
        self._pending = {i.key for i, _ in todo}
        for i, _ in todo:
            self.results[i.key] = "送信中"
        return todo

    def record_result(self, key: str, text: str) -> bool:
        """行の結果を記録する。書き込み対象の結果がすべて揃ったら True。"""
        if key not in self._pending:
            return False
        self.results[key] = text
        self._pending.discard(key)
        return not self._pending

    def abort(self, text: str) -> None:
        """切断などで結果が返らなくなった。未了の行に text を入れて書き込み中を解除する。"""
        for k in self._pending:
            self.results[k] = text
        self._pending.clear()
