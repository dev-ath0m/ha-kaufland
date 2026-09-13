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
    ISSUE_ID_MARKETPLACE_COOKIE_INVALID,
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

    This coordinator also fetches and auto-activates in-store/regular
    Kaufland Card XTRA coupons (``storeCoupons``), via a completely
    different backend (``app.kaufland.net``/"loyalty" API) than marketplace
    coupons. Confirmed live 2026-09: unlike marketplace activation, this
    endpoint only needs the OAuth bearer token - no session cookie - and
    exposes a ``buttonActive`` flag that reliably tells real,
    currently-activatable coupons apart from "Deal des Tages" style preview
    listings that aren't activatable yet (which the older, cookie-gated
    ``/coupons`` endpoint this used to rely on could not distinguish).
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
        self._marketplace_cookie_issue_created = False
        # gcns where Kaufland's activate endpoint returned success (200) but
        # the coupon's status never actually changed - these appear to be
        # plain "special offer"/product-deal listings mixed into the same
        # marketplace coupons feed, not real per-account activatable
        # coupons. Remembered so we don't keep retrying them every cycle.
        self._non_activatable_gcns: set[str] = set()
        # Same idea, but for in-store/regular Kaufland Card XTRA coupons.
        self._non_activatable_instore_gcns: set[str] = set()

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
            cookie_error = False
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
                    # update cycle. A repair issue is raised instead (see
                    # below) so the user is proactively notified once,
                    # rather than on every update cycle.
                    _LOGGER.debug(
                        "Kaufland: failed to auto-activate free coupon %s: %s",
                        gcn,
                        err,
                    )
                    last_activation_error = str(err)
                    failed += 1
                    if "session cookie" in str(err).lower():
                        cookie_error = True

            if failed:
                _LOGGER.debug(
                    "Kaufland: %d free coupon(s) could not be auto-activated "
                    "(requires the session cookie in account options); they "
                    "remain visible as pending",
                    failed,
                )

            # There is no way to automatically refresh the ALTSESSID session
            # cookie - it's a short-lived, HMAC-signed WebView session that
            # must go through Kaufland's real login + anti-bot flow in a
            # real browser (see repo notes). Proactively surface a repair
            # issue instead so the user knows to paste a fresh one in the
            # account options, rather than silently failing every cycle.
            if cookie_error:
                if not self._marketplace_cookie_issue_created:
                    ir.async_create_issue(
                        self.hass,
                        DOMAIN,
                        ISSUE_ID_MARKETPLACE_COOKIE_INVALID,
                        is_fixable=False,
                        severity=ir.IssueSeverity.WARNING,
                        translation_key="marketplace_cookie_invalid",
                    )
                    self._marketplace_cookie_issue_created = True
            elif attempted and self._marketplace_cookie_issue_created:
                # At least one activation call was accepted (no cookie
                # error) - the cookie is valid again.
                ir.async_delete_issue(
                    self.hass, DOMAIN, ISSUE_ID_MARKETPLACE_COOKIE_INVALID
                )
                self._marketplace_cookie_issue_created = False

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
        instore_activated: list[str] = []
        instore_last_activation_error: str | None = None
        instore_fetch_error: str | None = None
        try:
            loyalty_payload = await client.get_loyalty_coupons()
            instore_coupons = loyalty_payload.get("storeCoupons", {}).get(
                "coupons", []
            )
        except KauflandCouponsApiError as err:
            instore_fetch_error = str(err)
            _LOGGER.debug("Kaufland: in-store coupons fetch failed: %s", err)

        if self.auto_activate_free_coupons and instore_coupons:
            failed = 0
            attempted = []
            for coupon in instore_coupons:
                gcn = coupon.get("gcn")
                if not gcn or coupon.get("status") != 0:
                    continue  # already activated, expired, or no id
                if not coupon.get("buttonActive", True):
                    continue  # not yet activatable (e.g. a "Deal des Tages" preview)
                if not self._is_free_to_activate(coupon):
                    continue  # costs loyalty points - never auto-activate
                if gcn in self._non_activatable_instore_gcns:
                    continue  # confirmed non-activatable in a previous cycle

                try:
                    await client.activate_stationary_coupon(
                        gcn, coupon.get("exchangeRuleNumber")
                    )
                    attempted.append(gcn)
                except KauflandCouponsApiError as err:
                    _LOGGER.debug(
                        "Kaufland: failed to auto-activate free in-store coupon %s: %s",
                        gcn,
                        err,
                    )
                    instore_last_activation_error = str(err)
                    failed += 1

            if failed:
                _LOGGER.debug(
                    "Kaufland: %d free in-store coupon(s) could not be "
                    "auto-activated; they remain visible as pending",
                    failed,
                )

            if attempted:
                try:
                    loyalty_payload = await client.get_loyalty_coupons()
                    instore_coupons = loyalty_payload.get("storeCoupons", {}).get(
                        "coupons", []
                    )
                except KauflandCouponsApiError as err:
                    _LOGGER.debug(
                        "Kaufland: could not refresh in-store coupons after "
                        "activation: %s",
                        err,
                    )
                else:
                    refreshed_by_gcn = {c.get("gcn"): c for c in instore_coupons}
                    for gcn in attempted:
                        refreshed = refreshed_by_gcn.get(gcn)
                        if refreshed is not None and refreshed.get("status") != 0:
                            instore_activated.append(gcn)
                            _LOGGER.info(
                                "Kaufland: auto-activated free in-store coupon %s",
                                gcn,
                            )
                        else:
                            self._non_activatable_instore_gcns.add(gcn)
                            _LOGGER.debug(
                                "Kaufland: activation call for in-store coupon %s "
                                "succeeded but its status did not change - "
                                "treating it as non-activatable and will not "
                                "retry it",
                                gcn,
                            )

        return {
            "coupons": coupons,
            "activated_this_cycle": activated,
            "last_activation_error": last_activation_error,
            "instore_coupons": instore_coupons,
            "instore_activated_this_cycle": instore_activated,
            "instore_last_activation_error": instore_last_activation_error,
            "instore_fetch_error": instore_fetch_error,
        }

    async def async_activate_all_now(self) -> None:
        """Immediately attempt to activate every free, pending coupon.

        Used by the "Activate All Coupons" button for on-demand activation
        outside the normal polling interval - runs the exact same
        fetch-activate-verify logic as a scheduled update (marketplace and
        in-store), temporarily forcing activation on for this one refresh
        even if the "Automatically activate free coupons" option is
        disabled (a manual button press is an explicit request to
        activate, so it should work regardless of that setting).
        """
        previous = self.auto_activate_free_coupons
        self.auto_activate_free_coupons = True
        try:
            await self.async_request_refresh()
        finally:
            self.auto_activate_free_coupons = previous

