"""Deep Daily Report — configurable daily news digest pipeline.

Each deploy has its own HOME directory (``deep-daily init <path>``) holding
its config, data, caches, and outputs. The tool repo itself is stateless:
code + templates + docs only.

Extension points:
    - LLMBackend: Pluggable LLM provider (default: OpenAI-compatible HTTP)
    - Publisher: Pluggable publish channel (default: file-only)
"""

from __future__ import annotations

__version__ = "0.3.0-dev"
