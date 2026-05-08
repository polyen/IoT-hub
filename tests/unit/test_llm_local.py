from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from hub.edge.agent.llm_local import LocalLLMClient


def make_mock_response(body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = body
    return resp


def make_mock_client(response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response)
    client.get = AsyncMock(return_value=response)
    return client


# ── 1: generate() returns content string from mocked response ─────────────────


@pytest.mark.asyncio
async def test_generate_returns_content_string() -> None:
    resp = make_mock_response({"content": "lights are on"})
    mock_client = make_mock_client(resp)

    with patch("hub.edge.agent.llm_local.httpx.AsyncClient", return_value=mock_client):
        llm = LocalLLMClient()
        result = await llm.generate("Turn on the lights")

    assert result == "lights are on"
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args[0][0].endswith("/completion")
    payload = call_args[1]["json"]
    assert payload["prompt"] == "Turn on the lights"
    assert payload["stream"] is False


# ── 2: generate_constrained() returns parsed dict ─────────────────────────────


@pytest.mark.asyncio
async def test_generate_constrained_returns_parsed_dict() -> None:
    body = json.dumps({"topic": "home/kitchen/light", "payload": {"state": "on"}})
    resp = make_mock_response({"content": body})
    mock_client = make_mock_client(resp)

    with patch("hub.edge.agent.llm_local.httpx.AsyncClient", return_value=mock_client):
        llm = LocalLLMClient()
        result = await llm.generate_constrained("Turn on kitchen light", grammar="root ::= ...")

    assert result["topic"] == "home/kitchen/light"
    assert result["payload"] == {"state": "on"}
    payload = mock_client.post.call_args[1]["json"]
    assert "grammar" in payload
    assert payload["temperature"] == 0.0


# ── 3: generate_constrained() raises on invalid JSON from LLM ─────────────────


@pytest.mark.asyncio
async def test_generate_constrained_raises_on_invalid_json() -> None:
    resp = make_mock_response({"content": "not valid json {{{"})
    mock_client = make_mock_client(resp)

    with patch("hub.edge.agent.llm_local.httpx.AsyncClient", return_value=mock_client):
        llm = LocalLLMClient()
        with pytest.raises(json.JSONDecodeError):
            await llm.generate_constrained("something", grammar="root ::= ...")


# ── 4: health() returns True when server responds 200 ─────────────────────────


@pytest.mark.asyncio
async def test_health_returns_true_on_200() -> None:
    resp = MagicMock()
    resp.status_code = 200
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=resp)

    with patch("hub.edge.agent.llm_local.httpx.AsyncClient", return_value=mock_client):
        llm = LocalLLMClient()
        result = await llm.health()

    assert result is True
    mock_client.get.assert_called_once()
    assert mock_client.get.call_args[0][0].endswith("/health")


# ── 5: health() returns False when server unreachable ─────────────────────────


@pytest.mark.asyncio
async def test_health_returns_false_when_unreachable() -> None:
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with patch("hub.edge.agent.llm_local.httpx.AsyncClient", return_value=mock_client):
        llm = LocalLLMClient()
        result = await llm.health()

    assert result is False
