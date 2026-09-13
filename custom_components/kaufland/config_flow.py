"""Config flow for the Kaufland integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import KauflandAPIClient, Store
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_EMAIL,
    CONF_AUTO_ACTIVATE_FREE_COUPONS,
    CONF_ENTRY_TYPE,
    CONF_PRODUCT_FILTERS,
    CONF_REFRESH_TOKEN,
    CONF_STORE_CODE,
    CONF_TOKEN_EXPIRES_AT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_AUTO_ACTIVATE_FREE_COUPONS,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    ENTRY_TYPE_ACCOUNT,
    ENTRY_TYPE_STORE,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
)
from .coupons_api import (
    KauflandAuth,
    KauflandAuthError,
    build_authorize_url,
    extract_code_from_input,
    generate_pkce_pair,
    generate_state,
)

_LOGGER = logging.getLogger(__name__)


class KauflandConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle a config flow for Kaufland."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._search_results: list[Store] = []
        self._discovery_data: dict[str, Any] = {}
        self._code_verifier: str | None = None
        self._state: str | None = None
        self._authorize_url: str | None = None

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle a discovered Kaufland store (triggered by location-based auto-discovery)."""
        store_code = str(discovery_info.get(CONF_STORE_CODE, "")).strip()
        if not store_code:
            return self.async_abort(reason="no_stores_found")

        await self.async_set_unique_id(f"kaufland_{store_code}")
        self._abort_if_unique_id_configured()

        self._discovery_data = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.get("name") or store_code,
            "city": discovery_info.get("city") or "",
            "address": discovery_info.get("street") or "",
        }
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm adding the discovered Kaufland store."""
        if user_input is not None:
            store_code = self._discovery_data.get(CONF_STORE_CODE, "")
            name = self._discovery_data.get("name") or store_code
            title = f"Kaufland {name}"
            return self.async_create_entry(
                title=title,
                data={**self._discovery_data, CONF_ENTRY_TYPE: ENTRY_TYPE_STORE},
            )

        return self.async_show_form(step_id="discovery_confirm")

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle initial step: choose what to set up."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["store_search", "link_account"],
        )

    async def async_step_store_search(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Search stores by postcode or city."""
        errors: dict[str, str] = {}

        if user_input is not None:
            query = user_input["search_query"].strip()

            try:
                client = KauflandAPIClient()
                results = await self.hass.async_add_executor_job(
                    client.search_stores, query
                )

                if not results:
                    errors["base"] = "no_stores_found"
                else:
                    self._search_results = results
                    return await self.async_step_select_store()
            except Exception as exc:
                _LOGGER.error("Kaufland store search error: %s", exc)
                errors["base"] = "search_failed"

        schema = vol.Schema(
            {
                vol.Required("search_query"): str,
            }
        )

        return self.async_show_form(
            step_id="store_search",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_select_store(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Let the user pick a store from the search results."""
        errors: dict[str, str] = {}

        if user_input is not None:
            store_code = user_input[CONF_STORE_CODE]
            store = next(
                (s for s in self._search_results if s.store_code == store_code), None
            )
            if store is None:
                errors["base"] = "no_stores_found"
            else:
                await self.async_set_unique_id(f"kaufland_{store_code}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Kaufland {store.title}",
                    data={
                        CONF_ENTRY_TYPE: ENTRY_TYPE_STORE,
                        CONF_STORE_CODE: store.store_code,
                        "name": store.name,
                        "street": store.street,
                        "postal_code": store.postal_code,
                        "city": store.city,
                        "friendly_url": store.friendly_url,
                    },
                )

        store_options = [
            {"value": s.store_code, "label": s.label}
            for s in self._search_results
            if s.store_code
        ]

        schema = vol.Schema(
            {
                vol.Required(CONF_STORE_CODE): SelectSelector(
                    SelectSelectorConfig(
                        options=store_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="select_store",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_link_account(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show the Kaufland account login link the user must open in a browser."""
        self._code_verifier, code_challenge = generate_pkce_pair()
        self._state = generate_state()
        self._authorize_url = build_authorize_url(
            state=self._state, code_challenge=code_challenge
        )

        return self.async_show_form(
            step_id="account_code",
            data_schema=vol.Schema({vol.Required("redirect_url"): str}),
            description_placeholders={"authorize_url": self._authorize_url},
        )

    async def async_step_account_code(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Exchange the pasted authorization code/redirect URL for tokens."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = extract_code_from_input(user_input["redirect_url"])
            session = async_get_clientsession(self.hass)
            auth = KauflandAuth(session)

            try:
                tokens = await auth.exchange_code(code, self._code_verifier or "")
            except KauflandAuthError as exc:
                _LOGGER.error("Kaufland account login failed: %s", exc)
                errors["base"] = "auth_failed"
            else:
                email = tokens.get("email")
                sub = tokens.get("sub")
                await self.async_set_unique_id(f"kaufland_account_{sub or email}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Kaufland account ({email})" if email else "Kaufland account",
                    data={
                        CONF_ENTRY_TYPE: ENTRY_TYPE_ACCOUNT,
                        CONF_ACCOUNT_EMAIL: email,
                        CONF_ACCESS_TOKEN: tokens["access_token"],
                        CONF_REFRESH_TOKEN: tokens["refresh_token"],
                        CONF_TOKEN_EXPIRES_AT: tokens["expires_at"],
                    },
                    options={
                        CONF_AUTO_ACTIVATE_FREE_COUPONS: DEFAULT_AUTO_ACTIVATE_FREE_COUPONS,
                    },
                )

        # Re-show the same form, with the same still-pending PKCE challenge,
        # so the user can correct a mistyped/expired code.
        return self.async_show_form(
            step_id="account_code",
            data_schema=vol.Schema({vol.Required("redirect_url"): str}),
            description_placeholders={"authorize_url": self._authorize_url},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return options flow handler."""
        if config_entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_ACCOUNT:
            return KauflandAccountOptionsFlowHandler(config_entry)
        return KauflandOptionsFlowHandler(config_entry)


class KauflandOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for a Kaufland store entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            product_filters_input = user_input.get(CONF_PRODUCT_FILTERS, [])
            if isinstance(product_filters_input, str):
                product_filters = [
                    f.strip() for f in product_filters_input.split(",") if f.strip()
                ]
            else:
                product_filters = [
                    str(f).strip() for f in product_filters_input if str(f).strip()
                ]

            return self.async_create_entry(
                title="",
                data={
                    CONF_UPDATE_INTERVAL: user_input[CONF_UPDATE_INTERVAL],
                    CONF_PRODUCT_FILTERS: product_filters,
                },
            )

        current = {**self._config_entry.data, **self._config_entry.options}

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=current.get(
                        CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_UPDATE_INTERVAL,
                        max=MAX_UPDATE_INTERVAL,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_PRODUCT_FILTERS,
                    default=current.get(CONF_PRODUCT_FILTERS, []),
                ): str,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)


class KauflandAccountOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for a linked Kaufland account entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_AUTO_ACTIVATE_FREE_COUPONS: user_input[
                        CONF_AUTO_ACTIVATE_FREE_COUPONS
                    ],
                },
            )

        current = {**self._config_entry.data, **self._config_entry.options}

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_AUTO_ACTIVATE_FREE_COUPONS,
                    default=current.get(
                        CONF_AUTO_ACTIVATE_FREE_COUPONS,
                        DEFAULT_AUTO_ACTIVATE_FREE_COUPONS,
                    ),
                ): bool,
            }
        )

        return self.async_show_form(step_id="account_init", data_schema=schema)

