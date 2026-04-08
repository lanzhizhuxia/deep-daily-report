from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request


_KEY_BLOCK_UNTIL: dict[str, float] = {}
_KEY_LOCK = threading.Lock()


def _split_keys(v: str) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _mask_key(k: str) -> str:
    return f"{k[:8]}...{k[-4:]}" if len(k) > 12 else "***"


def _keys_for_model(model: str) -> list[str]:
    if model.startswith("google/"):
        keys = _split_keys(os.environ.get("LITELLM_API_KEYS_GOOGLE", ""))
        if keys:
            return keys
    keys = _split_keys(os.environ.get("LITELLM_API_KEYS", ""))
    if keys:
        return keys
    one = os.environ.get("LITELLM_API_KEY", "").strip()
    return [one] if one else []


def _classify_http_error(err: urllib.error.HTTPError) -> tuple[str, int, str]:
    try:
        body = err.read().decode("utf-8", errors="ignore")
    except Exception:
        body = str(err)
    try:
        obj = json.loads(body)
        msg = str((obj.get("error") or {}).get("message") or body)
    except Exception:
        msg = body
    low = msg.lower()
    if "budget has been exceeded" in low or ("budget" in low and "exceed" in low):
        return "budget_exceeded", err.code, msg
    if "model" in low and any(
        w in low for w in ("not allowed", "not found", "permission", "access")
    ):
        return "model_denied", err.code, msg
    if err.code in (401, 403):
        return "auth_denied", err.code, msg
    return "other", err.code, msg


def _is_key_blocked(key: str) -> bool:
    with _KEY_LOCK:
        return _KEY_BLOCK_UNTIL.get(key, 0) > time.time()


def _mark_key_budget_block(key: str) -> None:
    sec = int(os.environ.get("LITELLM_BUDGET_BLOCK_SECONDS", "21600"))
    with _KEY_LOCK:
        _KEY_BLOCK_UNTIL[key] = time.time() + max(sec, 60)
    print(
        f"  LiteLLM: key {_mask_key(key)} budget_exceeded, blocked for {sec}s",
        file=sys.stderr,
    )


class DavidMultiKeyBackend:
    def __init__(self, *, api_base: str | None = None, timeout: int = 180) -> None:
        self._api_base = (api_base or os.environ.get("LITELLM_API_BASE", "")).rstrip("/")
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
            raise RuntimeError("Missing LITELLM_API_BASE")
        keys_all = _keys_for_model(model)
        if not keys_all:
            raise RuntimeError("Missing LITELLM_API_KEYS / LITELLM_API_KEY")

        keys = [k for k in keys_all if not _is_key_blocked(k)] or list(keys_all)
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        ).encode("utf-8")

        errs: list[str] = []
        for key in keys:
            req = urllib.request.Request(
                f"{self._api_base}/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    result = json.loads(resp.read())
                return result["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                reason, status, _msg = _classify_http_error(e)
                if reason == "budget_exceeded":
                    _mark_key_budget_block(key)
                    errs.append(f"{_mask_key(key)} budget_exceeded({status})")
                    continue
                if reason in ("model_denied", "auth_denied") or status in (
                    429,
                    500,
                    502,
                    503,
                    504,
                ):
                    errs.append(f"{_mask_key(key)} {reason}({status})")
                    continue
                raise
            except Exception as exc:
                errs.append(f"{_mask_key(key)} err:{exc}")
                continue

        raise RuntimeError(
            f"All LiteLLM keys failed for model={model}. attempts=[{'; '.join(errs)}]"
        )
