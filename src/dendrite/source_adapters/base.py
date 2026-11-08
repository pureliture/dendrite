"""Shared adapter base."""
from __future__ import annotations


class TranscriptSourceAdapter:
    def read_redacted_transcript(self, request: dict) -> str:
        raise NotImplementedError
