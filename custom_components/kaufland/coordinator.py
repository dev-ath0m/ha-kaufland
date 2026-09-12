"""Data Update Coordinator for the Kaufland Weekly Offers integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import storage
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import KauflandAPIClient
from .const import (
    CONF_PRODUCT_FILTERS,
    CONF_STORE_CODE,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    ISSUE_ID_CONNECTION,
    MIN_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class KauflandDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manage fetching Kaufland weekly offers for a single store."""

    config_entry: config_entries.ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: config_entries.ConfigEntry) -> None:
        """Initialize coordinator."""
        config = {**entry.data, **entry.options}
        self.store_code: str = config[CONF_STORE_CODE]
        self.product_filters: list[str] = config.get(CONF_PRODUCT_FILTERS, [])
        self.config_entry = entry
        self.client = KauflandAPIClient()

        self._issue_created = False

        self.store: storage.Store = storage.Store(
            hass, 1, f"{DOMAIN}_{self.store_code}"
        )

        interval_hours = max(
            MIN_UPDATE_INTERVAL,
            config.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
        )

        friendly_url = entry.data.get("friendly_url")
        self.configuration_url = (
            f"https://filiale.kaufland.de/standorte/{friendly_url}.html"
            if friendly_url
            else "https://filiale.kaufland.de/"
        )

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"Kaufland {self.store_code}",
            update_interval=timedelta(hours=interval_hours),
        )

    async def async_load_cache(self) -> None:
        """Load cached data from HA storage (restart-resistance)."""
        cache = await self.store.async_load()
        if cache:
            if "offers" not in cache:
                _LOGGER.info(
                    "Kaufland cache for store %s is outdated – discarding",
                    self.store_code,
                )
                await self.store.async_remove()
                return
            self.data = cache

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch current Kaufland weekly offers for the configured store."""
        try:
            data = await self.hass.async_add_executor_job(
                self.client.get_offers, self.store_code
            )
            data["last_success"] = dt_util.now().isoformat()

            await self.store.async_save(data)

            if self._issue_created:
                ir.async_delete_issue(self.hass, DOMAIN, ISSUE_ID_CONNECTION)
                self._issue_created = False

            return data
        except Exception as err:
            _LOGGER.warning(
                "Kaufland store %s: fetch failed: %s", self.store_code, err
            )

            if not self._issue_created:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    ISSUE_ID_CONNECTION,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="connection_error",
                )
                self._issue_created = True

            raise UpdateFailed(f"Error communicating with Kaufland: {err}") from err
