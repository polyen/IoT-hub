"""Local LLM client — connects to llama-cpp-python HTTP server.

The server runs Qwen 2.5 1.5B-Instruct Q4_K_M GGUF in a separate container
(see hub/edge/agent/Dockerfile.llm and docker-compose.edge.yml).  This module
is a thin async HTTP client with structured output support (GBNF grammar).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_LLM_URL = "http://localhost:8001"
DEFAULT_MAX_TOKENS = 256
DEFAULT_TEMPERATURE = 0.0  # deterministic for tool calls
# Qwen 2.5 1.5B on RPi5 should hit 7-12 tok/s in isolation; observed 2-3 tok/s
# under CPU contention with CV/STT.  360s buffer accommodates worst-case cold
# starts plus contention; if you reliably need more, the LLM is the wrong tool
# — switch to scene engine (research §3 D) or smaller draft model.
DEFAULT_TIMEOUT_SEC = 360.0


class LocalLLMClient:
    def __init__(
        self, base_url: str = DEFAULT_LLM_URL, timeout: float = DEFAULT_TIMEOUT_SEC
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def generate(
        self,
        prompt: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        stop: list[str] | None = None,
    ) -> str:
        """Generate text from prompt. Returns completion string."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            payload: dict[str, Any] = {
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            }
            if stop:
                payload["stop"] = stop
            resp = await client.post(f"{self._base_url}/v1/completions", json=payload)
            if resp.status_code >= 400:
                # llama-cpp-server returns a JSON error body — surface it so the
                # caller's log shows the real cause instead of a bare HTTPStatusError.
                raise httpx.HTTPStatusError(
                    f"LLM /v1/completions HTTP {resp.status_code}: {resp.text[:500]}",
                    request=resp.request,
                    response=resp,
                )
            data = resp.json()
            # OpenAI-compatible response: choices[0].text
            choices = data.get("choices")
            if choices:
                return str(choices[0].get("text", ""))
            return str(data.get("content", ""))

    async def generate_constrained(
        self,
        prompt: str,
        grammar: str,  # GBNF grammar string
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> dict[str, Any]:
        """Generate JSON using GBNF grammar constraint. Returns parsed dict."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            payload: dict[str, Any] = {
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": 0.0,
                "grammar": grammar,
                "stream": False,
            }
            resp = await client.post(f"{self._base_url}/v1/completions", json=payload)
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"LLM /v1/completions HTTP {resp.status_code}: {resp.text[:500]}",
                    request=resp.request,
                    response=resp,
                )
            data = resp.json()
            choices = data.get("choices")
            content = (
                str(choices[0].get("text", "{}")) if choices else str(data.get("content", "{}"))
            )
            return dict(json.loads(content))

    async def generate_chat(
        self,
        system: str,
        user: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        json_mode: bool = False,
    ) -> str:
        """Generate using the chat completions API.

        json_mode=True sets response_format=json_object so the model is
        grammar-constrained to emit valid JSON (requires the prompt to
        mention JSON explicitly so the model knows what to produce).
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            payload: dict[str, Any] = {
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            resp = await client.post(f"{self._base_url}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return ""
            return str(choices[0].get("message", {}).get("content", ""))

    async def health(self) -> bool:
        """Return True if LLM server is reachable."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self._base_url}/v1/models")
                return resp.status_code == 200
        except Exception:
            return False
