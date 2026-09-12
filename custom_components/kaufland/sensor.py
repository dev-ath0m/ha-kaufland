"""Kaufland Weekly Offers sensor platform."""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant import config_entries
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import ATTR_ATTRIBUTION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import KauflandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: Any,
) -> None:
    """Set up Kaufland Weekly Offers sensors from a config entry."""
    coordinator: KauflandDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[Any] = [KauflandOffersSensor(coordinator)]

    for product_filter in coordinator.product_filters:
        entities.append(KauflandProductFilterSensor(coordinator, product_filter))

    async_add_entities(entities, update_before_add=False)


def _parse_price(price_str: str | None) -> float | None:
    """Parse numeric price float from a string like '1,49' or '1.49 €'."""
    if not price_str or price_str == "-":
        return None
    cleaned = price_str.replace("€", "").replace("$", "").replace("£", "").strip()
    match = re.search(r"(\d+(?:[.,]\d+)?)", cleaned)
    if match:
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return None
    return None


class KauflandOffersSensor(
    CoordinatorEntity[KauflandDataUpdateCoordinator], SensorEntity
):
    """Represents current Kaufland weekly offers."""

    _attr_icon = "mdi:cart-percent"
    _attr_native_unit_of_measurement = "items"
    _attr_has_entity_name = True
    _attr_name = "Offers"
    _unrecorded_attributes = frozenset({"discounts", "discounts_by_date"})

    def __init__(self, coordinator: KauflandDataUpdateCoordinator) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self._store_code = coordinator.store_code
        self._attr_unique_id = f"kaufland_{self._store_code}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._store_code)},
            name=coordinator.config_entry.title,
            manufacturer="Kaufland",
            model="Weekly Offers",
            configuration_url=coordinator.configuration_url,
        )

    @property
    def native_value(self) -> int | None:
        """Return the number of current offers."""
        if not self.coordinator.data:
            return None
        return len(self.coordinator.data.get("offers", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details of current offers."""
        data = self.coordinator.data or {}
        config_data = self.coordinator.config_entry.data
        return {
            "store_code": self._store_code,
            "store_name": config_data.get("name"),
            "store_address": config_data.get("street"),
            "store_postal_code": config_data.get("postal_code"),
            "store_city": config_data.get("city"),
            "valid_from": data.get("valid_from"),
            "valid_until": data.get("valid_until"),
            "discounts": data.get("offers", []),
            "discounts_by_date": data.get("offers_by_date", {}),
            ATTR_ATTRIBUTION: ATTRIBUTION,
        }

    @property
    def available(self) -> bool:
        """Return True if coordinator has data."""
        return self.coordinator.data is not None


class KauflandProductFilterSensor(
    CoordinatorEntity[KauflandDataUpdateCoordinator], SensorEntity
):
    """Represents a product filter offer sensor."""

    _attr_icon = "mdi:tag-search"
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: KauflandDataUpdateCoordinator, product_filter: str
    ) -> None:
        """Initialize product filter sensor."""
        super().__init__(coordinator)
        self._store_code = coordinator.store_code
        self._filter = product_filter
        clean_slug = re.sub(r"[^a-z0-9_]+", "_", product_filter.lower()).strip("_")
        self._attr_name = f"Offer {product_filter}"
        self._attr_unique_id = f"kaufland_{self._store_code}_filter_{clean_slug}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._store_code)},
            name=coordinator.config_entry.title,
            manufacturer="Kaufland",
            model="Weekly Offers",
            configuration_url=coordinator.configuration_url,
        )

    def _get_matches(self) -> list[dict[str, Any]]:
        """Return list of matching offers for this filter."""
        if not self.coordinator.data:
            return []
        offers = self.coordinator.data.get("offers", [])
        filter_term = self._filter.lower().strip()
        if not filter_term:
            return []

        matches: list[dict[str, Any]] = []
        for offer in offers:
            searchable_text = " ".join(
                str(offer.get(field) or "")
                for field in (
                    "title",
                    "subtitle",
                    "category",
                    "price_per_unit",
                    "discount",
                )
            ).lower()
            if filter_term in searchable_text:
                matches.append(offer)
        return matches

    @property
    def native_value(self) -> str:
        """Return best price found or 'Nicht im Angebot'."""
        matches = self._get_matches()
        if not matches:
            return "Nicht im Angebot"

        best_price = None
        best_price_numeric = float("inf")

        for m in matches:
            price_val = m.get("price")
            if price_val and price_val != "-":
                num = _parse_price(str(price_val))
                if num is not None and num < best_price_numeric:
                    best_price_numeric = num
                    best_price = str(price_val)
                elif best_price is None:
                    best_price = str(price_val)

        return best_price if best_price is not None else "Nicht im Angebot"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose product filter attributes."""
        matches = self._get_matches()
        on_sale = len(matches) > 0

        best_match: dict[str, Any] = {}
        best_price = None
        best_price_numeric = float("inf")

        if matches:
            for m in matches:
                price_val = m.get("price")
                if price_val and price_val != "-":
                    num = _parse_price(str(price_val))
                    if num is not None and num < best_price_numeric:
                        best_price_numeric = num
                        best_price = str(price_val)
                        best_match = m
                    elif not best_match:
                        best_match = m
            if not best_match:
                best_match = matches[0]
            if best_price is None:
                best_price = best_match.get("price")

        return {
            "filter": self._filter,
            "on_sale": on_sale,
            "match_count": len(matches),
            "best_price": best_price,
            "base_price": best_match.get("price_per_unit"),
            "product_title": best_match.get("title"),
            "category": best_match.get("category"),
            "picture_link": best_match.get("image_url"),
            "valid_from": best_match.get("date_from"),
            "valid_until": best_match.get("date_to"),
            "matches": matches,
            ATTR_ATTRIBUTION: ATTRIBUTION,
        }

    @property
    def available(self) -> bool:
        """Return True if coordinator has data."""
        return self.coordinator.data is not None
