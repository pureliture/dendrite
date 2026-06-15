from __future__ import annotations

from pathlib import Path

import pytest

from dendrite import __version__
from dendrite.cli import main

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_imports_version() -> None:
    assert __version__ == "0.1.0"


def test_cli_help(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "thin-client" in capsys.readouterr().out


def test_cli_boundary(capsys) -> None:
    assert main(["--show-boundary"]) == 0
    assert capsys.readouterr().out.strip() == "provider hook -> locator-only spool -> thin shipper -> POST 18080"


def test_readme_describes_extracted_thin_client_surface() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "M1 bootstrap" not in readme
    assert "기능 추출 전" not in readme
    for command in (
        "dendrite capture-fixture",
        "dendrite capture",
        "dendrite transcript-capture",
        "dendrite transcript-drain",
        "agy-headless-capture",
        "dendrite provider doctor",
        "dendrite provider hook-plan",
    ):
        assert command in readme
    assert "server worker" in readme
    assert "neurons" in readme
