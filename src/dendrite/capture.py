from __future__ import annotations

from pathlib import Path

from .minimizer import minimize_event
from .spool import Spool


def capture_event(raw_event: dict, spool_root: Path | str) -> Path:
    event = minimize_event(raw_event)
    return Spool(spool_root).enqueue(event)
