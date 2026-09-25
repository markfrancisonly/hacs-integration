"""Exercise the fork through real Home Assistant update services.

Keep fork-only scenarios separate from upstream's GitHub snapshot fixtures.
Run with: python -m unittest discover -s tests_fork -v
"""

# This suite intentionally uses the standard-library runner, including its assertions.
# ruff: noqa: PT009

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

from awesomeversion import AwesomeVersion
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.setup import async_setup_component

from custom_components.hacs import async_unload_entry
from custom_components.hacs.base import HacsBase
from custom_components.hacs.const import DOMAIN
from custom_components.hacs.coordinator import HacsUpdateCoordinator
from custom_components.hacs.enums import HacsCategory, HacsGitHubRepo
from custom_components.hacs.exceptions import HacsException
from custom_components.hacs.repositories.integration import HacsIntegrationRepository
from custom_components.hacs.switch import HacsAutoUpdateSwitchEntity
from custom_components.hacs.update import HacsRepositoryUpdateEntity
from custom_components.hacs.utils.backup import Backup
from custom_components.hacs.utils.queue_manager import QueueManager

if AwesomeVersion(HA_VERSION) > "2025.3.0":
    from tests.homeassistantfixtures.dev import async_test_home_assistant
else:
    from tests.homeassistantfixtures.min import async_test_home_assistant


