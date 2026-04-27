"""Tests for senex.secret_redactor — pattern set + redact_dict.

Conventions §5: secret-shaped strings in tests are placeholders, never real keys.
"""
from __future__ import annotations

import pytest

from senex.secret_redactor import SecretRedactor


@pytest.fixture(scope="module")
def redactor() -> SecretRedactor:
    return SecretRedactor()


@pytest.mark.parametrize(
    "raw, expected_marker",
    [
        # AWS access key (16-char IAM access-key ID): AKIA + 16 alphanum.
        ("AKIAIOSFODNN7EXAMPLE", "[REDACTED:aws_access_key]"),
        # GitHub PAT classic: ghp_ + 36 alphanum.
        ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "[REDACTED:github_pat]"),
        # GitHub OAuth: gho_ + 36 alphanum.
        ("gho_abcdefghijklmnopqrstuvwxyz0123456789", "[REDACTED:github_pat]"),
        # OpenAI key.
        ("sk-FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE12", "[REDACTED:llm_api_key]"),
        # Anthropic key.
        ("sk-ant-FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE12", "[REDACTED:llm_api_key]"),
        # JWT (three base64 segments — fixture is built at runtime so the literal
        # bytes do not appear as a single token in this source file).
        (
            "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0In0" + "."
            + "Sf" + "lKxwRJSMeKKF" + "2QT4fwpMeJf36",
            "[REDACTED:jwt]",
        ),
        # PEM block.
        (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
            "[REDACTED:pem]",
        ),
        # env-style.
        ("DATABASE_PASSWORD=hunter2-with-letters", "[REDACTED:env_secret]"),
        ("API_TOKEN=abcdef0123456789", "[REDACTED:env_secret]"),
    ],
)
def test_redactor_pattern_class_redacts(
    redactor: SecretRedactor, raw: str, expected_marker: str
) -> None:
    out = redactor.redact(raw)
    assert expected_marker in out
    # The literal sensitive substring must be gone.
    assert "FAKEFAKEFAKEFAKE" not in out  # OpenAI body removed.
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "MIIEowIBAAKCAQEA" not in out


def test_redactor_priority_pem_before_env(redactor: SecretRedactor) -> None:
    # Generic env pattern MUST NOT eat a PEM block: PEM matches first.
    mixed = "PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----\nbody\n-----END RSA PRIVATE KEY-----"
    out = redactor.redact(mixed)
    assert "[REDACTED:pem]" in out
    # Body content gone.
    assert "BEGIN RSA" not in out


def test_redactor_redact_dict_redacts_known_key_names(redactor: SecretRedactor) -> None:
    d = {
        "api_key": "sk-realkey-NOT-REAL-1234567890",
        "username": "alice",
        "nested": {"github_token": "ghp_NotRealTokenButShaped0000000000000000"},
        "auth_secret": "shouldredact",
        "password": "hunter2",
    }
    out = redactor.redact_dict(d)
    assert out["api_key"] == "[REDACTED]"
    assert out["nested"]["github_token"] == "[REDACTED]"
    assert out["auth_secret"] == "[REDACTED]"
    assert out["password"] == "[REDACTED]"
    assert out["username"] == "alice"  # not a sensitive key.


def test_redactor_redact_dict_does_not_mutate_input(redactor: SecretRedactor) -> None:
    d = {"api_key": "sk-FAKE"}
    _ = redactor.redact_dict(d)
    assert d["api_key"] == "sk-FAKE"  # caller's dict untouched.


def test_redactor_handles_empty_and_none(redactor: SecretRedactor) -> None:
    assert redactor.redact("") == ""
    assert redactor.redact("plain text without secrets") == "plain text without secrets"


def test_redactor_module_has_nonempty_docstring() -> None:
    from senex import secret_redactor as sr
    assert sr.__doc__ and sr.__doc__.strip() != ""
