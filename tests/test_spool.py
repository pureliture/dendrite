from __future__ import annotations

import json
import stat

from dendrite.spool import JsonFileSpool, Spool


def _event(event_id: str = "evt_001") -> dict:
    return {
        "schema_version": "agent_knowledge_event.v1",
        "event_id": event_id,
        "provider": "codex",
        "project": "dendrite",
        "session_id_hash": "sha256:" + "a" * 64,
        "event_type": "session_end",
        "observed_at": "2026-06-21T00:00:00Z",
        "privacy_level": "private",
        "summary": {"text": "redacted"},
        "content_hash": "sha256:" + "b" * 64,
        "redaction_version": "redaction.v2",
    }


def test_json_file_spool_writes_once_and_moves_private_file(tmp_path):
    spool = JsonFileSpool(tmp_path / "spool", subdirs=("pending", "acked"), root_label="test spool")

    first = spool.write_json_once("item.json", {"b": 2, "a": 1}, separators=(",", ":"))
    second = spool.write_json_once("item.json", {"a": 999})
    moved = spool.move_to(first, "acked")

    assert second == first
    assert moved.parent.name == "acked"
    assert json.loads(moved.read_text(encoding="utf-8")) == {"a": 1, "b": 2}
    assert stat.S_IMODE(moved.stat().st_mode) == 0o600
    assert spool.depth_counts() == {"pending": 0, "acked": 1}


def test_json_file_spool_ignores_stale_fixed_temp_file(tmp_path):
    spool = JsonFileSpool(tmp_path / "spool", subdirs=("pending",), root_label="test spool")
    stale_temp = tmp_path / "spool" / "pending" / ".item.json.tmp"
    stale_temp.write_text("stale\n", encoding="utf-8")

    written = spool.write_json_once("item.json", {"ok": True})

    assert written.name == "item.json"
    assert json.loads(written.read_text(encoding="utf-8")) == {"ok": True}
    assert stale_temp.read_text(encoding="utf-8") == "stale\n"


def test_json_file_spool_rejects_path_traversal_filename(tmp_path):
    spool = JsonFileSpool(tmp_path / "spool", subdirs=("pending",), root_label="test spool")

    try:
        spool.write_json_once("../escape.json", {"ok": False})
    except ValueError as exc:
        assert "spool filename" in str(exc)
    else:
        raise AssertionError("path traversal filename must be rejected")

    assert not (tmp_path / "escape.json").exists()


def test_spool_public_api_compat_after_composition(tmp_path):
    spool = Spool(tmp_path / "spool")

    first = spool.enqueue(_event())
    second = spool.write_json_once("manual.json", _event("evt_manual"))
    duplicate = spool.enqueue(_event())
    claimed = spool.claim_next()
    acked = spool.ack(claimed)

    assert duplicate == first
    assert spool._find_existing(first.name) == acked
    assert spool.find_existing("manual.json") == second
    assert [path.name for path in spool.files("pending")] == ["manual.json"]
    assert spool.depth_counts() == {"pending": 1, "processing": 0, "acked": 1, "quarantine": 0}
    assert spool.root == tmp_path / "spool"
    assert spool.subdirs == ("pending", "processing", "acked", "quarantine")
    assert spool.root_label == "spool"

    rewritten = json.loads(spool.replace_json(second, _event("evt_rewritten")).read_text(encoding="utf-8"))
    quarantined = spool.quarantine(spool.claim_next())

    assert rewritten["event_id"] == "evt_rewritten"
    assert quarantined.parent.name == "quarantine"
    assert spool.move_to(quarantined, "pending").parent.name == "pending"
