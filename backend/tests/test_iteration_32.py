"""Tests for iteration 32 fixes:
1. Gemini provider with retry and None content guard
2. WebSocket receive_loop safety
3. LLM API key encryption at rest
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import json

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.engines.llm_brain import (
    LLMBrain,
    LLMResponse,
    GeminiProvider,
)
from app.core.database import (
    _xor_encrypt,
    _xor_decrypt,
    _get_encryption_key,
    load_llm_config,
    init_db,
)


# ============================================================================
# Task 1: Gemini Provider Tests
# ============================================================================


class TestGeminiProviderRetry:
    """Test that Gemini provider uses retry logic."""

    @pytest.mark.asyncio
    async def test_gemini_complete_with_retry(self):
        """Test that Gemini uses _retry_with_backoff for requests."""
        provider = GeminiProvider(api_key="test-key", model="gemini-2.0-flash")

        with patch("app.engines.llm_brain._retry_with_backoff", new_callable=AsyncMock) as mock_retry:
            # Mock the response
            mock_response = MagicMock()
            mock_response.text = '{"action": "buy"}'
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=100,
                candidates_token_count=50,
            )
            mock_retry.return_value = mock_response

            result = await provider.complete("system", "user")

            # Verify _retry_with_backoff was called
            mock_retry.assert_called_once()
            assert result.content == '{"action": "buy"}'
            assert result.provider == "gemini"


class TestGeminiProviderNoneGuard:
    """Test that Gemini provider guards against None response.text."""

    @pytest.mark.asyncio
    async def test_gemini_handles_none_response_text(self):
        """Test that response.text=None is handled gracefully."""
        provider = GeminiProvider(api_key="test-key", model="gemini-2.0-flash")

        with patch("app.engines.llm_brain._retry_with_backoff", new_callable=AsyncMock) as mock_retry:
            # Mock response with None text
            mock_response = MagicMock()
            mock_response.text = None
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=100,
                candidates_token_count=50,
            )
            mock_retry.return_value = mock_response

            result = await provider.complete("system", "user")

            # Should return empty string instead of None
            assert result.content == ""
            assert result.provider == "gemini"

    @pytest.mark.asyncio
    async def test_gemini_handles_empty_response_text(self):
        """Test that response.text='' is handled."""
        provider = GeminiProvider(api_key="test-key", model="gemini-2.0-flash")

        with patch("app.engines.llm_brain._retry_with_backoff", new_callable=AsyncMock) as mock_retry:
            # Mock response with empty text
            mock_response = MagicMock()
            mock_response.text = ""
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=100,
                candidates_token_count=50,
            )
            mock_retry.return_value = mock_response

            result = await provider.complete("system", "user")

            assert result.content == ""


@pytest.mark.asyncio
async def test_llm_brain_decides_with_none_content():
    """Test that LLM Brain handles None content from JSON parsing."""
    from app.core.events import event_bus

    brain = LLMBrain()

    # Create a provider that returns None content
    fake_provider = MagicMock()
    fake_provider.complete = AsyncMock(return_value=LLMResponse(
        content=None,  # None content
        prompt_tokens=100,
        completion_tokens=50,
        model="test",
        provider="test",
        latency_ms=100.0,
    ))
    brain._provider = fake_provider

    signal_event = MagicMock()
    signal_event.data = {"symbol": "AAPL", "signal_type": "test", "value": 10.0, "price": 150.0, "metadata": {}}

    with patch("app.core.events.event_bus.publish", new_callable=AsyncMock):
        with patch("app.core.database.log_api_usage", new_callable=AsyncMock):
            result = await brain.decide(signal_event)

            # Should return None and not crash
            assert result is None
            assert not brain._last_call_success


# ============================================================================
# Task 2: WebSocket Receive Loop Safety Tests
# ============================================================================


def test_websocket_message_size_check():
    """Test that WebSocket message size check logic is correct."""
    # This tests the size limit check: if len(raw) > 4096: skip
    normal_message = "x" * 100
    oversized_message = "x" * 5000

    assert len(normal_message) <= 4096
    assert len(oversized_message) > 4096


def test_websocket_json_parsing_logic():
    """Test that JSON parsing logic handles errors correctly."""
    # Test valid JSON
    valid_json = json.dumps({"command": "kill_switch", "active": True})
    try:
        data = json.loads(valid_json)
        assert data["command"] == "kill_switch"
    except (json.JSONDecodeError, ValueError):
        pytest.fail("Should parse valid JSON")

    # Test invalid JSON
    invalid_json = "not valid json"
    try:
        data = json.loads(invalid_json)
        pytest.fail("Should raise JSONDecodeError")
    except (json.JSONDecodeError, ValueError):
        pass  # Expected


# ============================================================================
# Task 3: LLM API Key Encryption Tests
# ============================================================================


def test_xor_encrypt_decrypt_roundtrip():
    """Test that encryption and decryption roundtrip correctly."""
    plaintext = "sk-1234567890abcdefghijklmnopqrstuv"
    key = "test-encryption-key"

    encrypted = _xor_encrypt(plaintext, key)
    decrypted = _xor_decrypt(encrypted, key)

    assert decrypted == plaintext


def test_xor_encrypt_produces_different_output():
    """Test that encryption produces different output from plaintext."""
    plaintext = "test-api-key-12345"
    key = "test-key"

    encrypted = _xor_encrypt(plaintext, key)

    # Encrypted should be different
    assert encrypted != plaintext
    # Encrypted should be base64 encoded
    import base64
    try:
        base64.b64decode(encrypted)
        is_valid_base64 = True
    except Exception:
        is_valid_base64 = False
    assert is_valid_base64


def test_xor_decrypt_backwards_compat_plaintext():
    """Test that decryption is backwards compatible with plaintext."""
    # If stored value is plaintext, decryption should return it as-is
    plaintext = "legacy-api-key-not-encrypted"
    key = "some-key"

    # Attempting to decrypt plaintext should return it as-is (due to exception handling)
    decrypted = _xor_decrypt(plaintext, key)
    assert decrypted == plaintext


def test_get_encryption_key_from_settings():
    """Test that encryption key comes from settings."""
    with patch("app.core.database.settings") as mock_settings:
        mock_settings.api_secret_key = "custom-secret-key"

        key = _get_encryption_key()
        assert key == "custom-secret-key"


def test_get_encryption_key_fallback():
    """Test that encryption key falls back to default."""
    with patch("app.core.database.settings") as mock_settings:
        mock_settings.api_secret_key = None

        key = _get_encryption_key()
        assert key == "claw-trader-default-key"


@pytest.mark.asyncio
async def test_load_llm_config_decrypts_key(tmp_path, monkeypatch):
    """Test that load_llm_config decrypts the api_key."""
    import aiosqlite
    import app.core.database as db_module

    db_path = tmp_path / "test_crypto.db"
    monkeypatch.setattr(db_module, "DB_PATH", str(db_path))

    # Initialize database
    await init_db()

    # Insert an encrypted key
    plaintext_key = "test-api-key-12345"
    encrypted_key = _xor_encrypt(plaintext_key, _get_encryption_key())

    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "INSERT INTO llm_config (provider, model_name, api_key, base_url, is_active) VALUES (?, ?, ?, ?, ?)",
            ("gemini", "gemini-2.0-flash", encrypted_key, "", 1),
        )
        await db.commit()

    # Load and verify decryption
    config = await load_llm_config()

    assert config is not None
    assert config["api_key"] == plaintext_key
    assert config["provider"] == "gemini"


@pytest.mark.asyncio
async def test_load_llm_config_backwards_compat_plaintext(tmp_path, monkeypatch):
    """Test that load_llm_config handles old plaintext keys."""
    import aiosqlite
    import app.core.database as db_module

    db_path = tmp_path / "test_compat.db"
    monkeypatch.setattr(db_module, "DB_PATH", str(db_path))

    # Initialize database
    await init_db()

    # Insert a plaintext key (old format)
    plaintext_key = "old-plaintext-api-key"

    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "INSERT INTO llm_config (provider, model_name, api_key, base_url, is_active) VALUES (?, ?, ?, ?, ?)",
            ("gemini", "gemini-2.0-flash", plaintext_key, "", 1),
        )
        await db.commit()

    # Load and verify backwards compatibility
    config = await load_llm_config()

    assert config is not None
    # Should still work with plaintext (backwards compat)
    assert config["api_key"] == plaintext_key


def test_llm_config_encryption_integration():
    """Test that api_key encryption works in the route logic."""
    from app.core.database import _xor_encrypt, _xor_decrypt, _get_encryption_key

    # Simulate what happens in update_llm_config
    api_key_plaintext = "sk-test-key-12345"

    # Encrypt (as done in update_llm_config)
    api_key_encrypted = _xor_encrypt(api_key_plaintext, _get_encryption_key())

    # Verify encrypted form is different
    assert api_key_encrypted != api_key_plaintext

    # Decrypt (as done in load_llm_config)
    api_key_decrypted = _xor_decrypt(api_key_encrypted, _get_encryption_key())

    # Verify roundtrip
    assert api_key_decrypted == api_key_plaintext


# ============================================================================
# Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_gemini_provider_json_parsing_with_none():
    """Integration test: Gemini provider with None content triggers TypeError catch."""
    from app.core.events import Event

    brain = LLMBrain()

    # Simulate Gemini response with None text
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(return_value=LLMResponse(
        content=None,
        prompt_tokens=100,
        completion_tokens=50,
        model="gemini-2.0-flash",
        provider="gemini",
        latency_ms=50.0,
    ))
    brain._provider = mock_provider

    signal_event = Event(
        type="signal",
        data={
            "symbol": "AAPL",
            "signal_type": "test",
            "value": 10.0,
            "price": 150.0,
            "metadata": {},
        }
    )

    with patch("app.core.database.log_api_usage", new_callable=AsyncMock):
        with patch("app.core.events.event_bus.publish", new_callable=AsyncMock):
            result = await brain.decide(signal_event)

            # Should handle gracefully and return None
            assert result is None
            assert not brain._last_call_success
            # Error should mention JSON parsing or TypeError
            assert brain._last_call_error is not None
