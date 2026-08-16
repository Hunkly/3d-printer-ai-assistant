"""``system.*`` MCP tools: metadata, environment, health."""

from __future__ import annotations

import platform

from print_engineer import __version__


def system_info() -> dict[str, object]:
    """Project metadata: name, version, runtime platform."""
    return {
        "name": "print-engineer",
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def system_health() -> dict[str, object]:
    """Health probe; returns ``{"status": "ok"}`` when serving."""
    return {"status": "ok", "version": __version__}
