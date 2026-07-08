"""Tests for server-side chat transcripts (src/studio/transcripts.py)."""

import json
from pathlib import Path

import pytest

from src.studio import transcripts


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("STUDIO_RUNS_DIR", str(tmp_path))
    return tmp_path


def test_append_load_round_trip(runs_root: Path) -> None:
    cid = "conv_abc-123"
    transcripts.append_event(cid, {"role": "user", "text": "make a video"})
    transcripts.append_event(cid, {"type": "tool_use", "id": "t1", "name": "Bash", "summary": "Bash: ls"})
    transcripts.append_event(cid, {"type": "tool_result", "id": "t1", "ok": True})
    transcripts.append_event(cid, {"type": "tool_use", "id": "t2", "name": "Write", "summary": "Write: x"})
    transcripts.append_event(cid, {"type": "tool_result", "id": "t2", "ok": False, "error": "boom"})
    transcripts.append_event(cid, {"role": "assistant", "text": "done!"})

    assert transcripts.load_messages(cid) == [
        {"role": "user", "text": "make a video", "tools": []},
        {
            "role": "assistant",
            "text": "done!",
            "tools": [
                {"name": "Bash", "summary": "Bash: ls", "status": "done"},
                {"name": "Write", "summary": "Write: x", "status": "failed"},
            ],
        },
    ]


def test_tool_before_text_creates_assistant_message(runs_root: Path) -> None:
    cid = "conv_toolsonly"
    transcripts.append_event(cid, {"type": "tool_use", "id": "t1", "name": "Read", "summary": "Read: f"})
    transcripts.append_event(cid, {"type": "tool_result", "id": "t1", "ok": True})

    assert transcripts.load_messages(cid) == [
        {
            "role": "assistant",
            "text": "",
            "tools": [{"name": "Read", "summary": "Read: f", "status": "done"}],
        },
    ]


def test_multiple_turns_stay_separate(runs_root: Path) -> None:
    cid = "conv_turns"
    transcripts.append_event(cid, {"role": "user", "text": "one"})
    transcripts.append_event(cid, {"role": "assistant", "text": "first"})
    transcripts.append_event(cid, {"role": "user", "text": "two"})
    transcripts.append_event(cid, {"type": "tool_use", "id": "t1", "name": "Bash", "summary": "s"})
    transcripts.append_event(cid, {"role": "assistant", "text": "second"})

    messages = transcripts.load_messages(cid)
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[3]["text"] == "second"
    assert messages[3]["tools"] == [{"name": "Bash", "summary": "s", "status": "done"}]


def test_error_event_becomes_error_message(runs_root: Path) -> None:
    cid = "conv_err"
    transcripts.append_event(cid, {"role": "user", "text": "hi"})
    transcripts.append_event(cid, {"type": "error", "message": "turn failed"})

    assert transcripts.load_messages(cid) == [
        {"role": "user", "text": "hi", "tools": []},
        {"role": "error", "text": "turn failed", "tools": []},
    ]


def test_invalid_conversation_id_writes_nothing(runs_root: Path) -> None:
    transcripts.append_event("../evil", {"role": "user", "text": "hi"})
    transcripts.append_event("a/b", {"role": "user", "text": "hi"})
    transcripts.append_event("", {"role": "user", "text": "hi"})
    assert not (runs_root / "chats").exists()
    assert transcripts.load_messages("../evil") == []
    assert transcripts.load_messages("missing_but_valid") == []


def test_events_get_timestamps(runs_root: Path) -> None:
    cid = "conv_ts"
    transcripts.append_event(cid, {"role": "user", "text": "hi"})
    lines = (runs_root / "chats" / f"{cid}.jsonl").read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert isinstance(record["ts"], float)
    assert record["text"] == "hi"


def test_load_skips_corrupt_lines(runs_root: Path) -> None:
    cid = "conv_corrupt"
    transcripts.append_event(cid, {"role": "user", "text": "ok"})
    path = runs_root / "chats" / f"{cid}.jsonl"
    with path.open("a") as fh:
        fh.write("not json\n")
    transcripts.append_event(cid, {"role": "assistant", "text": "still ok"})

    assert [m["text"] for m in transcripts.load_messages(cid)] == ["ok", "still ok"]
