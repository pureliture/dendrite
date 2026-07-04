from __future__ import annotations

import json

from dendrite.cli import main
from dendrite.provider_contracts import (
    build_default_provider_source_contracts,
    build_provider_doctor_report,
    build_provider_hook_plan,
)
from dendrite.providers.contracts import no_op_hook_response, normalize_provider_event


def test_default_provider_source_contracts_are_locator_only() -> None:
    contracts = {contract.provider: contract for contract in build_default_provider_source_contracts()}

    assert set(contracts) == {"claude", "gemini", "codex", "antigravity", "hermes"}
    assert contracts["codex"].raw_prompt_policy == "locator_only_not_transcript_content"
    # The four live-smoked providers store per-session jsonl and are locator-verified.
    for name in ("claude", "gemini", "codex", "antigravity"):
        contract = contracts[name]
        assert contract.source_locator_field == "transcript_path"
        assert contract.hook_install_status == "deferred_not_installed"
        assert contract.source_status == "source_locator_verified"
    # Hermes is registered but intentionally unverified: its store is a single
    # SQLite DB and it has not been live-smoked, so it must not claim a verified
    # source locator. It is still deferred (never auto-installed).
    hermes = contracts["hermes"]
    assert hermes.hook_install_status == "deferred_not_installed"
    assert hermes.source_status != "source_locator_verified"


def test_provider_doctor_report_is_non_mutating() -> None:
    report = build_provider_doctor_report()

    assert report["network_used"] is False
    assert report["live_mutation_allowed"] is False
    assert report["hook_mutation_performed"] is False
    assert report["mutation_performed"] is False


def test_provider_hook_plan_uses_dendrite_command() -> None:
    plan = build_provider_hook_plan(provider="codex", action="install")

    assert plan["planned_status"] == "plan_only"
    assert plan["planned_argv"][:2] == ["dendrite", "transcript-capture"]
    assert plan["requires_approval_before_execution"] is True


def test_cli_provider_doctor_outputs_json(capsys) -> None:
    assert main(["provider", "doctor"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == build_provider_doctor_report()


def test_cli_provider_hook_plan_outputs_non_mutating_json(capsys) -> None:
    assert main(["provider", "hook-plan", "--provider", "claude", "--action", "install"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["provider"] == "claude"
    assert output["live_mutation_allowed"] is False
    assert output["hook_mutation_performed"] is False


def test_hermes_hook_plan_reports_profile_readiness_without_paths(tmp_path) -> None:
    hermes_home = tmp_path / ".hermes"
    default_db = hermes_home / "state.db"
    metis_home = hermes_home / "profiles" / "metis"
    metis_db = metis_home / "state.db"
    default_db.parent.mkdir()
    metis_home.mkdir(parents=True)
    default_db.write_bytes(b"not-opened-by-hook-plan")
    metis_db.write_bytes(b"not-opened-by-hook-plan")
    (hermes_home / "config.yaml").write_text(
        "hooks:\n  on_session_end:\n    - command: dendrite transcript-capture --provider hermes\n",
        encoding="utf-8",
    )

    plan = build_provider_hook_plan(provider="hermes", action="install", hermes_home=hermes_home)

    assert plan["provider"] == "hermes"
    assert plan["mutation_performed"] is False
    readiness = plan["profile_readiness"]
    assert readiness["default"]["source_status"] == "source_present"
    assert readiness["default"]["hook_status"] == "hook_present"
    assert readiness["metis"]["source_status"] == "source_present"
    assert readiness["metis"]["hook_status"] == "hook_missing"
    blob = json.dumps(plan, sort_keys=True)
    assert str(default_db) not in blob
    assert str(metis_db) not in blob


def test_provider_doctor_reports_hermes_profile_summary(tmp_path) -> None:
    hermes_home = tmp_path / ".hermes"
    default_db = hermes_home / "state.db"
    metis_db = hermes_home / "profiles" / "metis" / "state.db"
    default_db.parent.mkdir()
    metis_db.parent.mkdir(parents=True)
    default_db.write_bytes(b"not-opened-by-doctor")
    metis_db.write_bytes(b"not-opened-by-doctor")

    report = build_provider_doctor_report(hermes_home=hermes_home)

    assert report["mutation_performed"] is False
    hermes = report["provider_profile_readiness"]["hermes"]
    assert set(hermes) == {"default", "metis"}
    assert hermes["default"]["source_status"] == "source_present"
    assert hermes["metis"]["source_status"] == "source_present"
    blob = json.dumps(report, sort_keys=True)
    assert str(default_db) not in blob
    assert str(metis_db) not in blob


def test_provider_event_normalizer_hashes_raw_prompt() -> None:
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "s1",
        "prompt": "raw prompt must not persist",
    }

    normalized = normalize_provider_event("codex", payload)

    assert normalized["event_type"] == "user_prompt_seen"
    assert normalized["prompt_hash"].startswith("sha256:")
    assert "prompt" not in normalized


def test_no_op_hook_response_is_empty() -> None:
    assert no_op_hook_response("codex") == ""
