"""Kaufland Weekly Offers – Home Assistant Custom Component."""

from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant import config_entries, core
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import (
    CONF_ENTRY_TYPE,
    CONF_REFRESH_TOKEN,
    CONF_STORE_CODE,
    DISCOVERY_RADIUS_KM,
    DOMAIN,
    ENTRY_TYPE_ACCOUNT,
)
from .coordinator import KauflandCouponsCoordinator, KauflandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# A Kaufland entry can hold a store (KauflandDataUpdateCoordinator), a linked
# account (KauflandCouponsCoordinator), or both (see _async_migrate_legacy_
# account_entries / the config flow's "also link my account" option) - the
# union of platforms used by either is the same, so a single list works.
PLATFORMS = ["sensor", "button"]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in km between two GPS coordinates."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def async_setup(hass: core.HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Kaufland integration.

    When 'kaufland:' is listed in configuration.yaml (zero-entry bootstrap),
    this is the only hook HA calls.
    """
    await _async_migrate_legacy_account_entries(hass)

    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get("_discovery_scheduled"):
        domain_data["_discovery_scheduled"] = True

        async def _on_ha_started(event: core.Event) -> None:  # noqa: RUF100
            await _async_discover_stores(hass)

        if hass.is_running:
            hass.async_create_task(_async_discover_stores(hass))
        else:
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_ha_started)

    return True


async def _async_migrate_legacy_account_entries(hass: core.HomeAssistant) -> None:
    """Fold a standalone linked-account entry into a single store entry.

    Account linking used to always create its own, separate config entry
    (``ENTRY_TYPE_ACCOUNT``), so a household with one store and one linked
    account ended up with two unrelated entries that never appeared grouped
    together in the UI - unlike e.g. the Lidl integration, which links an
    account as part of its single store entry. New installs now do the
    same (see the config/options flows), but existing installs need a
    one-time migration to merge their existing separate account entry into
    their store entry.

    Only runs the merge when there is exactly one store entry without an
    account already linked, and exactly one standalone account entry -
    the common case. Anything more ambiguous (0 or 2+ of either) is left
    alone; the standalone account entry keeps working on its own either
    way (account-only entries, with no store, remain fully supported).
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    unlinked_store_entries = [
        e
        for e in entries
        if CONF_STORE_CODE in e.data and CONF_REFRESH_TOKEN not in e.data
    ]
    account_only_entries = [
        e
        for e in entries
        if CONF_STORE_CODE not in e.data
        and e.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_ACCOUNT
    ]

    if len(unlinked_store_entries) != 1 or len(account_only_entries) != 1:
        return

    store_entry = unlinked_store_entries[0]
    account_entry = account_only_entries[0]

    _LOGGER.info(
        "Kaufland: merging linked account entry '%s' into store entry '%s' "
        "so coupons and weekly offers are grouped under one entry/device "
        "set (one-time migration)",
        account_entry.title,
        store_entry.title,
    )

    merged_data = {**store_entry.data, **account_entry.data}
    merged_data.pop(CONF_ENTRY_TYPE, None)  # store entry already implies "store"
    hass.config_entries.async_update_entry(
        store_entry,
        data=merged_data,
        options={**store_entry.options, **account_entry.options},
    )
    await hass.config_entries.async_remove(account_entry.entry_id)


