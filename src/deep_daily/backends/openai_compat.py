from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class OpenAICompatibleBackend:

    def __init__(
        self,
        api_base: str | None = None,
        api_key: str | None = None,
        timeout: int = 180,
    ) -> None:
        self._api_base = (api_base or os.environ.get("LLM_API_BASE", "")).rstrip("/")
        self._api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self._timeout = timeout

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        temperature: float = 0.3,
        max_tokens: int = 8192,
    ) -> str:
        if not self._api_base:
            raise RuntimeError("LLM_API_BASE not configured")

        payload = json.dumps({
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode("utf-8")

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self._api_base}/chat/completions"
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        req = urllib.request.Request(url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")[:500]
            except Exception:
                pass
            raise RuntimeError(
                f"LLM HTTP {e.code} model={model}: {body}"
            ) from e
