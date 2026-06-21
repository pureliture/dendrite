from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


INGRESS_ENQUEUE_PATH = "/v1/ingest/enqueue"
RETRYABLE_HTTP_STATUS_CODES = {408, 409, 425, 429}
SAFE_REJECTION_HTTP_STATUS_CODES = {400, 422}
SAFE_REJECTION_STATUSES = {
    "bad_request",
    "invalid",
    "invalid_payload",
    "payload_invalid",
    "rejected",
    "schema_invalid",
    "unsupported",
    "validation_failed",
}


class IngressEnqueueError(RuntimeError):
    pass


class IngressEnqueueRejected(IngressEnqueueError):
    pass


class IngressEnqueueUnreachable(IngressEnqueueError):
    pass


class IngressHttpTransport:
    def __init__(self, *, base_url: str, timeout_seconds: float = 10.0):
        if not base_url:
            raise ValueError("base_url is required")
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("base_url must use http or https")
        if parsed.username or parsed.password:
            raise ValueError("base_url must not contain credentials")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def enqueue_json_payload(self, payload: dict) -> dict:
        request_body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{INGRESS_ENQUEUE_PATH}",
            data=request_body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                try:
                    response_body = response.read().decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise IngressEnqueueUnreachable("ingress enqueue failed: unreachable invalid_response") from exc
                response_payload = _read_json_response(response_body)
        except urllib.error.HTTPError as exc:
            if exc.code in RETRYABLE_HTTP_STATUS_CODES:
                raise IngressEnqueueUnreachable(f"ingress enqueue failed: unreachable http_{exc.code}") from exc
            if 400 <= exc.code < 500:
                rejection_status = None
                if exc.code in SAFE_REJECTION_HTTP_STATUS_CODES:
                    rejection_status = _safe_http_error_rejection_status(exc)
                if rejection_status is not None:
                    raise IngressEnqueueRejected(f"ingress enqueue rejected: {rejection_status}") from exc
                raise IngressEnqueueUnreachable(f"ingress enqueue failed: unreachable http_{exc.code}") from exc
            raise IngressEnqueueUnreachable(f"ingress enqueue failed: unreachable http_{exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise IngressEnqueueUnreachable("ingress enqueue failed: unreachable") from exc
        if response_payload.get("accepted") is not True:
            status = str(response_payload.get("status") or "rejected")
            if status in SAFE_REJECTION_STATUSES:
                raise IngressEnqueueRejected(f"ingress enqueue rejected: {status}")
            raise IngressEnqueueUnreachable(f"ingress enqueue failed: unreachable {status}")
        return {
            "job_id": str(response_payload.get("jobId") or response_payload.get("job_id") or ""),
            "status": str(response_payload.get("status") or "queued"),
        }


def _read_json_response(body: str) -> dict:
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise IngressEnqueueUnreachable("ingress enqueue failed: invalid_json") from exc
    if not isinstance(payload, dict):
        raise IngressEnqueueUnreachable("ingress enqueue failed: invalid_json")
    return payload


def _safe_http_error_rejection_status(exc: urllib.error.HTTPError) -> str | None:
    try:
        body = exc.read().decode("utf-8")
        payload = json.loads(body or "{}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("status") or payload.get("reason") or "")
    if status in SAFE_REJECTION_STATUSES:
        return status
    return None
