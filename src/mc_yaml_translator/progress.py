"""tqdm progress: one overall bar plus a capped pool of in-flight key previews."""

from __future__ import annotations

import shutil
import threading
from types import TracebackType

from tqdm import tqdm


def _term_size() -> tuple[int, int]:
    size = shutil.get_terminal_size(fallback=(80, 24))
    return size.columns, size.lines


def max_slots(reserved: int = 3) -> int:
    """How many in-flight preview bars fit below the overall bar.

    reserved accounts for the overall bar, a possible trailing blank, and
    a safety line so the UI never scrolls the overall bar off the screen.
    """
    _, lines = _term_size()
    return max(1, lines - reserved)


def _clip_preview(text: str, width: int) -> str:
    flat = text.replace("\r", "").replace("\n", "↵").replace("\t", " ")
    if width <= 1:
        return ""
    if len(flat) <= width:
        return flat
    return "…" + flat[-(width - 1) :]


class KeySlot:
    def __init__(self, bar: tqdm, path: str, lock: threading.Lock) -> None:
        self.bar = bar
        self.path = path
        self._lock = lock

    def update_preview(self, streamed: str) -> None:
        from mc_yaml_translator.client import preview_translation

        shown = preview_translation(streamed) or streamed
        cols, _ = _term_size()
        # desc + separator + preview should fit on one line
        budget = max(8, cols - len(self.path) - 6)
        preview = _clip_preview(shown, budget)
        with self._lock:
            self.bar.set_postfix_str(preview, refresh=True)


class ProgressUI:
    def __init__(self, total: int, disable: bool = False) -> None:
        self.total = total
        self.disable = disable
        self._lock = threading.Lock()
        self._slot_count = 1 if disable else max_slots()
        self._free: list[int] = list(range(1, self._slot_count + 1))
        self._overall: tqdm | None = None

    def __enter__(self) -> "ProgressUI":
        self._overall = tqdm(
            total=self.total,
            desc="翻译",
            unit="键",
            position=0,
            leave=True,
            dynamic_ncols=True,
            disable=self.disable,
            colour="cyan",
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._overall is not None:
            self._overall.close()
            self._overall = None

    def acquire_slot(self, path: str) -> KeySlot | None:
        if self.disable:
            return None
        with self._lock:
            if not self._free:
                return None
            pos = self._free.pop(0)
        bar = tqdm(
            total=0,
            desc=path,
            position=pos,
            leave=False,
            dynamic_ncols=True,
            bar_format="{desc}: {postfix}",
            disable=False,
        )
        bar.set_postfix_str("…", refresh=True)
        return KeySlot(bar, path, self._lock)

    def release_slot(self, slot: KeySlot | None) -> None:
        if slot is None:
            return
        with self._lock:
            pos = slot.bar.pos
            slot.bar.close()
            # tqdm.pos is the position we gave it
            try:
                position = int(pos)
            except Exception:
                position = -1
            if 1 <= position <= self._slot_count and position not in self._free:
                self._free.append(position)
                self._free.sort()

    def tick(self, ok: bool) -> None:
        if self._overall is None:
            return
        with self._lock:
            if ok:
                self._overall.update(1)
            else:
                self._overall.update(1)
                # Keep a running failure count in the postfix.
                n = getattr(self, "_fails", 0) + 1
                self._fails = n
                self._overall.set_postfix_str(f"失败 {n}", refresh=True)
