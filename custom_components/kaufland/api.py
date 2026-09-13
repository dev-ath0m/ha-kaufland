"""Pure Python client for Kaufland's public store finder & weekly offers pages.

Kaufland does not require any personal login to browse public store
information or the weekly "Angebote" (offers) overview for a given store.
This client mirrors that public, anonymous access:

- ``.klstorefinder.json`` returns the full national list of Kaufland stores
  (id, name, address, postcode, city, coordinates, opening hours, ...).
- The rendered ``/angebote/uebersicht.html`` page is server-side rendered
  with the currently valid offers for whichever store is selected via the
  ``x-aem-variant`` cookie (e.g. ``DE2553``). The page embeds a
  ``window.SSR[...] = {"component":"OfferTemplate", ...}`` JSON payload
  containing the complete, structured offer catalogue (every category and
  every offer for the current AND the next promotional week, each with its
  own ``dateFrom``/``dateTo`` validity), so that payload is parsed directly
  instead of scraping the rendered product tiles (which only reflect
  whichever single day/category tab happens to be selected in the UI).

If anything fails, methods raise ``RuntimeError`` rather than silently
returning fabricated placeholder data.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from curl_cffi import requests
from pydantic import BaseModel, ConfigDict, Field

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://filiale.kaufland.de"
STOREFINDER_URL = f"{BASE_URL}/.klstorefinder.json"
OFFERS_HTML_URL = f"{BASE_URL}/angebote/uebersicht.html"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_SSR_MARKER = "window.SSR['"
_OFFER_TEMPLATE_MARKER = '"component":"OfferTemplate"'


def _find_matching_brace(text: str, start: int) -> int | None:
    """Return the index of the ``}`` that closes the ``{`` at ``start``."""
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def _extract_offer_template_payload(html: str) -> dict[str, Any] | None:
    """Extract the embedded ``window.SSR`` ``OfferTemplate`` JSON payload.

    The offers page embeds one or more
    ``window.SSR['<uuid>'] = {...};`` script blocks. We look for the one
    whose object contains ``"component":"OfferTemplate"`` and parse it as
    JSON, using brace-matching (rather than a regex) since the payload
    contains arbitrarily nested objects/arrays and string values that may
    themselves contain ``{``/``}`` characters.
    """
    search_start = 0
    while True:
        marker_idx = html.find(_SSR_MARKER, search_start)
        if marker_idx == -1:
            return None
        brace_start = html.find("{", marker_idx)
        if brace_start == -1:
            return None
        if html[brace_start : brace_start + 200].find(_OFFER_TEMPLATE_MARKER) == -1:
            search_start = brace_start + 1
            continue
        brace_end = _find_matching_brace(html, brace_start)
        if brace_end is None:
            return None
        blob = html[brace_start : brace_end + 1]
        try:
            return json.loads(blob)
        except json.JSONDecodeError as exc:
            _LOGGER.debug("Failed to parse Kaufland OfferTemplate payload: %s", exc)
            return None


class Store(BaseModel):
    """A single Kaufland store, as returned by the public store finder."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    store_code: str | None = Field(default=None, alias="n")
    name: str | None = Field(default=None, alias="cn")
    street: str | None = Field(default=None, alias="sn")
    postal_code: str | None = Field(default=None, alias="pc")
    city: str | None = Field(default=None, alias="t")
    phone: str | None = Field(default=None, alias="p")
    latitude: float | None = Field(default=None, alias="lat")
    longitude: float | None = Field(default=None, alias="lng")
    friendly_url: str | None = Field(default=None, alias="friendlyUrl")
    closed_date: str | None = Field(default=None, alias="eod")

    @property
    def is_closed(self) -> bool:
        return bool(self.closed_date)

    @property
    def label(self) -> str:
        postcode_and_city = (
            " ".join(value for value in (self.postal_code, self.city) if value)
            or None
        )
        return ", ".join(
            value for value in (self.name, self.street, postcode_and_city) if value
        )

    @property
    def title(self) -> str:
        if self.name:
            return self.name
        if self.store_code:
            return f"Kaufland {self.store_code}"
        return "Kaufland"


class KauflandAPIClient:
    """API client for Kaufland's public store finder & weekly offers pages."""

    def __init__(self) -> None:
        self._stores_cache: list[Store] | None = None

    def _get(
        self, url: str, cookies: dict[str, str] | None = None
    ) -> requests.Response:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9",
            "User-Agent": _USER_AGENT,
        }
        try:
            response = requests.get(
                url,
                headers=headers,
                cookies=cookies,
                impersonate="chrome",
                timeout=30.0,
            )
            response.raise_for_status()
            return response
        except Exception as exc:
            _LOGGER.error("Kaufland request failed for %s: %s", url, exc)
            raise RuntimeError(f"Kaufland request failed: {exc}") from exc

    def list_stores(self, *, force_refresh: bool = False) -> list[Store]:
        """Return the full national list of Kaufland stores."""
        if self._stores_cache is not None and not force_refresh:
            return self._stores_cache

        response = self._get(STOREFINDER_URL)
        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError(f"Kaufland store finder returned invalid JSON: {exc}") from exc

        if not isinstance(data, list):
            raise RuntimeError("Kaufland store finder returned an unexpected payload")

        stores = [Store.model_validate(item) for item in data]
        self._stores_cache = stores
        return stores

    def search_stores(self, query: str) -> list[Store]:
        """Search stores by postcode/city/name/street via client-side filtering."""
        all_stores = self.list_stores()
        terms = [term.casefold() for term in query.split() if term.strip()]
        if not terms:
            return []

        results: list[Store] = []
        for store in all_stores:
            if store.is_closed:
                continue
            searchable = " ".join(
                value
                for value in (
                    store.store_code,
                    store.name,
                    store.street,
                    store.postal_code,
                    store.city,
                )
                if value
            ).casefold()
            if all(term in searchable for term in terms):
                results.append(store)
        return results

    def get_store(self, store_code: str) -> Store | None:
        """Return a single store by its store code (e.g. 'DE2553')."""
        for store in self.list_stores():
            if store.store_code == store_code:
                return store
        return None

    def get_offers(self, store_code: str) -> dict[str, Any]:
        """Fetch and parse the currently valid weekly offers for a store.

        The offers page's embedded ``OfferTemplate`` JSON payload holds
        every promotional "cycle" known to the storefront at once (usually
        the current week, a "start of week" highlight batch, and the
        upcoming week), each broken down into categories and, within each
        category, individual offers with their own ``dateFrom``/``dateTo``
        validity window. Parsing that payload (rather than the rendered
        product tiles, which only ever show whichever single day/category
        tab is currently selected) yields the complete catalogue in one
        request.
        """
        html = self._get(
            OFFERS_HTML_URL, cookies={"x-aem-variant": store_code}
        ).text

        payload = _extract_offer_template_payload(html)
        if payload is None:
            raise RuntimeError(
                "Kaufland offers page did not contain the expected offer data"
            )

        cycles = payload.get("props", {}).get("offerData", {}).get("cycles", [])

        offers: list[dict[str, Any]] = []
        for cycle in cycles:
            for category in cycle.get("categories", []):
                category_name = category.get("displayName") or category.get("name") or ""
                category_color = category.get("colorCode")
                for offer in category.get("offers", []):
                    # For most offers ``title`` already is the full product
                    # name (``subtitle`` is null). For branded articles
                    # (e.g. house brands like "PARKSIDE®"), ``title`` is only
                    # the brand/manufacturer and the actual article name is
                    # in the separate ``subtitle`` field - combine both so
                    # the offer isn't just labelled with the brand name.
                    brand_or_name = (
                        offer.get("title") or offer.get("detailTitle") or ""
                    ).strip()
                    article_name = (offer.get("subtitle") or "").strip()
                    if article_name and article_name.lower() not in brand_or_name.lower():
                        title = f"{brand_or_name} {article_name}".strip()
                    else:
                        title = brand_or_name
                    if not title:
                        continue
                    discount = offer.get("discount")
                    offers.append(
                        {
                            "kl_nr": offer.get("klNr"),
                            "title": title,
                            "category": category_name,
                            "category_color": category_color,
                            "subtitle": (offer.get("detailDescription") or "").strip(),
                            "price_per_unit": offer.get("unit") or "",
                            "price": str(
                                offer.get("formattedPrice")
                                or offer.get("price")
                                or ""
                            ),
                            "old_price": str(offer.get("formattedOldPrice") or ""),
                            "discount": f"-{discount}%" if discount else "",
                            "image_url": offer.get("listImage") or "",
                            "date_from": offer.get("dateFrom"),
                            "date_to": offer.get("dateTo"),
                        }
                    )

        date_froms = [offer["date_from"] for offer in offers if offer.get("date_from")]
        date_tos = [offer["date_to"] for offer in offers if offer.get("date_to")]
        valid_from = min(date_froms) if date_froms else None
        valid_until = max(date_tos) if date_tos else None

        offers_by_date: dict[str, list[dict[str, Any]]] = {}
        for offer in offers:
            date_from = offer.get("date_from")
            date_to = offer.get("date_to")
            if not date_from:
                continue
            key = date_from if date_from == date_to else f"{date_from} – {date_to}"
            offers_by_date.setdefault(key, []).append(offer)
        offers_by_date = dict(sorted(offers_by_date.items()))

        return {
            "offers": offers,
            "offers_by_date": offers_by_date,
            "valid_from": valid_from,
            "valid_until": valid_until,
        }
