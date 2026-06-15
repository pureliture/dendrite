from __future__ import annotations

import json
import stat

from dendrite.spool import JsonFileSpool


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
