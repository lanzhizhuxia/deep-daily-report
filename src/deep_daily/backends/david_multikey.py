"""Deprecated: use ``deep_daily.backends.litellm_multikey`` instead.

This shim exists solely so existing imports like::

    from deep_daily.backends.david_multikey import DavidMultiKeyBackend

keep working through v0.3.x. Remove in v0.4.0.
"""

from __future__ import annotations

import warnings

from deep_daily.backends.litellm_multikey import (  # noqa: F401
    DavidMultiKeyBackend,
    LiteLLMMultiKeyBackend,
)

warnings.warn(
    "deep_daily.backends.david_multikey is deprecated; "
    "import from deep_daily.backends.litellm_multikey instead.",
    DeprecationWarning,
    stacklevel=2,
)
