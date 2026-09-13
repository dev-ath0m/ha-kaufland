"""Kaufland sensor platform."""

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

from .const import ATTRIBUTION, CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_ACCOUNT
from .coordinator import KauflandCouponsCoordinator, KauflandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: Any,
) -> None:
    """Set up Kaufland sensors from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_ACCOUNT:
        entities: list[Any] = [
            KauflandAvailableCouponsSensor(coordinator),
            KauflandCouponsSensor(coordinator),
        ]
        if coordinator.instore_session_cookie:
            entities.append(KauflandAvailableInstoreCouponsSensor(coordinator))
            entities.append(KauflandActiveInstoreCouponsSensor(coordinator))
        async_add_entities(entities, update_before_add=False)
        return

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


class KauflandAvailableCouponsSensor(
    CoordinatorEntity[KauflandCouponsCoordinator], SensorEntity
):
    """Represents Kaufland Card XTRA *marketplace* coupons that are
    available to activate (fetched, not yet activated - ``status == 0``).

    Marketplace-only: Kaufland's separate in-store/regular Kaufland Card
    XTRA coupons are not covered here - listing those requires a session
    cookie (``ALTSESSID``) this integration intentionally does not try to
    obtain (see the coordinator docstring / repo notes for details).
    """

    _attr_icon = "mdi:ticket-outline"
    _attr_native_unit_of_measurement = "coupons"
    _attr_has_entity_name = True
    _attr_name = "Available Marketplace Coupons"
    _unrecorded_attributes = frozenset({"coupons"})

    def __init__(self, coordinator: KauflandCouponsCoordinator) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self._account_email = coordinator.account_email or coordinator.config_entry.entry_id
        self._attr_unique_id = f"kaufland_account_{self._account_email}_available_coupons"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._account_email)},
            name=coordinator.config_entry.title,
            manufacturer="Kaufland",
            model="Account",
        )

    @staticmethod
    def _pending_coupons(coupons: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return coupons that are still available to activate (status 0)."""
        return [c for c in coupons if c.get("status") == 0]

    @property
    def native_value(self) -> int | None:
        """Return the number of coupons available to activate."""
        if not self.coordinator.data:
            return None
        coupons = self.coordinator.data.get("coupons", [])
        return len(self._pending_coupons(coupons))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full list of available coupons, split free vs. points."""
        data = self.coordinator.data or {}
        coupons = self._pending_coupons(data.get("coupons", []))
        free_coupons = [c for c in coupons if not c.get("loyaltyPoints")]
        points_coupons = [c for c in coupons if c.get("loyaltyPoints")]
        return {
            "account_email": self.coordinator.account_email,
            "coupons": coupons,
            "free_coupon_count": len(free_coupons),
            "points_required_coupon_count": len(points_coupons),
            ATTR_ATTRIBUTION: ATTRIBUTION,
        }

    @property
    def available(self) -> bool:
        """Return True if coordinator has data."""
        return self.coordinator.data is not None


class KauflandCouponsSensor(
    CoordinatorEntity[KauflandCouponsCoordinator], SensorEntity
):
    """Represents the linked Kaufland account's activated *marketplace*
    coupons (``status != 0``).

    Marketplace-only: this will read 0 unless a coupon was activated some
    other way (e.g. manually in the Kaufland app) and that state happens to
    be reflected by the marketplace coupons API - server-side activation
    from a plain API client is currently rejected (see
    ``KauflandCouponsCoordinator`` docstring). This sensor never reflects
    the separate in-store/regular Kaufland Card XTRA coupons shown
    elsewhere in the app - listing those requires a session cookie
    (``ALTSESSID``) that has the same anti-automation protection and is
    intentionally not something this integration tries to obtain.
    """

    _attr_icon = "mdi:ticket-percent"
    _attr_native_unit_of_measurement = "coupons"
    _attr_has_entity_name = True
    _attr_name = "Active Marketplace Coupons"
    _unrecorded_attributes = frozenset({"coupons"})


    def __init__(self, coordinator: KauflandCouponsCoordinator) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self._account_email = coordinator.account_email or coordinator.config_entry.entry_id
        self._attr_unique_id = f"kaufland_account_{self._account_email}_coupons"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._account_email)},
            name=coordinator.config_entry.title,
            manufacturer="Kaufland",
            model="Account",
        )

    @property
    def native_value(self) -> int | None:
        """Return the number of activated (status != 0) coupons."""
        if not self.coordinator.data:
            return None
        coupons = self.coordinator.data.get("coupons", [])
        return sum(1 for c in coupons if c.get("status") != 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return coupon details and last auto-activation results."""
        data = self.coordinator.data or {}
        return {
            "account_email": self.coordinator.account_email,
            "auto_activate_free_coupons": self.coordinator.auto_activate_free_coupons,
            "coupons": data.get("coupons", []),
            "activated_this_cycle": data.get("activated_this_cycle", []),
            "last_activation_error": data.get("last_activation_error"),
            ATTR_ATTRIBUTION: ATTRIBUTION,
        }

    @property
    def available(self) -> bool:
        """Return True if coordinator has data."""
        return self.coordinator.data is not None


