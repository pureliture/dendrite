from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace

from .transcript_capture import TranscriptCaptureSpool, normalize_provider_capture_request


RunCommand = Callable[[Sequence[str]], SimpleNamespace | subprocess.CompletedProcess]


def main(argv: Sequence[str] | None = None) -> int:
    return run_headless_capture(sys.argv[1:] if argv is None else argv)


def run_headless_capture(
    agy_args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
    run_command: RunCommand | None = None,
) -> int:
    if _is_help(agy_args):
        print(
            "\n".join(
                [
                    "usage: agy-headless-capture [agy args...]",
                    "",
                    "Runs agy with the forwarded args, then spools an Antigravity transcript locator.",
                    "Configuration: AK_BRAIN, AK_SPOOL, AK_PROJECT, AGY_BIN.",
                ]
            )
        )
        return 0

    runtime_env = dict(os.environ if env is None else env)
    launch_dir = Path.cwd() if cwd is None else Path(cwd)
    brain = Path(runtime_env.get("AK_BRAIN", str(Path.home() / ".gemini" / "antigravity-cli" / "brain"))).expanduser()
    spool = Path(
        runtime_env.get(
            "AK_SPOOL",
            str(Path.home() / "Library" / "Application Support" / "session-compactor" / "spool"),
        )
    ).expanduser()
    agy_bin = runtime_env.get("AGY_BIN", "agy")
    project = runtime_env.get("AK_PROJECT") or launch_dir.name

    before = _session_names(brain)
    runner = run_command or _run_command
    try:
        completed = runner([agy_bin, *agy_args])
        agy_rc = int(getattr(completed, "returncode", 0))
    except FileNotFoundError:
        print(json.dumps({"status": "agy_error", "error_class": "FileNotFoundError"}, sort_keys=True), file=sys.stderr)
        return 127

    conversation_id = _select_session_id(brain, before)
    transcript = brain / conversation_id / ".system_generated" / "logs" / "transcript.jsonl" if conversation_id else None
    if not conversation_id or transcript is None or not transcript.is_file():
        print(f"agy-headless-capture: no transcript found for capture (agy rc={agy_rc})", file=sys.stderr)
        return agy_rc

    payload = {
        "conversationId": conversation_id,
        "transcriptPath": str(transcript),
        "workspacePaths": [str(launch_dir)],
        "terminationReason": "model_stop",
    }
    try:
        request = normalize_provider_capture_request("antigravity", payload, project=project)
        path = TranscriptCaptureSpool(spool).enqueue(request)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema_version": "dendrite_transcript_capture_result.v1",
                    "status": "capture_error",
                    "error_class": exc.__class__.__name__,
                },
                sort_keys=True,
            )
        )
        return agy_rc

    source_locator = request.get("source_locator") or {}
    print(
        json.dumps(
            {
                "schema_version": "dendrite_transcript_capture_result.v1",
                "status": "spooled",
                "request_id": request["request_id"],
                "provider": request["provider"],
                "project": request["project"],
                "event_type": request["event_type"],
                "source_locator_hash": source_locator.get("locator_hash", ""),
                "spool_file": str(path),
            },
            sort_keys=True,
        )
    )
    return agy_rc


def _run_command(argv: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), check=False)


def _is_help(argv: Sequence[str]) -> bool:
    return len(argv) == 1 and argv[0] in {"-h", "--help"}


def _session_names(brain: Path) -> set[str]:
    if not brain.is_dir():
        return set()
    return {path.name for path in brain.iterdir() if path.is_dir()}


def _select_session_id(brain: Path, before: set[str]) -> str:
    if not brain.is_dir():
        return ""
    sessions = [path for path in brain.iterdir() if path.is_dir()]
    new_sessions = [path for path in sessions if path.name not in before]
    candidates = new_sessions or sessions
    if not candidates:
        return ""
    return max(candidates, key=lambda path: path.stat().st_mtime).name
