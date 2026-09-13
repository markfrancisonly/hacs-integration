"""Binary sensor entities for HACS."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base import HacsBase
from .const import DOMAIN, HACS_SYSTEM_ID
from .entity import HacsSystemEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup binary sensor platform."""
    hacs: HacsBase = hass.data[DOMAIN]
    async_add_entities([HacsRestartRequiredBinarySensor(hacs=hacs)])


class HacsRestartRequiredBinarySensor(HacsSystemEntity, BinarySensorEntity):
    """On while a downloaded integration is waiting for a restart.

    This fork suppresses the restart_required repair; this entity carries the
    same information as state instead. pending_restart only ever lives in
    memory, so a restart clears it.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_icon = None  # let the device class pick the state icon
    _attr_name = "Restart required"
    _attr_unique_id = f"{HACS_SYSTEM_ID}_restart_required"

    @property
    def _pending(self) -> list[str]:
        """Return the downloaded repositories waiting for a restart."""
        return sorted(
            repository.data.full_name
            for repository in self.hacs.repositories.list_downloaded
            if repository.pending_restart
        )

    @property
    def is_on(self) -> bool:
        """Return True while any downloaded repository needs a restart."""
        return bool(self._pending)

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        """Return which repositories are waiting."""
        return {"repositories": self._pending}
