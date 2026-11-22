from __future__ import annotations

from dendrite.transcript_ingest import (
    _classify_error,
    _quarantine_failure_record,
    _redacted_error_message,
)


def test_ingress_http_rejection_preserves_safe_category() -> None:
    exc = RuntimeError("ingress enqueue rejected: http_502")
    assert _classify_error(exc) == "ingress_rejected_http_502"
    assert _redacted_error_message(exc) == "ingress_rejected_http_502"


def test_ingress_unreachable_preserves_category() -> None:
    exc = RuntimeError("ingress enqueue failed: unreachable")
    assert _classify_error(exc) == "ingress_unreachable"
    assert _redacted_error_message(exc) == "ingress_unreachable"


def test_ingress_invalid_json_preserves_category() -> None:
    exc = RuntimeError("ingress enqueue failed: invalid_json")
    assert _classify_error(exc) == "ingress_invalid_json"


def test_generic_runtime_error_still_redacted() -> None:
    exc = RuntimeError("some internal /private/path leaked secret detail")
    assert _classify_error(exc) == "RuntimeError"
    assert _redacted_error_message(exc) == "transcript ingest failed"


def test_quarantine_record_surfaces_ingress_category_and_recoverable() -> None:
    exc = RuntimeError("ingress enqueue rejected: http_503")
    rec = _quarantine_failure_record(exc)
    assert rec["error_class"] == "ingress_rejected_http_503"
    assert rec["message"] == "ingress_rejected_http_503"
    assert rec["recoverable"] is True


def test_quarantine_message_preserves_server_rejection_detail() -> None:
    """A stored quarantine record must explain the refusal without payload text."""
    exc = RuntimeError("ingress enqueue rejected: status=rejected error=SecretLikeMetadataError")
    rec = _quarantine_failure_record(exc)
    assert rec["error_class"] == "ingress_rejected"
    assert rec["message"] == "ingress_rejected; status=rejected error=SecretLikeMetadataError"


def test_quarantine_message_drops_unparsable_ingress_detail() -> None:
    """Anything outside the closed ingress vocabulary collapses to the class."""
    exc = RuntimeError("ingress enqueue rejected: transcript body text that must not be stored")
    rec = _quarantine_failure_record(exc)
    assert rec["error_class"] == "ingress_rejected"
    assert rec["message"] == "ingress_rejected"
