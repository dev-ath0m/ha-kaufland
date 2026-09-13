"""Data Update Coordinators for the Kaufland integration."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import storage
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import KauflandAPIClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_EMAIL,
    CONF_AUTO_ACTIVATE_FREE_COUPONS,
    CONF_INSTORE_SESSION_COOKIE,
    CONF_PRODUCT_FILTERS,
    CONF_REFRESH_TOKEN,
    CONF_STORE_CODE,
    CONF_TOKEN_EXPIRES_AT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_AUTO_ACTIVATE_FREE_COUPONS,
    DEFAULT_COUPONS_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    ISSUE_ID_ACCOUNT_REAUTH,
    ISSUE_ID_CONNECTION,
    ISSUE_ID_INSTORE_COOKIE_INVALID,
    MIN_UPDATE_INTERVAL,
)
from .coupons_api import (
    KauflandAuth,
    KauflandAuthError,
    KauflandCouponsApiError,
    KauflandCouponsClient,
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


class KauflandCouponsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manage fetching coupons for a linked Kaufland account and, unless
    disabled, automatically activate every Kaufland Card XTRA marketplace
    coupon that costs 0 loyalty points (i.e. is free to activate).

    Coupons that require spending loyalty points (``loyaltyPoints`` > 0) are
    never auto-activated - only coupons with ``loyaltyPoints`` missing/0.

    Activating a coupon requires a session cookie (``ALTSESSID``) in
    addition to the OAuth bearer token - Kaufland's backend rejects
    activation from a plain API client with ``400 Missing session cookie``
    otherwise. This integration never obtains or forges that cookie itself;
    the user must copy it from their own, already logged-in browser session
    on kaufland.de and paste it into the account options
    (``CONF_INSTORE_SESSION_COOKIE``). Confirmed live 2026-09: once that
    cookie is supplied, marketplace coupon activation succeeds. Without it,
    activation attempts fail and coupons stay visible as pending so they can
    still be activated manually in the app (see ``last_activation_error``).

    Not every entry in the marketplace coupons feed is a real, per-account
    activatable coupon - some are plain product/"special offer" listings
    mixed into the same feed. Kaufland's activate endpoint returns success
    (``200``) for these too, without ever actually changing their status, so
    this coordinator verifies the status actually changed (via a follow-up
    fetch) before counting/reporting a coupon as activated, and remembers
    non-activatable gcns for the lifetime of the coordinator to avoid
    retrying them every update cycle.

    Marketplace-only by default: this coordinator only fetches *marketplace*
    coupons (``/coupons/marketplaceCoupons``). Kaufland's separate in-store/
    regular Kaufland Card XTRA coupons live behind a different endpoint that
    requires the same session cookie just to list them.

    If the user has supplied that cookie, this coordinator also fetches
    in-store coupons (``GET /coupons``) for read-only display. In-store
    coupon *activation* is not implemented - it goes through a separate,
    third-party backend Kaufland uses for its loyalty/CRM coupons, distinct
    from the marketplace API this coordinator otherwise talks to. This is
    opt-in and will stop working whenever the manually-supplied cookie
    expires until the user refreshes it - a repair issue
    (``instore_cookie_invalid``) is raised when that happens.
    """


    config_entry: config_entries.ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: config_entries.ConfigEntry) -> None:
        """Initialize coordinator."""
        config = {**entry.data, **entry.options}
        self.config_entry = entry
        self.account_email: str | None = entry.data.get(CONF_ACCOUNT_EMAIL)
        self.auto_activate_free_coupons: bool = config.get(
            CONF_AUTO_ACTIVATE_FREE_COUPONS, DEFAULT_AUTO_ACTIVATE_FREE_COUPONS
        )
        self.instore_session_cookie: str | None = (
            config.get(CONF_INSTORE_SESSION_COOKIE) or None
        )

        self._session = async_get_clientsession(hass)
        self._auth = KauflandAuth(self._session)
        self._issue_created = False
        self._instore_issue_created = False
        # gcns where Kaufland's activate endpoint returned success (200) but
        # the coupon's status never actually changed - these appear to be
        # plain "special offer"/product-deal listings mixed into the same
        # marketplace coupons feed, not real per-account activatable
        # coupons. Remembered so we don't keep retrying them every cycle.
        self._non_activatable_gcns: set[str] = set()

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"Kaufland account {self.account_email or entry.entry_id}",
            update_interval=timedelta(hours=DEFAULT_COUPONS_UPDATE_INTERVAL),
        )

    async def _async_get_valid_access_token(self) -> str:
        """Return a valid access token, refreshing it first if needed."""
        data = self.config_entry.data
        if data.get(CONF_TOKEN_EXPIRES_AT, 0) > time.time() + 60:
            return data[CONF_ACCESS_TOKEN]

        tokens = await self._auth.refresh(data[CONF_REFRESH_TOKEN])
        new_data = {
            **data,
            CONF_ACCESS_TOKEN: tokens["access_token"],
            CONF_REFRESH_TOKEN: tokens.get("refresh_token", data[CONF_REFRESH_TOKEN]),
            CONF_TOKEN_EXPIRES_AT: tokens["expires_at"],
        }
        self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
        return new_data[CONF_ACCESS_TOKEN]

    @staticmethod
    def _is_free_to_activate(coupon: dict[str, Any]) -> bool:
        """Return True if a coupon costs 0 loyalty points to activate."""
        return not coupon.get("loyaltyPoints")

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch current coupons and auto-activate the free (0 point) ones."""
        try:
            access_token = await self._async_get_valid_access_token()
        except KauflandAuthError as err:
            if not self._issue_created:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    ISSUE_ID_ACCOUNT_REAUTH,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="account_reauth_required",
                )
                self._issue_created = True
            raise UpdateFailed(
                f"Kaufland account re-authentication required: {err}"
            ) from err

        client = KauflandCouponsClient(self._session, access_token)

        try:
            payload = await client.get_marketplace_coupons()
        except KauflandCouponsApiError as err:
            raise UpdateFailed(f"Error communicating with Kaufland: {err}") from err

        coupons: list[dict[str, Any]] = payload.get("coupons", [])
        activated: list[str] = []
        last_activation_error: str | None = None

        if self.auto_activate_free_coupons:
            failed = 0
            attempted: list[str] = []
            for coupon in coupons:
                gcn = coupon.get("gcn")
                if not gcn or coupon.get("status") != 0:
                    continue  # already activated, expired, or no id
                if not self._is_free_to_activate(coupon):
                    continue  # costs loyalty points - never auto-activate
                if gcn in self._non_activatable_gcns:
                    continue  # confirmed non-activatable in a previous cycle

                try:
                    await client.activate_coupon(
                        gcn,
                        coupon.get("exchangeRuleNumber"),
                        self.instore_session_cookie,
                    )
                    attempted.append(gcn)
                except KauflandCouponsApiError as err:
                    # Activation requires the same session cookie as in-store
                    # coupons (see class docstring). Without it - or if it
                    # has expired - Kaufland rejects activation with "Missing
                    # session cookie", so keep this at debug level to avoid
                    # log spam; see last_activation_error / the coupons
                    # attribute for visibility instead of a warning every
                    # update cycle.
                    _LOGGER.debug(
                        "Kaufland: failed to auto-activate free coupon %s: %s",
                        gcn,
                        err,
                    )
                    last_activation_error = str(err)
                    failed += 1

            if failed:
                _LOGGER.debug(
                    "Kaufland: %d free coupon(s) could not be auto-activated "
                    "(requires the session cookie in account options); they "
                    "remain visible as pending",
                    failed,
                )

            if attempted:
                try:
                    payload = await client.get_marketplace_coupons()
                    coupons = payload.get("coupons", [])
                except KauflandCouponsApiError as err:
                    _LOGGER.debug(
                        "Kaufland: could not refresh coupons after activation: %s", err
                    )
                else:
                    # Kaufland's activate endpoint returns 200 even for some
                    # marketplace listings that never actually change status
                    # (plain "special offer" product deals mixed into the
                    # same feed as real, per-account activatable coupons).
                    # Only report/keep a coupon as activated if its status
                    # actually changed - otherwise remember it as
                    # non-activatable so it isn't retried every cycle.
                    refreshed_by_gcn = {c.get("gcn"): c for c in coupons}
                    for gcn in attempted:
                        refreshed = refreshed_by_gcn.get(gcn)
                        if refreshed is not None and refreshed.get("status") != 0:
                            activated.append(gcn)
                            _LOGGER.info(
                                "Kaufland: auto-activated free coupon %s", gcn
                            )
                        else:
                            self._non_activatable_gcns.add(gcn)
                            _LOGGER.debug(
                                "Kaufland: activation call for %s succeeded but "
                                "its status did not change - treating it as a "
                                "non-activatable listing (e.g. a special offer, "
                                "not a real coupon) and will not retry it",
                                gcn,
                            )

        if self._issue_created:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_ID_ACCOUNT_REAUTH)
            self._issue_created = False

        instore_coupons: list[dict[str, Any]] = []
        instore_fetch_error: str | None = None
        if self.instore_session_cookie:
            try:
                instore_payload = await client.get_instore_coupons(
                    self.instore_session_cookie
                )
                # This endpoint returns several coupon categories in one
                # payload - only "stationary_coupons" are the in-store/
                # regular Kaufland Card XTRA ones we're after here.
                # Confirmed live 2026-09-13: status is the *string*
                # "active"/"inactive" (not the marketplace endpoint's
                # integer 0/non-0), and the points field is snake_case
                # "loyalty_points".
                instore_coupons = instore_payload.get("stationary_coupons", [])
                if self._instore_issue_created:
                    ir.async_delete_issue(
                        self.hass, DOMAIN, ISSUE_ID_INSTORE_COOKIE_INVALID
                    )
                    self._instore_issue_created = False
            except KauflandCouponsApiError as err:
                # Not fatal for the whole update - marketplace data is still
                # good. The manually-supplied session cookie has likely
                # expired; ask the user to refresh it via a repair issue
                # rather than failing every coordinator refresh.
                instore_fetch_error = str(err)
                _LOGGER.debug(
                    "Kaufland: in-store coupons fetch failed (session cookie "
                    "likely expired, see account options): %s",
                    err,
                )
                if not self._instore_issue_created:
                    ir.async_create_issue(
                        self.hass,
                        DOMAIN,
                        ISSUE_ID_INSTORE_COOKIE_INVALID,
                        is_fixable=False,
                        severity=ir.IssueSeverity.WARNING,
                        translation_key="instore_cookie_invalid",
                    )
                    self._instore_issue_created = True

        return {
            "coupons": coupons,
            "activated_this_cycle": activated,
            "last_activation_error": last_activation_error,
            "instore_coupons": instore_coupons,
            "instore_fetch_error": instore_fetch_error,
        }

