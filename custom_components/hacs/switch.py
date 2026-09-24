"""Switch entities for HACS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .base import HacsBase
from .const import DOMAIN, HACS_SYSTEM_ID
from .entity import HacsRepositoryEntity, HacsSystemEntity
from .repositories.base import HacsRepository


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup switch platform."""
    hacs: HacsBase = hass.data[DOMAIN]
    async_add_entities(
        [
            HacsAutoUpdateSwitchEntity(hacs=hacs),
            *(
                HacsRepositoryPreReleaseSwitchEntity(hacs=hacs, repository=repository)
                for repository in hacs.repositories.list_downloaded
            ),
        ]
    )


class HacsAutoUpdateSwitchEntity(HacsSystemEntity, SwitchEntity, RestoreEntity):
    """Whether HACS installs updates unattended.

    While on, every update entity downloads a new version as soon as HACS
    learns of it, so nothing waits in Settings > Updates; what remains is the
    restart, reported by the "Restart required" binary sensor. State is
    restored across restarts and defaults to on.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_icon = "mdi:autorenew"
    _attr_is_on = True
    _attr_name = "Auto update"
    _attr_unique_id = f"{HACS_SYSTEM_ID}_auto_update"

    async def async_added_to_hass(self) -> None:
        """Restore the last state."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._attr_is_on = last_state.state == STATE_ON
        self.hacs.system.auto_update = self._attr_is_on
        if self._attr_is_on:
            self._nudge_update_entities()

    @callback
    def _nudge_update_entities(self) -> None:
        """Let the update entities act on anything already pending."""
        for coordinator in self.hacs.coordinators.values():
            coordinator.async_update_listeners()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Install updates unattended, starting with anything pending."""
        self._attr_is_on = True
        self.hacs.system.auto_update = True
        self.async_write_ha_state()
        self._nudge_update_entities()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Hold unattended updates."""
        self._attr_is_on = False
        self.hacs.system.auto_update = False
        self.async_write_ha_state()


class HacsRepositoryPreReleaseSwitchEntity(HacsRepositoryEntity, SwitchEntity):
    """Pre-release switch entities for repositories downloaded with HACS."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "pre-release"

    def __init__(self, hacs: HacsBase, repository: HacsRepository) -> None:
        """Initialize the repository pre-release switch."""
        super().__init__(hacs, repository)
        self._attr_entity_registry_enabled_default = self.repository.data.show_beta

    @property
    def is_on(self) -> bool:
        """Return if the pre-release option is enabled for the repository."""
        return self.repository.data.show_beta

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self._handle_change(value=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._handle_change(value=False)

    async def _handle_change(self, value: bool) -> None:
        """Handle attribute value changes."""
        self.repository.data.show_beta = value

        # As this value is directly affecting what data points is in use by other entities
        # we need to update all entities to reflect the change
        # Do force an update of the entities we need to clear the last fetched data
        # since that is used to limit state updates
        # Once we have signaled the update we can restore the last fetched data
        _last_fetch = self.repository.data.last_fetched
        self.repository.data.last_fetched = None
        self.coordinator.async_update_listeners()
        self.repository.data.last_fetched = _last_fetch  # Restore last fetched

        # Write the HACS data and update the entity state
        await self.hacs.data.async_write()
        self.async_write_ha_state()
