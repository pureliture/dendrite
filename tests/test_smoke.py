from __future__ import annotations

import pytest

from dendrite import __version__
from dendrite.cli import main


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
