"""Pure Python client for Kaufland's public store finder & weekly offers pages.

Kaufland does not require any personal login to browse public store
information or the weekly "Angebote" (offers) overview for a given store.
This client mirrors that public, anonymous access:

- ``.klstorefinder.json`` returns the full national list of Kaufland stores
  (id, name, address, postcode, city, coordinates, opening hours, ...).
- The rendered ``/angebote/uebersicht.html`` page is server-side rendered
  with the currently valid offers for whichever store is selected via the
  ``x-aem-variant`` cookie (e.g. ``DE2553``). No AJAX/JSON product-detail
  API is exposed publicly, so offer details (title, price, discount, image)
  are parsed directly out of the server-rendered HTML.
- ``.kloffers.storeName={code}.json`` returns the validity date range
  (``dateFrom``/``dateTo``) for the current batch of offers for a store.

If anything fails, methods raise ``RuntimeError`` rather than silently
returning fabricated placeholder data.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from curl_cffi import requests
from pydantic import BaseModel, ConfigDict, Field

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://filiale.kaufland.de"
STOREFINDER_URL = f"{BASE_URL}/.klstorefinder.json"
OFFERS_HTML_URL = f"{BASE_URL}/angebote/uebersicht.html"
OFFERS_META_URL_TEMPLATE = f"{BASE_URL}/.kloffers.storeName={{store_code}}.json"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_TILE_RE = re.compile(r'<a class="k-product-tile"[^>]*>(.*?)</a>', re.DOTALL)
_HEADING_RE = re.compile(
    r'<h2 class="k-product-section__headline[^"]*">([^<]*)</h2>'
)
_IMG_SRC_RE = re.compile(r'<img[^>]+src="([^"]+)"')
_IMG_ALT_RE = re.compile(r'<img[^>]+alt="([^"]*)"')
_TITLE_RE = re.compile(r'class="k-product-tile__title">([^<]*)<')
_SUBTITLE_RE = re.compile(r'class="k-product-tile__subtitle">([^<]*)<')
_UNIT_PRICE_RE = re.compile(r'class="k-product-tile__unit-price">([^<]*)<')
_DISCOUNT_RE = re.compile(r'class="k-price-tag__discount">([^<]*)<')
_PRICE_RE = re.compile(r'class="k-price-tag__price">([^<]*)<')
_OLD_PRICE_RE = re.compile(
    r'class="k-price-tag__old-price-line-through">([^<]*)<'
)
_KLNR_RE = re.compile(r"/is/image/schwarz/([A-Za-z0-9]+)")


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

    def _get_offer_validity(self, store_code: str) -> tuple[str | None, str | None]:
        """Return (valid_from, valid_until) for the current offers batch."""
        url = OFFERS_META_URL_TEMPLATE.format(store_code=store_code)
        try:
            response = self._get(url)
            data = response.json()
        except Exception as exc:
            _LOGGER.debug("Kaufland offers validity lookup failed: %s", exc)
            return None, None

        if isinstance(data, list) and data:
            first = data[0]
            return first.get("dateFrom"), first.get("dateTo")
        return None, None

    def get_offers(self, store_code: str) -> dict[str, Any]:
        """Fetch and parse the currently valid weekly offers for a store."""
        html = self._get(
            OFFERS_HTML_URL, cookies={"x-aem-variant": store_code}
        ).text

        headings = [(m.start(), m.group(1).strip()) for m in _HEADING_RE.finditer(html)]
        heading_idx = 0

        offers: list[dict[str, Any]] = []
        for match in _TILE_RE.finditer(html):
            block = match.group(1)
            pos = match.start()

            category = ""
            while heading_idx < len(headings) and headings[heading_idx][0] <= pos:
                category = headings[heading_idx][1]
                heading_idx += 1

            price_match = _PRICE_RE.search(block)
            title_match = _TITLE_RE.search(block)
            if not price_match or not title_match or not title_match.group(1).strip():
                continue

            img_src_match = _IMG_SRC_RE.search(block)
            img_src = img_src_match.group(1) if img_src_match else ""
            kl_nr_match = _KLNR_RE.search(img_src)

            alt_match = _IMG_ALT_RE.search(block)
            subtitle_match = _SUBTITLE_RE.search(block)
            unit_price_match = _UNIT_PRICE_RE.search(block)
            discount_match = _DISCOUNT_RE.search(block)
            old_price_match = _OLD_PRICE_RE.search(block)

            offers.append(
                {
                    "kl_nr": kl_nr_match.group(1) if kl_nr_match else None,
                    "title": title_match.group(1).strip()
                    or (alt_match.group(1).strip() if alt_match else ""),
                    "category": category,
                    "subtitle": subtitle_match.group(1).strip()
                    if subtitle_match
                    else "",
                    "price_per_unit": unit_price_match.group(1).strip()
                    if unit_price_match
                    else "",
                    "price": price_match.group(1).strip(),
                    "old_price": old_price_match.group(1).strip()
                    if old_price_match
                    else "",
                    "discount": discount_match.group(1).strip()
                    if discount_match
                    else "",
                    "image_url": img_src,
                }
            )

        valid_from, valid_until = self._get_offer_validity(store_code)

        return {
            "offers": offers,
            "valid_from": valid_from,
            "valid_until": valid_until,
        }
