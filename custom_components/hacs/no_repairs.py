"""Repair and notification helpers that honour the auto update option.

With the auto update option on, HACS installs updates as soon as they are
known and reports a pending restart through the "Restart required" binary
sensor, so the restart_required repair, the "removed from HACS" repair and the
critical-repository notification are not raised (HACS logs each of them). With
the option off the real Home Assistant helpers are called, as upstream does.

Imported in place of the real helpers by base.py and repositories/integration.py.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.persistent_notification import (
    async_create as _async_create_persistent_notification,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue as _async_create_issue,
)

from .const import DOMAIN

__all__ = [
    "IssueSeverity",
    "async_create_issue",
    "async_create_persistent_notification",
]


def _auto_update(hass: HomeAssistant) -> bool:
    """Whether the auto update option is on."""
    hacs = hass.data.get(DOMAIN)
    return bool(hacs is not None and hacs.configuration is not None and hacs.configuration.auto_update)


def async_create_issue(hass: HomeAssistant, *args: Any, **kwargs: Any) -> None:
    """Create a repair issue, unless auto update handles it."""
    if _auto_update(hass):
        return
    _async_create_issue(hass, *args, **kwargs)


def async_create_persistent_notification(hass: HomeAssistant, *args: Any, **kwargs: Any) -> None:
    """Create a persistent notification, unless auto update handles it."""
    if _auto_update(hass):
        return
    _async_create_persistent_notification(hass, *args, **kwargs)