async def _async_discover_stores(hass: core.HomeAssistant) -> None:
    """Search for the nearest Kaufland store and trigger integration discovery."""
    ha_lat = hass.config.latitude
    ha_lon = hass.config.longitude

    if not ha_lat or not ha_lon:
        _LOGGER.debug("Kaufland discovery: HA home location not set, skipping")
        return

    zip_code: str = getattr(hass.config, "zip_code", "") or ""
    location_name: str = hass.config.location_name or ""
    query = zip_code.strip() or location_name.strip()
    if not query:
        _LOGGER.debug(
            "Kaufland discovery: no ZIP code or location_name configured, skipping"
        )
        return

    from .api import KauflandAPIClient, Store

    try:
        client = KauflandAPIClient()
        stores: list[Store] = await hass.async_add_executor_job(
            client.search_stores, query
        )
    except Exception as exc:
        _LOGGER.debug("Kaufland discovery: API error during search: %s", exc)
        return

    configured_codes = {
        entry.data.get(CONF_STORE_CODE)
        for entry in hass.config_entries.async_entries(DOMAIN)
    }

    candidates: list[tuple[float, Store]] = []
    for store in stores:
        if not store.store_code:
            continue

        dist = DISCOVERY_RADIUS_KM
        if store.latitude is not None and store.longitude is not None:
            try:
                dist = _haversine_km(ha_lat, ha_lon, store.latitude, store.longitude)
            except (TypeError, ValueError):
                pass

        if dist <= DISCOVERY_RADIUS_KM:
            candidates.append((dist, store))

    if not candidates:
        _LOGGER.debug(
            "Kaufland discovery: no stores found within %.0f km", DISCOVERY_RADIUS_KM
        )
        return

    candidates.sort(key=lambda t: t[0])
    nearest_dist, nearest = candidates[0]

    if nearest.store_code in configured_codes:
        _LOGGER.debug(
            "Kaufland discovery: nearest store %s is already configured, skipping",
            nearest.store_code,
        )
        return

    _LOGGER.debug(
        "Kaufland discovery: triggering flow for nearest store %s (%s, %.1f km)",
        nearest.store_code,
        nearest.name,
        nearest_dist,
    )
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
            data={
                CONF_STORE_CODE: nearest.store_code,
                "name": nearest.name,
                "street": nearest.street,
                "postal_code": nearest.postal_code,
                "city": nearest.city,
                "friendly_url": nearest.friendly_url,
            },
        )
    )


async def async_setup_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Set up a Kaufland config entry.

    An entry can represent a store (weekly offers), a linked account
    (coupon auto-activation), or both at once - a store entry that also
    has a linked account, mirroring how other Kaufland/Lidl-style
    integrations group a store and its coupons under one entry/device set
    instead of two unrelated entries.
    """
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {}

    has_store = CONF_STORE_CODE in entry.data
    has_account = CONF_REFRESH_TOKEN in entry.data

    if has_store:
        await _async_setup_store(hass, entry)
    if has_account:
        await _async_setup_account(hass, entry)

    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if has_store:
        domain_data = hass.data[DOMAIN]
        if not domain_data.get("_discovery_scheduled"):
            domain_data["_discovery_scheduled"] = True
            hass.async_create_task(_async_discover_stores(hass))

    return True


async def _async_setup_store(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> None:
    """Set up the weekly-offers side of a Kaufland entry."""
    _LOGGER.debug(
        "Setting up Kaufland Weekly Offers entry: %s (store_code: %s)",
        entry.entry_id,
        entry.data.get(CONF_STORE_CODE),
    )

    coordinator = KauflandDataUpdateCoordinator(hass, entry)
    await coordinator.async_load_cache()

    hass.data[DOMAIN][entry.entry_id]["store"] = coordinator

    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as err:
        if not coordinator.data:
            raise ConfigEntryNotReady(
                f"Cannot connect to Kaufland for store {coordinator.store_code}: {err}"
            ) from err
        _LOGGER.warning(
            "Initial Kaufland update failed for store %s, using cached data. Error: %s",
            coordinator.store_code,
            err,
        )


async def _async_setup_account(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> None:
    """Set up the linked-account/coupons side of a Kaufland entry."""
    _LOGGER.debug("Setting up Kaufland linked account for entry: %s", entry.entry_id)

    coordinator = KauflandCouponsCoordinator(hass, entry)

    hass.data[DOMAIN][entry.entry_id]["account"] = coordinator

    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as err:
        raise ConfigEntryNotReady(
            f"Cannot connect to Kaufland account {coordinator.account_email}: {err}"
        ) from err


async def _async_update_options(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> None:
    """Reload entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Unload a Kaufland config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok

