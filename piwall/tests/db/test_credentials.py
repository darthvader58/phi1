from backend.db.crud import hash_api_key


def test_hash_is_deterministic():
    assert hash_api_key("pw_abc") == hash_api_key("pw_abc")


def test_hash_differs_per_key():
    assert hash_api_key("pw_abc") != hash_api_key("pw_abd")


def test_hash_is_not_the_raw_key():
    raw = "pw_secret"
    assert hash_api_key(raw) != raw
    assert raw not in hash_api_key(raw)


def test_hash_is_hex_sha256():
    digest = hash_api_key("pw_abc")
    assert len(digest) == 64
    int(digest, 16)
