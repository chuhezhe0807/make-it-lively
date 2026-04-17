"""Environment configuration bootstrap + runtime toggles.

Loading order matters: this module must be imported before any router or SDK
client is instantiated, because `anthropic.Anthropic()` / `replicate.Client()`
read their credentials from `os.environ` at construction time. Importing this
module first ensures variables from `backend/.env` are merged into the process
environment before any handler fires.

The helper functions at the bottom centralize runtime toggles (base URLs,
fallback mode) so routers don't sprinkle `os.environ[...]` reads throughout
the codebase.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# `.env` lives next to pyproject.toml (one level above the `app/` package).
# We resolve the path explicitly so `uvicorn` launched from any working
# directory still finds the file. `override=False` means real environment
# variables (e.g. ones exported in the shell or injected by CI) win over
# whatever is written in the file.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _BACKEND_ROOT / ".env"

load_dotenv(dotenv_path=_ENV_FILE, override=False)


# --- Runtime toggles -------------------------------------------------------


def anthropic_base_url() -> str | None:
    """Return ``ANTHROPIC_BASE_URL`` when set, else ``None``.

    Used by routers that build an ``anthropic.Anthropic`` client so traffic
    can be routed through a proxy/gateway (OneAPI, an internal reverse proxy,
    etc.). When unset, the SDK falls back to its default public endpoint.
    """
    value = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    return value or None


def use_replicate_fallback() -> bool:
    """Return True when Replicate-backed endpoints should use Pillow fallbacks.

    Enabled when either:
      * ``USE_REPLICATE_FALLBACK`` is set to ``1`` / ``true`` / ``yes``
        (explicit opt-in — useful for local dev and CI), or
      * ``REPLICATE_API_TOKEN`` is missing (auto — no way to reach Replicate).

    Fallbacks are intentionally lower quality (rectangular bbox crops for
    segmentation, Gaussian-blur fill for inpainting) but let the whole
    pipeline run end-to-end on Claude alone.
    """
    flag = os.environ.get("USE_REPLICATE_FALLBACK", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    token = os.environ.get("REPLICATE_API_TOKEN", "").strip()
    return not token
