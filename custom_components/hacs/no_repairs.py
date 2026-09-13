"""Drop-in no-ops for the Home Assistant repair and notification helpers.

This fork suppresses HACS's UI repairs and persistent notifications. Nothing is
lost: HACS already logs every one of these events (restart required, repository
removed from HACS, critical repository) through its own logger, so the
information stays available in the Home Assistant log.

Imported in place of the real helpers by base.py and repositories/integration.py.
"""

from __future__ import annotations

from typing import Any

# Re-exported so call sites can keep passing a real severity value.
from homeassistant.helpers.issue_registry import IssueSeverity

__all__ = [
    "IssueSeverity",
    "async_create_issue",
    "async_create_persistent_notification",
]


def async_create_issue(*_args: Any, **_kwargs: Any) -> None:
    """Swallow a repair issue. HACS logs the same event."""


def async_create_persistent_notification(*_args: Any, **_kwargs: Any) -> None:
    """Swallow a persistent notification. HACS logs the same event."""
