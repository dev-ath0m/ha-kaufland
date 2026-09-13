"""Client for the Kaufland account (Cidaas OAuth) and marketplace coupons API.

Kaufland's official Android app authenticates against Kaufland's own Cidaas
identity provider (`account.kaufland.com`) using the standard OAuth 2.0
Authorization Code flow with PKCE, then calls an authenticated backend
(`shop-mobile-bff.cloud.kaufland.de`) to read and activate "Kaufland Card
XTRA" marketplace coupons.

There is no password-grant available for this client, and the login page
itself must be completed in a real browser (Cidaas's hosted login UI), so
this integration asks the user to open the authorize URL themselves, log in,
and paste back the (non-resolving) app redirect URL that Cidaas sends the
browser to afterwards - the `code` query parameter is all that's needed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

CIDAAS_CLIENT_ID = "fb1b425b-ab2f-4140-aef9-20263b6cfa49"
CIDAAS_AUTHORIZE_URL = "https://account.kaufland.com/authz-srv/authz"
CIDAAS_TOKEN_URL = "https://account.kaufland.com/token-srv/token"
CIDAAS_REDIRECT_URI = "com.kaufland.kaufland://oauth/callback/marketplace"
CIDAAS_SCOPES = "openid profile email offline_access"

COUPONS_API_BASE_URL = "https://shop-mobile-bff.cloud.kaufland.de"

# Kaufland Card XTRA in-store/loyalty coupons live behind a completely
# different backend than marketplace coupons (found by decompiling the
# Android app - the older shop-mobile-bff `/coupons` endpoint used to be
# tried here requires a session cookie even for read access and there is
# no way to tell real, currently-activatable coupons apart from inactive
# "Deal des Tages" style previews via that feed). This one only needs the
# OAuth bearer token plus a `countryCode` header and exposes a
# `buttonActive` flag that reliably identifies real, activatable coupons.
# Confirmed live 2026-09.
LOYALTY_API_BASE_URL = "https://app.kaufland.net"


class KauflandAuthError(Exception):
    """Raised when authentication with Kaufland's account service fails."""


class KauflandCouponsApiError(Exception):
    """Raised when the coupons API returns an unexpected response."""


def generate_pkce_pair() -> tuple[str, str]:
    """Return a fresh (code_verifier, code_challenge) PKCE pair (S256)."""
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def generate_state() -> str:
    """Return a fresh random OAuth `state` value."""
    return secrets.token_urlsafe(24)


