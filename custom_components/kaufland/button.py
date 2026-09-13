"""Kaufland Weekly Offers button platform."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_ACCOUNT
from .coordinator import KauflandCouponsCoordinator, KauflandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: Any,
) -> None:
    """Set up Kaufland buttons from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_ACCOUNT:
        async_add_entities(
            [KauflandActivateAllCouponsButton(coordinator)], update_before_add=False
        )
        return

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


class KauflandActivateAllCouponsButton(ButtonEntity):
    """Button to immediately activate every free, pending coupon.

    Activates both marketplace and in-store Kaufland Card XTRA coupons on
    demand, regardless of the "Automatically activate free coupons"
    option - useful to trigger activation right away instead of waiting
    for the next scheduled update, e.g. right after a fresh "Deal des
    Tages" coupon becomes activatable or after pasting a freshly refreshed
    session cookie. Coupons that cost loyalty points are still never
    activated (same safety rule as automatic activation).
    """

    _attr_icon = "mdi:ticket-confirmation-outline"
    _attr_has_entity_name = True
    _attr_name = "Activate All Coupons"

    def __init__(self, coordinator: KauflandCouponsCoordinator) -> None:
        """Initialize the button."""
        self.coordinator = coordinator
        self._account_email = coordinator.account_email or coordinator.config_entry.entry_id
        self._attr_unique_id = f"kaufland_account_{self._account_email}_activate_all"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._account_email)},
            name=coordinator.config_entry.title,
            manufacturer="Kaufland",
            model="Account",
        )

    async def async_press(self) -> None:
        """Press the button."""
        _LOGGER.info(
            "Kaufland: manually activating all free coupons for account %s",
            self._account_email,
        )
        await self.coordinator.async_activate_all_now()