class KauflandAvailableInstoreCouponsSensor(
    CoordinatorEntity[KauflandCouponsCoordinator], SensorEntity
):
    """Represents in-store/regular Kaufland Card XTRA coupons that are
    available to activate (``status == "inactive"``).

    Experimental & opt-in: only created when the user has manually supplied
    an ``ALTSESSID`` session cookie value in the account options - this
    integration never obtains or forges that cookie itself, the user must
    copy it from their own already logged-in browser session on
    kaufland.de. It expires periodically and will need to be refreshed
    there when an ``instore_cookie_invalid`` repair issue appears. Field
    names/values (``status`` is the *string* "active"/"inactive", points
    field is ``loyalty_points``) were confirmed live on 2026-09-13.
    """

    _attr_icon = "mdi:ticket-outline"
    _attr_native_unit_of_measurement = "coupons"
    _attr_has_entity_name = True
    _attr_name = "Available In-store Coupons"
    _unrecorded_attributes = frozenset({"coupons"})

    def __init__(self, coordinator: KauflandCouponsCoordinator) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self._account_email = coordinator.account_email or coordinator.config_entry.entry_id
        self._attr_unique_id = (
            f"kaufland_account_{self._account_email}_available_instore_coupons"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._account_email)},
            name=coordinator.config_entry.title,
            manufacturer="Kaufland",
            model="Account",
        )

    @property
    def native_value(self) -> int | None:
        """Return the number of in-store coupons available to activate."""
        if not self.coordinator.data:
            return None
        coupons = self.coordinator.data.get("instore_coupons", [])
        return len([c for c in coupons if c.get("status") == "inactive"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full list of available in-store coupons, split free vs. points."""
        data = self.coordinator.data or {}
        coupons = [
            c for c in data.get("instore_coupons", []) if c.get("status") == "inactive"
        ]
        free_coupons = [c for c in coupons if not c.get("loyalty_points")]
        points_coupons = [c for c in coupons if c.get("loyalty_points")]
        return {
            "account_email": self.coordinator.account_email,
            "coupons": coupons,
            "free_coupon_count": len(free_coupons),
            "points_required_coupon_count": len(points_coupons),
            "fetch_error": data.get("instore_fetch_error"),
            ATTR_ATTRIBUTION: ATTRIBUTION,
        }

    @property
    def available(self) -> bool:
        """Return True if coordinator has data and in-store tracking is configured."""
        return self.coordinator.data is not None and bool(
            self.coordinator.instore_session_cookie
        )


class KauflandActiveInstoreCouponsSensor(
    CoordinatorEntity[KauflandCouponsCoordinator], SensorEntity
):
    """Represents in-store/regular Kaufland Card XTRA coupons that have
    already been activated (``status == "active"``).

    Experimental & opt-in - see ``KauflandAvailableInstoreCouponsSensor``
    docstring for details on the required manually-supplied session cookie.
    """

    _attr_icon = "mdi:ticket-percent"
    _attr_native_unit_of_measurement = "coupons"
    _attr_has_entity_name = True
    _attr_name = "Active In-store Coupons"
    _unrecorded_attributes = frozenset({"coupons"})

    def __init__(self, coordinator: KauflandCouponsCoordinator) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self._account_email = coordinator.account_email or coordinator.config_entry.entry_id
        self._attr_unique_id = (
            f"kaufland_account_{self._account_email}_active_instore_coupons"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._account_email)},
            name=coordinator.config_entry.title,
            manufacturer="Kaufland",
            model="Account",
        )

    @property
    def native_value(self) -> int | None:
        """Return the number of activated in-store coupons."""
        if not self.coordinator.data:
            return None
        coupons = self.coordinator.data.get("instore_coupons", [])
        return sum(1 for c in coupons if c.get("status") == "active")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return in-store coupon details."""
        data = self.coordinator.data or {}
        return {
            "account_email": self.coordinator.account_email,
            "coupons": data.get("instore_coupons", []),
            "fetch_error": data.get("instore_fetch_error"),
            ATTR_ATTRIBUTION: ATTRIBUTION,
        }

    @property
    def available(self) -> bool:
        """Return True if coordinator has data and in-store tracking is configured."""
        return self.coordinator.data is not None and bool(
            self.coordinator.instore_session_cookie
        )


def coordinator_email(coordinator: KauflandCouponsCoordinator) -> str | None:
    """Return the linked account's email for display in sensor attributes."""
    return coordinator.account_email
