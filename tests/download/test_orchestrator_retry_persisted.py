"""retry_persisted_download: what allow_request_linked lifts, and what it does not."""

from shelfmark.download import orchestrator


def _payload(**extra):
    """A minimal persisted payload for a request-linked download."""
    payload = {
        "task_id": "slskd:aa",
        "source": "slskd",
        "title": "Book",
        "request_id": 7,
    }
    payload.update(extra)
    return payload


def test_the_flag_still_refuses_a_download_with_nothing_to_restart_from():
    """allow_request_linked lifts the retry-from-requests rule, not the no-source rule."""
    ok, message = orchestrator.retry_persisted_download(
        _payload(can_retry_without_staged_source=False),
        final_status="error",
        allow_request_linked=True,
    )

    assert ok is False
    assert message == "Download cannot be retried"


def test_without_the_flag_a_request_linked_failure_goes_back_to_its_request():
    """Without the flag, a failed request download is retried from the request."""
    ok, message = orchestrator.retry_persisted_download(_payload(), final_status="error")

    assert ok is False
    assert message == "Request-linked downloads must be retried from requests"
