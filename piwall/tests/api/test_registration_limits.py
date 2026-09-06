"""Registration's rate limit must key on identity the caller cannot choose.

/api/register is the only limited endpoint with no authenticate() behind it,
and the shared key_func buckets on a raw x-api-key header that nothing
verifies. That combination failed in both directions at once: a caller
rotating the header got an unlimited supply of fresh buckets, while every
real signup — all arriving from the one Next.js process — shared a single
platform-wide bucket. These tests pin both directions.

They build Request objects directly rather than going through TestClient, so
the whole file runs with no database and no app startup.
"""

import pytest
from starlette.requests import Request

from backend import main

SECRET = "s3cret-provisioning-value"


def make_request(headers: dict, client_host: str = "10.0.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/register",
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
            "client": (client_host, 12345),
            "headers": [
                (k.lower().encode(), v.encode()) for k, v in headers.items()
            ],
        }
    )


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(main, "PROVISIONING_SECRET", SECRET)


# ─── provisioning_subject ────────────────────────────────────────────

def test_unset_secret_closes_registration(monkeypatch):
    """Fail closed. An unconfigured deployment must not fall back to open."""
    monkeypatch.setattr(main, "PROVISIONING_SECRET", "")
    request = make_request(
        {"x-provision-secret": "anything", "x-provision-subject": "user-1"}
    )
    assert main.provisioning_subject(request) is None


def test_wrong_secret_is_rejected(configured):
    request = make_request(
        {"x-provision-secret": SECRET + "x", "x-provision-subject": "user-1"}
    )
    assert main.provisioning_subject(request) is None


def test_missing_secret_header_is_rejected(configured):
    assert main.provisioning_subject(make_request({"x-provision-subject": "u"})) is None


def test_correct_secret_returns_subject(configured):
    request = make_request(
        {"x-provision-secret": SECRET, "x-provision-subject": "user-1"}
    )
    assert main.provisioning_subject(request) == "user-1"


def test_secret_without_subject_is_not_vouched(configured):
    """Holding the secret is not itself an identity to bucket on."""
    request = make_request({"x-provision-secret": SECRET, "x-provision-subject": "  "})
    assert main.provisioning_subject(request) is None


def test_oversized_subject_is_truncated(configured):
    request = make_request(
        {"x-provision-secret": SECRET, "x-provision-subject": "u" * 500}
    )
    assert main.provisioning_subject(request) == "u" * 128


# ─── registration_rate_key ───────────────────────────────────────────

def test_rotating_api_key_cannot_mint_new_buckets(configured):
    """The regression: an untrusted caller must not pick its own bucket.

    The shared key_func returns player:<hash of x-api-key>, so this same
    sequence produced a distinct bucket per request and the limit never bound.
    """
    keys = {
        main.registration_rate_key(make_request({"x-api-key": f"forged-{i}"}))
        for i in range(50)
    }
    assert keys == {"anon:10.0.0.1"}


def test_untrusted_callers_bucket_by_address(configured):
    a = main.registration_rate_key(make_request({}, client_host="10.0.0.1"))
    b = main.registration_rate_key(make_request({}, client_host="10.0.0.2"))
    assert a == "anon:10.0.0.1"
    assert b == "anon:10.0.0.2"


def test_vouched_users_get_separate_buckets(configured):
    """The other direction: real signups must not share one bucket.

    Both requests arrive from the same address, as every proxied signup does.
    """
    first = main.registration_rate_key(
        make_request({"x-provision-secret": SECRET, "x-provision-subject": "user-1"})
    )
    second = main.registration_rate_key(
        make_request({"x-provision-secret": SECRET, "x-provision-subject": "user-2"})
    )
    assert first == "provision:user-1"
    assert second == "provision:user-2"


def test_same_user_bucket_is_stable(configured):
    """A user's budget must not reset just because their address changed."""
    headers = {"x-provision-secret": SECRET, "x-provision-subject": "user-1"}
    first = main.registration_rate_key(make_request(headers, client_host="10.0.0.1"))
    second = main.registration_rate_key(make_request(headers, client_host="10.0.0.9"))
    assert first == second == "provision:user-1"


def test_vouched_subject_beats_a_forged_api_key(configured):
    """A trusted caller's bucket is its subject, not a header it also sent."""
    request = make_request(
        {
            "x-provision-secret": SECRET,
            "x-provision-subject": "user-1",
            "x-api-key": "forged",
        }
    )
    assert main.registration_rate_key(request) == "provision:user-1"
