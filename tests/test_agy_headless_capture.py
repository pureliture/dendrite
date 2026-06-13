from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from dendrite.agy_headless_capture import run_headless_capture


def _write_antigravity_transcript(brain: Path, session_id: str) -> Path:
    transcript = brain / session_id / ".system_generated" / "logs" / "transcript.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n", encoding="utf-8")
    return transcript


def test_headless_capture_spools_antigravity_locator_without_raw_transcript(tmp_path, capsys) -> None:
    brain = tmp_path / "brain"
    spool = tmp_path / "spool"
    launch_dir = tmp_path / "Projects" / "example-project"
    launch_dir.mkdir(parents=True)

    def fake_agy(argv):
        assert argv == ["agy", "--print", "hello"]
        _write_antigravity_transcript(brain, "session-1")
        return SimpleNamespace(returncode=7)

    rc = run_headless_capture(
        ["--print", "hello"],
        env={"AK_BRAIN": str(brain), "AK_SPOOL": str(spool), "AGY_BIN": "agy"},
        cwd=launch_dir,
        run_command=fake_agy,
    )

    assert rc == 7
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert output["status"] == "spooled"
    assert output["provider"] == "antigravity"
    assert output["project"] == "example-project"
    assert output["source_locator_hash"].startswith("sha256:")
    assert "transcript.jsonl" not in output_text

    stored = json.loads(next((spool / "pending").glob("*.json")).read_text(encoding="utf-8"))
    assert stored["content_policy"] == "locator_only"
    assert stored["source_locator"]["runtime_handle"].endswith("transcript.jsonl")
    assert stored["public_summary"]["project"] == "example-project"


def test_headless_capture_uses_newest_existing_session_when_no_new_session(tmp_path, capsys) -> None:
    brain = tmp_path / "brain"
    spool = tmp_path / "spool"
    launch_dir = tmp_path / "workspace"
    launch_dir.mkdir()
    _write_antigravity_transcript(brain, "session-existing")

    def fake_agy(argv):
        assert argv == ["agy"]
        return SimpleNamespace(returncode=0)

    assert (
        run_headless_capture(
            [],
            env={"AK_BRAIN": str(brain), "AK_SPOOL": str(spool), "AGY_BIN": "agy", "AK_PROJECT": "forced"},
            cwd=launch_dir,
            run_command=fake_agy,
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "spooled"
    stored = json.loads(next((spool / "pending").glob("*.json")).read_text(encoding="utf-8"))
    assert stored["project"] == "workspace"
    assert "session-existing" in stored["source_locator"]["runtime_handle"]


def test_headless_capture_help_does_not_run_agy_or_spool(tmp_path, capsys) -> None:
    def fail_if_called(argv):
        raise AssertionError("agy must not run for shim help")

    assert (
        run_headless_capture(
            ["--help"],
            env={"AK_BRAIN": str(tmp_path / "brain"), "AK_SPOOL": str(tmp_path / "spool"), "AGY_BIN": "agy"},
            cwd=tmp_path,
            run_command=fail_if_called,
        )
        == 0
    )

    assert "usage: agy-headless-capture" in capsys.readouterr().out
    assert not (tmp_path / "spool").exists()


def test_headless_capture_returns_agy_rc_when_transcript_is_missing(tmp_path, capsys) -> None:
    def fake_agy(argv):
        assert argv == ["agy", "--print", "hello"]
        return SimpleNamespace(returncode=3)

    rc = run_headless_capture(
        ["--print", "hello"],
        env={"AK_BRAIN": str(tmp_path / "missing-brain"), "AK_SPOOL": str(tmp_path / "spool"), "AGY_BIN": "agy"},
        cwd=tmp_path,
        run_command=fake_agy,
    )

    assert rc == 3
    captured = capsys.readouterr()
    assert "no transcript found for capture" in captured.err
    assert "transcript.jsonl" not in captured.err
