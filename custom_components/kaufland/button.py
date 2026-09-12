"""Kaufland Weekly Offers button platform."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import KauflandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: Any,
) -> None:
    """Set up Kaufland Weekly Offers buttons from a config entry."""
    coordinator: KauflandDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KauflandForceUpdateButton(coordinator)], update_before_add=False)


class KauflandForceUpdateButton(ButtonEntity):
    """Button to force update Kaufland weekly offers."""

    _attr_icon = "mdi:refresh"
    _attr_has_entity_name = True
    _attr_name = "Force Update"

    def __init__(self, coordinator: KauflandDataUpdateCoordinator) -> None:
        """Initialize the button."""
        self.coordinator = coordinator
        self._store_code = coordinator.store_code
        self._attr_unique_id = f"kaufland_{self._store_code}_force_update"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._store_code)},
            name=coordinator.config_entry.title,
            manufacturer="Kaufland",
            model="Weekly Offers",
            configuration_url=coordinator.configuration_url,
        )

    async def async_press(self) -> None:
        """Press the button."""
        _LOGGER.info(
            "Forcing Kaufland weekly offers update for store %s", self._store_code
        )
        await self.coordinator.async_request_refresh()