def build_authorize_url(*, state: str, code_challenge: str) -> str:
    """Build the Cidaas authorize URL for the user to open in a browser."""
    params = {
        "client_id": CIDAAS_CLIENT_ID,
        "response_type": "code",
        "scope": CIDAAS_SCOPES,
        "redirect_uri": CIDAAS_REDIRECT_URI,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return f"{CIDAAS_AUTHORIZE_URL}?{query}"


def extract_code_from_input(user_input: str) -> str:
    """Extract the `code` query parameter from a pasted redirect URL.

    Accepts either the full (non-resolving) redirect URL Cidaas sent the
    browser to, or a bare authorization code, for user convenience.
    """
    user_input = user_input.strip()
    if "code=" not in user_input:
        return user_input

    after = user_input.split("code=", 1)[1]
    for sep in ("&", "#"):
        if sep in after:
            after = after.split(sep, 1)[0]
    return after


_ALTSESSID_COOKIE_PATTERN = re.compile(r"ALTSESSID=([^;\s]+)", re.IGNORECASE)


def normalize_instore_session_cookie(raw: str) -> str:
    """Best-effort extraction of a bare ALTSESSID value from user input.

    Users are asked to copy this cookie value out of their own browser's
    cookie inspector (see the account options flow), so accept whatever
    shape they end up pasting: a bare value, an ``ALTSESSID=<value>`` pair,
    or a full DevTools "Cookie" row/header containing other cookies too.
    """
    raw = raw.strip()
    match = _ALTSESSID_COOKIE_PATTERN.search(raw)
    return match.group(1) if match else raw


def _decode_id_token(id_token: str) -> dict[str, Any]:
    """Best-effort decode of an id_token payload (no signature check needed).

    This is only used to read display fields (email/name) already obtained
    over a direct TLS connection to Kaufland's own token endpoint - it does
    not grant any additional trust the token exchange didn't already provide.
    """
    try:
        payload_b64 = id_token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Could not decode Kaufland id_token: %s", exc)
        return {}


class KauflandAuth:
    """Handles the Cidaas OAuth token lifecycle for a linked account."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        """Exchange an authorization code for tokens."""
        data = {
            "grant_type": "authorization_code",
            "client_id": CIDAAS_CLIENT_ID,
            "code": code,
            "redirect_uri": CIDAAS_REDIRECT_URI,
            "code_verifier": code_verifier,
        }
        return await self._post_token(data)

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        """Refresh an access token using a refresh token."""
        data = {
            "grant_type": "refresh_token",
            "client_id": CIDAAS_CLIENT_ID,
            "refresh_token": refresh_token,
        }
        return await self._post_token(data)

    async def _post_token(self, data: dict[str, str]) -> dict[str, Any]:
        try:
            async with self._session.post(CIDAAS_TOKEN_URL, data=data) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise KauflandAuthError(
                        f"Kaufland token request failed ({resp.status}): {body}"
                    )
                payload: dict[str, Any] = json.loads(body)
        except aiohttp.ClientError as exc:
            raise KauflandAuthError(f"Kaufland token request failed: {exc}") from exc

        payload["expires_at"] = time.time() + float(payload.get("expires_in", 3600))
        id_token = payload.get("id_token")
        if id_token:
            claims = _decode_id_token(id_token)
            payload["sub"] = claims.get("sub")
            payload["email"] = claims.get("email")
            payload["given_name"] = claims.get("given_name")
        return payload


class KauflandCouponsClient:
    """Client for the authenticated marketplace coupons API."""

    def __init__(self, session: aiohttp.ClientSession, access_token: str) -> None:
        self._session = session
        self._access_token = access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    async def get_marketplace_coupons(self) -> dict[str, Any]:
        """Return the raw marketplace coupons payload for the linked account."""
        url = f"{COUPONS_API_BASE_URL}/coupons/marketplaceCoupons"
        try:
            async with self._session.get(url, headers=self._headers()) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise KauflandCouponsApiError(
                        f"Kaufland coupons request failed ({resp.status}): {body}"
                    )
                return json.loads(body)
        except aiohttp.ClientError as exc:
            raise KauflandCouponsApiError(f"Kaufland coupons request failed: {exc}") from exc

    async def get_loyalty_coupons(self, country: str = "DE") -> dict[str, Any]:
        """Return the raw loyalty coupons payload (in-store + marketplace).

        This is the endpoint Kaufland's own app uses to list "Kaufland Card
        XTRA" coupons (``storeCoupons`` are the in-store/regular ones,
        ``marketplaceCoupons`` duplicate what ``get_marketplace_coupons``
        already returns). It only needs the OAuth bearer token - no session
        cookie required, unlike marketplace coupon *activation*.
        """
        url = f"{LOYALTY_API_BASE_URL}/kapp/api/v1/loyalty/coupons"
        headers = {**self._headers(), "countryCode": country}
        try:
            async with self._session.get(url, headers=headers) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise KauflandCouponsApiError(
                        f"Kaufland loyalty coupons request failed ({resp.status}): {body}"
                    )
                return json.loads(body)
        except aiohttp.ClientError as exc:
            raise KauflandCouponsApiError(
                f"Kaufland loyalty coupons request failed: {exc}"
            ) from exc

    async def activate_stationary_coupon(
        self,
        coupon_number: str,
        exchange_rule_number: str | None = None,
        country: str = "DE",
    ) -> dict[str, Any]:
        """Activate a single in-store (stationary) Kaufland Card XTRA coupon.

        Confirmed live 2026-09: in-store coupons are *not* activated
        through the marketplace ``/coupons/activate`` endpoint - that
        returns a misleading "Coupon was already redeemed" error for
        stationary coupon numbers, even ones never activated before.
        Instead they go through an event-based modification API on a
        different backend (``app.kaufland.net``), requiring only the OAuth
        bearer token (no session cookie).
        """
        url = f"{LOYALTY_API_BASE_URL}/kapp/api/v1/loyalty/coupon/modify"
        headers = {**self._headers(), "countryCode": country}
        body = {
            "Country": country,
            "Events": [
                {
                    "EventType": 0,  # ACTIVATE
                    "ExchangeRuleNumber": exchange_rule_number or "",
                    "Gcn": coupon_number,
                    "RedemptionPoint": "",
                    "Timestamp": datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%S.000Z"
                    ),
                }
            ],
        }
        try:
            async with self._session.post(url, headers=headers, json=body) as resp:
                text = await resp.text()
                if resp.status not in (200, 201):
                    raise KauflandCouponsApiError(
                        f"Kaufland in-store coupon activation failed for "
                        f"{coupon_number} ({resp.status}): {text}"
                    )
                return json.loads(text) if text else {}
        except aiohttp.ClientError as exc:
            raise KauflandCouponsApiError(
                f"Kaufland in-store coupon activation failed for {coupon_number}: {exc}"
            ) from exc

    async def activate_coupon(
        self,
        coupon_number: str,
        exchange_rule_number: str | None,
        session_cookie: str | None = None,
    ) -> dict[str, Any]:
        """Activate a single marketplace coupon by its coupon number (gcn).

        Confirmed live 2026-09: Kaufland's backend requires a session
        cookie (``ALTSESSID``) for this endpoint. The user must supply one
        via the account options (``normalize_instore_session_cookie``
        handles common paste shapes) - without it this call reliably fails
        with ``400 Missing session cookie``. Note this is unrelated to
        in-store coupon activation, which uses a different backend that
        needs only the OAuth bearer token (see ``activate_stationary_coupon``).
        """
        url = f"{COUPONS_API_BASE_URL}/coupons/activate"
        headers = self._headers()
        if session_cookie:
            headers["Cookie"] = f"ALTSESSID={session_cookie}"
        body = {
            "coupon_number": coupon_number,
            "exchange_rule_number": exchange_rule_number,
        }
        try:
            async with self._session.post(
                url, headers=headers, json=body
            ) as resp:
                text = await resp.text()
                if resp.status not in (200, 201):
                    raise KauflandCouponsApiError(
                        f"Kaufland coupon activation failed for {coupon_number} "
                        f"({resp.status}): {text}"
                    )
                return json.loads(text) if text else {}
        except aiohttp.ClientError as exc:
            raise KauflandCouponsApiError(
                f"Kaufland coupon activation failed for {coupon_number}: {exc}"
            ) from exc
