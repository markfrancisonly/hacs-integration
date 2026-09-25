"""Run unattended updates through Home Assistant's normal update service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from homeassistant.components.update import DOMAIN, SERVICE_INSTALL
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

if TYPE_CHECKING:
    from .base import HacsBase
    from .update import HacsRepositoryUpdateEntity


class HacsAutoUpdate:
    """Serialize automatic installs and drain them before unloading HACS.

    HACS's installer shares its backup directory across repositories. Only one
    automatic install may run at a time, and an active install must finish rather
    than be cancelled halfway through replacing files.
    """

    def __init__(self, hacs: HacsBase) -> None:
        self.hacs = hacs
        self._entities: dict[HacsRepositoryUpdateEntity, None] = {}
        self._pending: dict[HacsRepositoryUpdateEntity, None] = {}
        self._active: HacsRepositoryUpdateEntity | None = None
        self._active_done: asyncio.Future[None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._pauses = 0
        self._stopped = False

    @callback
    def async_register(self, entity: HacsRepositoryUpdateEntity) -> None:
        """Register an entity and consider its restored update state."""
        self._entities[entity] = None
        self.async_request(entity)

    @callback
    def async_discard(self, entity: HacsRepositoryUpdateEntity) -> None:
        """Discard queued work as soon as an entity starts being removed."""
        self._entities.pop(entity, None)
        self._pending.pop(entity, None)

    def _eligible(self, entity: HacsRepositoryUpdateEntity) -> bool:
        return (
            not self._stopped
            and not self._pauses
            and self.hacs.configuration.auto_update
            and self.hacs.system.auto_update
            and not self.hacs.system.disabled
            and entity in self._entities
            and entity.available
            and entity.in_progress is False
            and entity.repository.pending_update
            # Home Assistant's state includes the user's skipped version.
            and entity.state == STATE_ON
        )

    @callback
    def async_request(self, entity: HacsRepositoryUpdateEntity) -> None:
        """Queue each eligible entity at most once."""
        if entity is self._active or not self._eligible(entity):
            return
        self._pending[entity] = None
        if self._task is None:
            self._task = self.hacs.hass.async_create_task(
                self._async_run(), "HACS auto update", eager_start=False
            )

    async def _async_run(self) -> None:
        try:
            while self._pending:
                entity = next(iter(self._pending))
                self._pending.pop(entity)
                # A skip, removal, switch change or manual update may have
                # happened while this entity waited for an earlier install.
                if not self._eligible(entity):
                    continue
                self._active = entity
                self._active_done = self.hacs.hass.loop.create_future()
                try:
                    await self.hacs.hass.services.async_call(
                        DOMAIN,
                        SERVICE_INSTALL,
                        {ATTR_ENTITY_ID: entity.entity_id},
                        blocking=True,
                    )
                except HomeAssistantError as exception:
                    self.hacs.log.warning(
                        "Auto update of %s failed: %s", entity.repository.data.full_name, exception
                    )
                finally:
                    self._active_done.set_result(None)
                    self._active_done = None
                    self._active = None
        finally:
            self._task = None

    async def async_remove(self, entity: HacsRepositoryUpdateEntity) -> None:
        """Finish an active install before entity removal completes."""
        self.async_discard(entity)
        if entity is self._active and self._active_done is not None:
            await asyncio.shield(self._active_done)

    async def _async_drain(self) -> None:
        self._pending.clear()
        if self._task is not None:
            await asyncio.shield(self._task)

    @asynccontextmanager
    async def async_paused(self) -> AsyncIterator[None]:
        """Drain installs before recreating entities, then reconsider updates."""
        self._pauses += 1
        try:
            await self._async_drain()
            yield
        finally:
            self._pauses -= 1
            for entity in self._entities:
                self.async_request(entity)

    async def async_stop(self) -> None:
        """Stop accepting work and finish the current install before unloading."""
        self._stopped = True
        await self._async_drain()
