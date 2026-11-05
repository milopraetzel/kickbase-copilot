"""Tests for `kba probe` secret redaction."""

from kickbase_agent.cli.probe import REDACTED, redact


def test_redact_masks_sensitive_keys_recursively() -> None:
    payload = {
        "tkn": "secret-token",
        "u": {"email": "emil@example.com", "id": "42", "name": "Emil"},
        "srvl": [{"i": "123", "n": "Liga", "rt": "refresh-secret"}],
    }

    result = redact(payload)

    assert result["tkn"] == REDACTED
    assert result["u"]["email"] == REDACTED
    assert result["u"]["id"] == "42"
    assert result["srvl"][0]["rt"] == REDACTED
    assert result["srvl"][0]["n"] == "Liga"


def test_redact_masks_known_secret_strings_exact_match() -> None:
    payload = {"who": "emil@example.com", "note": "kontakt: emil@example.com bitte"}

    result = redact(payload, frozenset({"emil@example.com"}))

    assert result["who"] == REDACTED
    assert result["note"] == "kontakt: emil@example.com bitte"
