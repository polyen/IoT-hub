"""Local LLM client — connects to llama-cpp-python HTTP server.

The server runs Qwen 3.5 4B Q4_K_M GGUF in a separate container.
This module is a thin async HTTP client with structured output support.
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


class LocalLLMClient:
    def __init__(self, base_url: str = DEFAULT_LLM_URL, timeout: float = 90.0) -> None:
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
            resp.raise_for_status()
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
            resp.raise_for_status()
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
    ) -> str:
        """Generate with Qwen3 thinking disabled via completions-API prefill.

        Uses manually formatted Qwen3 chat template with an empty <think></think>
        block pre-filled as the start of the assistant turn.  The completions API
        strips thinking tokens from its output, so the returned text is pure answer.
        Prefilling '{' forces the model to continue directly with the JSON object.
        """
        prompt = (
            f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n<think>\n\n</think>\n{{"
        )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            payload: dict[str, Any] = {
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stop": ["<|im_end|>", "<|im_start|>"],
                "stream": False,
            }
            resp = await client.post(f"{self._base_url}/v1/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return ""
            # Prepend the '{' we used as prefill so the caller gets full JSON
            return "{" + str(choices[0].get("text", ""))

    async def health(self) -> bool:
        """Return True if LLM server is reachable."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self._base_url}/v1/models")
                return resp.status_code == 200
        except Exception:
            return False