class TestAutoUpdate(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hass = await self.enterAsyncContext(
            async_test_home_assistant(config_dir=self.temp.name)
        )
        self.assertTrue(await async_setup_component(self.hass, "update", {}))
        # Device registration is unrelated to the update service under test.
        self.enterContext(
            patch.object(
                HacsRepositoryUpdateEntity,
                "device_info",
                new_callable=PropertyMock,
                return_value=None,
            )
        )
        self.hacs = HacsBase()
        self.hacs.hass = self.hass
        self.hacs.core.config_path = self.temp.name
        self.hacs.core.ha_version = AwesomeVersion(HA_VERSION)
        self.hacs.version = AwesomeVersion("2026.9.25.4")
        self.hacs.configuration.auto_update = True
        self.hacs.queue = QueueManager(self.hass)
        self.hacs.data = Mock(async_write=AsyncMock())
        self.hacs.coordinators[HacsCategory.INTEGRATION] = HacsUpdateCoordinator()
        self.hass.data[DOMAIN] = self.hacs
        self.addAsyncCleanup(self.cleanup_hass)

    async def cleanup_hass(self):
        await self.hacs.auto_update.async_stop()
        await self.hass.async_stop(force=True)

    async def add_repository(self, name):
        repository = HacsIntegrationRepository(self.hacs, f"test/{name}")
        repository.data.id = name
        repository.data.domain = name
        repository.data.installed = True
        repository.data.releases = True
        repository.data.installed_version = "1.0.0"
        repository.data.last_version = "2.0.0"
        self.hacs.repositories.register(repository)

        async def download(*, ref):
            repository.data.installed_version = ref

        repository.async_download_repository = AsyncMock(side_effect=download)
        entity = HacsRepositoryUpdateEntity(self.hacs, repository)
        await self.hass.data["update"].async_add_entities([entity])
        self.assertIsNotNone(entity.entity_id)
        return entity

    def enable(self):
        self.hacs.system.auto_update = True
        self.hacs.coordinators[HacsCategory.INTEGRATION].async_update_listeners()

    async def finish(self):
        await asyncio.wait_for(self.hass.async_block_till_done(), 5)

    async def hold_install(self, entity):
        started, release = asyncio.Event(), asyncio.Event()
        self.addCleanup(release.set)

        async def download(*, ref):
            started.set()
            await release.wait()
            entity.repository.data.installed_version = ref

        entity.repository.async_download_repository.side_effect = download
        return started, release

    async def test_normal_install_action_and_no_duplicate_download(self):
        entity = await self.add_repository("normal")
        with patch.object(
            type(self.hass.services), "async_call", wraps=self.hass.services.async_call
        ) as calls:
            self.enable()
            self.enable()
            await self.finish()
        self.assertEqual(entity.installed_version, "2.0.0")
        entity.repository.async_download_repository.assert_awaited_once_with(ref="2.0.0")
        calls.assert_any_call("update", "install", {"entity_id": entity.entity_id}, blocking=True)
        self.enable()
        await self.finish()
        self.assertEqual(entity.repository.async_download_repository.await_count, 1)

    async def test_installs_are_serial_and_preserve_rollback(self):
        first = await self.add_repository("first")
        second = await self.add_repository("second")
        started, release = asyncio.Event(), asyncio.Event()
        self.addCleanup(release.set)
        target = Path(first.repository.localpath)
        target.mkdir(parents=True)
        original = target / "original.py"
        original.write_text("working integration")
        backup = Backup(self.hacs, local_path=str(target))

        async def failing_download(*, ref):
            backup.create()
            started.set()
            await release.wait()
            backup.restore()
            backup.cleanup()
            raise HacsException("simulated download failure")

        first.repository.async_download_repository.side_effect = failing_download
        self.enable()
        await asyncio.wait_for(started.wait(), 5)
        self.enable()  # Repeated coordinator notifications must not duplicate work.
        second.repository.async_download_repository.assert_not_awaited()
        self.assertTrue(Path(backup.backup_path_full).exists())
        release.set()
        with self.assertLogs("custom_components.hacs", level="WARNING"):
            await self.finish()
        self.assertEqual(original.read_text(), "working integration")
        self.assertEqual(first.repository.async_download_repository.await_count, 1)
        second.repository.async_download_repository.assert_awaited_once()

    async def test_skip_is_respected(self):
        entity = await self.add_repository("skipped")
        await self.hass.services.async_call(
            "update", "skip", {"entity_id": entity.entity_id}, blocking=True
        )
        self.enable()
        await self.finish()
        entity.repository.async_download_repository.assert_not_awaited()
        self.assertEqual(entity.state, "off")
        entity.repository.data.last_version = "3.0.0"
        self.enable()
        await self.finish()
        entity.repository.async_download_repository.assert_awaited_once_with(ref="3.0.0")

    async def test_skip_is_rechecked_after_waiting(self):
        first = await self.add_repository("first")
        second = await self.add_repository("second")
        started, release = await self.hold_install(first)
        self.enable()
        await asyncio.wait_for(started.wait(), 5)
        await self.hass.services.async_call(
            "update", "skip", {"entity_id": second.entity_id}, blocking=True
        )
        release.set()
        await self.finish()
        second.repository.async_download_repository.assert_not_awaited()

    async def test_switch_off_drops_waiting_installs_and_on_reconsiders(self):
        first = await self.add_repository("first")
        second = await self.add_repository("second")
        started, release = await self.hold_install(first)
        switch = HacsAutoUpdateSwitchEntity(self.hacs)
        switch.async_write_ha_state = Mock()
        await switch.async_turn_on()
        await asyncio.wait_for(started.wait(), 5)
        await switch.async_turn_off()
        release.set()
        await self.finish()
        second.repository.async_download_repository.assert_not_awaited()
        await switch.async_turn_on()
        await self.finish()
        second.repository.async_download_repository.assert_awaited_once()

    async def test_option_disabled_leaves_manual_install_working(self):
        entity = await self.add_repository("manual")
        self.hacs.configuration.auto_update = False
        self.enable()
        await self.finish()
        entity.repository.async_download_repository.assert_not_awaited()
        await self.hass.services.async_call(
            "update", "install", {"entity_id": entity.entity_id}, blocking=True
        )
        entity.repository.async_download_repository.assert_awaited_once_with(ref="2.0.0")

    async def test_unload_waits_for_active_install_and_discards_queue(self):
        first = await self.add_repository("first")
        second = await self.add_repository("second")
        started, release = await self.hold_install(first)
        self.enable()
        await asyncio.wait_for(started.wait(), 5)
        with patch.object(
            self.hass.config_entries, "async_unload_platforms", return_value=True
        ) as unload:
            task = asyncio.create_task(async_unload_entry(self.hass, Mock()))
            await asyncio.sleep(0)
            unload.assert_not_awaited()
            self.assertFalse(task.done())
            release.set()
            self.assertTrue(await asyncio.wait_for(task, 5))
        self.assertEqual(first.installed_version, "2.0.0")
        second.repository.async_download_repository.assert_not_awaited()
        self.hacs.auto_update.async_request(second)
        await self.finish()
        second.repository.async_download_repository.assert_not_awaited()

    async def test_entity_removal_waits_and_discards_queued_entity(self):
        first = await self.add_repository("first")
        second = await self.add_repository("second")
        started, release = await self.hold_install(first)
        self.enable()
        await asyncio.wait_for(started.wait(), 5)
        await second.async_remove()
        remove = asyncio.create_task(first.async_remove())
        await asyncio.sleep(0)
        self.assertFalse(remove.done())
        release.set()
        await asyncio.wait_for(remove, 5)
        await self.finish()
        second.repository.async_download_repository.assert_not_awaited()

    async def test_recreating_entities_drains_and_resumes_updates(self):
        first = await self.add_repository("first")
        second = await self.add_repository("second")
        started, release = await self.hold_install(first)
        self.enable()
        await asyncio.wait_for(started.wait(), 5)

        async def unload_entities(*args, **kwargs):
            await first.async_remove()
            await second.async_remove()
            return True

        async def add_replacements(*args, **kwargs):
            await self.hass.data["update"].async_add_entities(
                [
                    HacsRepositoryUpdateEntity(self.hacs, entity.repository)
                    for entity in (first, second)
                ]
            )

        with (
            patch.object(
                self.hass.config_entries, "async_unload_platforms", side_effect=unload_entities
            ) as unload,
            patch.object(
                self.hass.config_entries, "async_forward_entry_setups", side_effect=add_replacements
            ) as setup,
        ):
            recreate = asyncio.create_task(self.hacs.async_recreate_entities())
            await asyncio.sleep(0)
            unload.assert_not_awaited()
            release.set()
            await asyncio.wait_for(recreate, 5)
            unload.assert_awaited_once()
            setup.assert_awaited_once()
        await self.finish()
        second.repository.async_download_repository.assert_awaited_once()

    async def test_completed_manual_update_is_not_reinstalled_from_queue(self):
        first = await self.add_repository("first")
        second = await self.add_repository("second")
        started, release = await self.hold_install(first)
        self.enable()
        await asyncio.wait_for(started.wait(), 5)
        second.repository.data.installed_version = second.latest_version
        release.set()
        await self.finish()
        second.repository.async_download_repository.assert_not_awaited()

    async def test_skipped_version_survives_entity_recreation(self):
        entity = await self.add_repository("restored")
        await self.hass.services.async_call(
            "update", "skip", {"entity_id": entity.entity_id}, blocking=True
        )
        await entity.async_remove()
        replacement = HacsRepositoryUpdateEntity(self.hacs, entity.repository)
        await self.hass.data["update"].async_add_entities([replacement])
        self.enable()
        await self.finish()
        self.assertEqual(replacement.state, "off")
        entity.repository.async_download_repository.assert_not_awaited()

    async def test_fork_self_update_uses_same_service_flow(self):
        entity = await self.add_repository("hacs")
        entity.repository.data.full_name = HacsGitHubRepo.INTEGRATION
        entity.repository.data.installed_version = "2026.9.25.4"
        entity.repository.data.last_version = "2026.9.26"
        self.enable()
        await self.finish()
        entity.repository.async_download_repository.assert_awaited_once_with(ref="2026.9.26")
