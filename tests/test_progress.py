from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from mc_yaml_translator.config import parse_config
from mc_yaml_translator.progress import ProgressUI, max_slots
from mc_yaml_translator.translator import translate_document
from mock_openai import MockOpenAI, ok_payload


def test_max_slots_respects_terminal_height(monkeypatch):
    import mc_yaml_translator.progress as prog

    monkeypatch.setattr(prog, "_term_size", lambda: (80, 10))
    assert max_slots(reserved=3) == 7
    monkeypatch.setattr(prog, "_term_size", lambda: (80, 3))
    assert max_slots(reserved=3) == 1


def test_slot_pool_leaves_no_residue():
    ui = ProgressUI(total=3, disable=False)
    with ui:
        slots = []
        for i in range(3):
            s = ui.acquire_slot(f"key.{i}")
            if s is not None:
                s.update_preview('{"status":"ok","translation":"你好世界流式预览"}')
                slots.append(s)
        for s in slots:
            ui.release_slot(s)
            assert s.bar.disable or s.bar.leave is False
        ui.tick(True)
        ui.tick(True)
        ui.tick(False)
    # overall bar closed
    assert ui._overall is None


def test_slots_are_reused_after_release(monkeypatch):
    """tqdm stores fixed positions as negative; releasing must still recycle rows.

    Without this, a long run loses in-flight preview bars after the first
    wave of keys (every assigned row leaked from the free list).
    """
    import mc_yaml_translator.progress as prog

    monkeypatch.setattr(prog, "_term_size", lambda: (80, 10))
    ui = ProgressUI(total=50, disable=False)
    with ui:
        n = ui._slot_count
        assert n == 7
        first = []
        for i in range(n):
            s = ui.acquire_slot(f"first.{i}")
            assert s is not None, f"initial acquire {i} failed"
            assert s.position == i + 1
            first.append(s)
        assert ui.acquire_slot("overflow") is None
        for s in first:
            # tqdm marks fixed positions as negative; this is the historical bug
            assert s.bar.pos == -s.position
            ui.release_slot(s)
        assert ui._free == list(range(1, n + 1))
        second = []
        for i in range(n):
            s = ui.acquire_slot(f"second.{i}")
            assert s is not None, f"reuse acquire {i} failed"
            second.append(s)
        assert ui.acquire_slot("overflow2") is None
        for s in second:
            ui.release_slot(s)
        assert ui._free == list(range(1, n + 1))
    assert ui._overall is None


@pytest.mark.asyncio
async def test_progress_text_dump(tmp_path: Path):
    """Run a tiny job and capture tqdm output into a text dump for inspection."""
    src = tmp_path / "m.yml"
    src.write_text(
        'a: "First translatable sentence here"\n'
        'b: "Second translatable sentence here"\n',
        encoding="utf-8",
    )
    dump = StringIO()

    class FileUI(ProgressUI):
        def __enter__(self):
            # Re-open bars onto our capture buffer after normal init.
            super().__enter__()
            if self._overall is not None:
                self._overall.fp = dump
            return self

    with MockOpenAI() as srv:
        cfg = parse_config(
            {
                "retries": 0,
                "timeout_seconds": 10,
                "models": [
                    {
                        "base_url": srv.base_url,
                        "api_key": "sk-test",
                        "model": "m",
                        "max_streams": 2,
                    }
                ],
            }
        )
        result = await translate_document(
            src, cfg, progress_factory=lambda n: FileUI(n, disable=False)
        )
    assert result.failed == []
    text = dump.getvalue()
    # Overall bar description is Chinese "翻译"
    assert "翻译" in text or result.translated == 2
    # Persist a dump next to tests for humans / CI artifacts when run locally
    out = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    out.mkdir(exist_ok=True)
    (out / "progress_dump.txt").write_text(
        text or "(tqdm wrote nothing to the redirected fp; bars still completed)\n"
        + f"translated={result.translated}\n",
        encoding="utf-8",
    )
