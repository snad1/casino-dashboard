"""tools/load_proof.py: bet gaps from the bot database, WAL sampling, the off/on gate."""

import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "load_proof.py"
START = datetime(2026, 11, 20, 10, 0, 0)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("load_proof", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proof = _load()


def _bot_db(path: Path, bets: list[tuple[int, float]], wal: bool = True) -> Path:
    """A bets table like the bot's: (session, seconds after START) per row, in id order."""
    with sqlite3.connect(path) as conn:
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE bets (id INTEGER PRIMARY KEY, session_id INTEGER, timestamp TEXT)"
        )
        conn.executemany(
            "INSERT INTO bets (session_id, timestamp) VALUES (?, ?)",
            [(s, (START + timedelta(seconds=t)).isoformat()) for s, t in bets],
        )
    return path


def test_gaps_are_per_session_and_skip_pauses_and_the_window_edges(tmp_path: Path) -> None:
    db = _bot_db(
        tmp_path / "bot.db",
        [
            (1, -5.0),
            (1, 0.0),
            (1, 0.040),
            (2, 0.050),
            (1, 0.080),
            (2, 0.090),
            (1, 3.000),
            (1, 3.050),
            (1, 99.0),
        ],
    )
    gaps = proof.bet_gaps(db, START, START + timedelta(seconds=10))
    assert [round(g) for g in sorted(gaps)] == [40, 40, 40, 50]


def test_the_database_is_opened_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _bot_db(tmp_path / "bot.db", [(1, 0.0), (1, 0.05)])
    opened: list[str] = []
    real = sqlite3.connect

    def spy(target: str, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        opened.append(target)
        conn: sqlite3.Connection = real(target, *args, **kwargs)
        return conn

    monkeypatch.setattr(proof.sqlite3, "connect", spy)
    proof.bet_gaps(db, START, START + timedelta(seconds=1))
    assert opened == [f"file:{db}?mode=ro"]
    with real(opened[0], uri=True) as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO bets (session_id, timestamp) VALUES (1, 'x')")


def test_sample_watches_the_wal_then_reads_the_window(tmp_path: Path) -> None:
    db = _bot_db(tmp_path / "bot.db", [(1, 1.0), (1, 1.04), (1, 1.10)], wal=False)
    wal = Path(f"{db}-wal")
    sizes = iter([10, 30, 20])
    clock = iter([START, START + timedelta(seconds=30)])

    def sleep(_: float) -> None:
        wal.write_bytes(b"x" * next(sizes))

    result = proof.sample(db, "on", 0.5, sleep=sleep, now=lambda: next(clock))
    assert result["n"] == 2 and result["p50_ms"] in (40.0, 60.0)
    assert result["wal_max"] == 30 and result["wal_last"] == 20
    assert result["bps"] == round(1000 / 50, 2)


def test_sample_says_it_started_and_reports_each_minute(tmp_path: Path) -> None:
    db = _bot_db(tmp_path / "bot.db", [(1, 1.0), (1, 1.04)])
    lines: list[str] = []
    clock = iter([START, START + timedelta(minutes=3)])
    proof.sample(db, "off", 3, sleep=lambda _: None, now=lambda: next(clock), say=lines.append)
    assert lines[0] == ("off: sampling for 3 min; Ctrl-C stops early and keeps what was measured")
    assert [line.split(",")[0] for line in lines[1:]] == ["off: 1 of 3 min", "off: 2 of 3 min"]
    assert lines[1].endswith(" MiB")


def test_ctrl_c_ends_the_window_and_keeps_what_was_measured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _bot_db(tmp_path / "bot.db", [(1, 1.0), (1, 1.04), (1, 1.10)])
    steps = iter([None, None, KeyboardInterrupt()])

    def sleep(_: float) -> None:
        step = next(steps)
        if step is not None:
            raise step

    clock = iter([START, START + timedelta(seconds=20)])
    real = proof.sample
    monkeypatch.setattr(
        proof,
        "sample",
        lambda db, label, minutes: real(db, label, minutes, sleep=sleep, now=lambda: next(clock)),
    )
    out = tmp_path / "results"
    code = proof.main(
        ["sample", "--db", str(db), "--label", "off", "--minutes", "60", "--out", str(out)]
    )
    assert code == 0
    saved = json.loads((out / "off.json").read_text())
    assert saved["n"] == 2 and saved["end"] == (START + timedelta(seconds=20)).isoformat()
    streams = capsys.readouterr()
    assert "off: stopped after 0.3 of 60 min" in streams.err
    assert "off: 2 gaps" in streams.out


def test_an_empty_window_stops_with_a_reason(tmp_path: Path) -> None:
    db = _bot_db(tmp_path / "bot.db", [])
    with pytest.raises(SystemExit, match="no bets in the window"):
        proof.sample(db, "off", 0, sleep=lambda _: None, now=lambda: START)


def _summary(label: str, gaps: list[float]) -> dict[str, Any]:
    summary: dict[str, Any] = proof.summarize(
        label, gaps, [1048576, 2097152], START, START + timedelta(hours=1)
    )
    return summary


@pytest.mark.parametrize(("slower", "passed"), [(1.019, True), (1.021, False), (0.95, True)])
def test_the_gate_allows_at_most_two_percent_slower(slower: float, passed: bool) -> None:
    off = _summary("off", [40.0] * 500)
    on = _summary("on", [40.0 * slower] * 500)
    result = proof.compare(off, on)
    assert result["passed"] is passed
    assert result["low"] <= result["change"] <= result["high"]


def test_report_table_and_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    off, on = tmp_path / "off.json", tmp_path / "on.json"
    off.write_text(json.dumps(_summary("off", [40.0] * 200 + [45.0] * 50)))
    on.write_text(json.dumps(_summary("on", [44.0] * 250)))
    assert proof.main(["report", str(off), str(on)]) == 1
    out = capsys.readouterr().out
    assert "| baseline: live bot, events off" in out
    assert "| off (2026-11-20T10:00 to 11:00, n = 250) | 40.0" in out
    assert out.count("| 2.0 | 2.0 |") == 2
    assert "Gate (at most +2 %): **FAIL**" in out


def test_sample_needs_the_bot_database(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = proof.main(["sample", "--db", str(tmp_path / "missing.db"), "--label", "off"])
    assert code == 2 and "no bot database" in capsys.readouterr().err
