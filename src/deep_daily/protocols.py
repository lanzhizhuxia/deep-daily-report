from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMBackend(Protocol):

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        temperature: float = 0.3,
        max_tokens: int = 8192,
    ) -> str: ...


@runtime_checkable
class Publisher(Protocol):

    def publish(
        self,
        date_str: str,
        html_path: str,
        one_liner: str,
        topic_titles: list[str],
        panorama_md: str,
        **kwargs: Any,
    ) -> bool: ...
