<div align="center">
  <h1>Kaufland Weekly Offers & Coupons (for Home Assistant) 🛒</h1>

  <p><strong>A Home Assistant integration that fetches weekly grocery offers for your local Kaufland store and, optionally, auto-activates your free Kaufland Card XTRA / Marketplace coupons.</strong></p>

  [![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://hacs.xyz)
  [![License](https://img.shields.io/github/license/dev-ath0m/ha-kaufland?style=for-the-badge)](#-license)
</div>

---

## 🧬 Origin & AI Port

This project is a **community port**, built on top of the architecture,
config-flow patterns, device/entity conventions, and Lovelace/HACS setup
established by **[FaserF's](https://github.com/FaserF)** supermarket
integrations — [`ha-lidl`](https://github.com/FaserF/ha-lidl) and
[`ha-rewe`](https://github.com/FaserF/ha-rewe) in particular. Those
upstream projects don't support Kaufland, so this repository
re-implements the same overall structure against **Kaufland's own APIs**
instead: the public store-finder/weekly-offers pages, and the Kaufland
Card XTRA/Marketplace coupon APIs used by the official Kaufland app. The
Kaufland-specific reverse-engineering and integration code was written
and adapted with AI assistance.

It is **not affiliated with FaserF or Kaufland**, but is deliberately
built to slot into the same "supermarket family" of Home Assistant
integrations — the same install flow, the same "Force Update" button
convention, and the same `discounts`/offers sensor shape, so it works
out of the box alongside them on the same dashboard (see
[Lovelace Cards](#-lovelace-cards) below).

## 🧭 Quick Links

| | | | |
| :--- | :--- | :--- | :--- |
| [✨ Features](#-features) | [📦 Installation](#-installation) | [⚙️ Configuration](#️-configuration) | [🔐 Account Login](#-kaufland-account-login-optional) |
| [🛠️ Options](#️-options-flow) | [🃏 Lovelace Cards](#-lovelace-cards) | [💖 Credits](#-credits--acknowledgements) | [📄 License](#-license) |

### 🛒 Supermarket Family & Deals Hub

This integration follows the same conventions as FaserF's supermarket
integrations, so it fits naturally alongside them on the same dashboard:

| Repository | Description |
| :--- | :--- |
| 🏷️ [**ha-grocery-deals**](https://github.com/FaserF/ha-grocery-deals) | Smart multi-store price comparison hub (aggregates the integrations below) |
| 🔴 [**ha-rewe**](https://github.com/FaserF/ha-rewe) | REWE weekly offers, bonus points, coupons & product filters |
| 🟡 [**ha-edeka**](https://github.com/FaserF/ha-edeka) | EDEKA weekly offers, discounts & PAYBACK card |
| 🔵 [**ha-lidl**](https://github.com/FaserF/ha-lidl) | Lidl Plus weekly offers, coupons & digital receipts |
| ⚪ [**ha-aldi**](https://github.com/FaserF/ha-aldi) | ALDI Süd & ALDI Nord weekly flyers & brochures |
| 🟢 [**ha-norma**](https://github.com/FaserF/ha-norma) | Norma weekly store discounts & flyer offers |
| 🟠 [**ha-kaufland**](https://github.com/dev-ath0m/ha-kaufland) *(this repo)* | Kaufland weekly offers + Kaufland Card XTRA/Marketplace coupon auto-activation |

---

### Why use this integration?

**No Kaufland account is required** for the base weekly-offers feature —
it uses the same public store-finder and offers pages anyone can see on
[filiale.kaufland.de](https://filiale.kaufland.de) without logging in.
Optionally linking your Kaufland account additionally unlocks automatic
activation of free Kaufland Card XTRA and Marketplace coupons, using the
same OAuth (PKCE) flow as the official Kaufland app.

---

## ✨ Features

- **🛒 Weekly Offers Sensor**: current week's offer count, with a
  `discounts` attribute listing every offer (title, category, price, old
  price, discount, price per unit, product image) — compatible with the
  [Discounts Card](#-lovelace-cards) and similar Lovelace cards.
- **🔍 Product Filter Sensors**: optional sensors that track whether a
  specific keyword (e.g. "Bananen") is currently on offer, and at what
  price.
- **📍 Automatic Store Discovery**: if a Kaufland store is within range of
  your Home Assistant location, a discovery flow offers to add it
  automatically.
- **🎟️ Kaufland Account Features** *(optional, requires login)*:
  - **Available/Active Coupons**: sensors for Marketplace coupons and
    in-store Kaufland Card XTRA coupons, split into available and
    active/activated. Coupons that are fetched but not usable yet (e.g.
    a future-dated "Deal des Tages" preview) are excluded from these
    counts.
  - **Upcoming Coupons**: a display-only sensor listing coupons that have
    already appeared in the feed but aren't usable yet (Marketplace and
    in-store combined) — handy to see what's coming up next.
  - **Automatic Coupon Activation**: free coupons (0 loyalty points) are
    activated automatically on each refresh; coupons that cost loyalty
    points are never touched.
  - **Activate All Coupons Button**: manually trigger an activation pass
    on demand.
  - **Repair Issues**: a repair notification is raised if your account
    login expires, or if the manually-provided Marketplace session
    cookie becomes invalid, with instructions to fix it.
- **📱 Single Grouped Device Entry**: like `ha-lidl`, a store and its
  linked account live under **one** config entry — you can link or
  unlink the account at any time via **Options**, instead of managing two
  unrelated entries.
- **🎛️ Manual Force Update**: a "Force Update" button entity refreshes
  the weekly offers on demand.
- **💾 Restart-Resistant Caching**: the last successfully fetched offers
  are cached to Home Assistant storage, so a restart doesn't show
  "unavailable" while waiting for the next refresh.
- **⚙️ Configurable Update Interval**: 1–168 hours (default 24h) for
  weekly offers; coupons refresh independently, every 6 hours.

---

## 📦 Installation

### HACS (Custom Repository)

This integration is not (yet) in the default HACS store, so add it as a
custom repository:

1. Open HACS in Home Assistant.
2. Click the three dots in the top right corner and select **Custom repositories**.
3. Add `dev-ath0m/ha-kaufland` with category **Integration**.
4. Search for **Kaufland Weekly Offers** and click **Download**.
5. Restart Home Assistant.

### Manual Installation

1. Download the latest source (or clone this repository).
2. Copy the `custom_components/kaufland` folder into your Home
   Assistant's `custom_components` directory.
3. Restart Home Assistant.

---

## ⚙️ Configuration

1. Navigate to **Settings → Devices & Services** in Home Assistant.
2. Click **Add Integration** and search for **Kaufland**.

[![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=kaufland)

3. Choose **Search for a store**, enter your postcode or city to find
   nearby Kaufland stores, then select yours from the list.
4. *(Optional)* Check **Also link my Kaufland account** to enable coupon
   auto-activation as part of the same setup — see below.
5. Submit to create the device and entities.

Alternatively, choose **Link a Kaufland account** from the initial menu
to link only an account (no store), for pure coupon tracking.

---

## 🔐 Kaufland Account Login *(optional)*

Linking your Kaufland account enables the coupon sensors and automatic
activation described above. Home Assistant cannot open Kaufland's mobile
app OAuth redirect directly, so a short manual copy/paste step is
required (the same pattern `ha-lidl` uses for its browser-based login):

1. During setup (or later via **Options → Link a Kaufland account**),
   Home Assistant shows you an **authorization link**.
2. Open it in a browser and log in with your Kaufland account.
3. After logging in, the page will fail to load — this is expected (the
   redirect points at a mobile-app URL scheme the browser can't open).
4. Copy the full URL from the address bar and paste it back into Home
   Assistant.

### Marketplace coupon activation cookie *(optional, advanced)*

Activating **Marketplace** coupons additionally requires a session
cookie (`ALTSESSID`) that only a real logged-in browser session can
obtain (Kaufland protects this endpoint with bot detection this
integration deliberately does not try to bypass). In-store Kaufland Card
XTRA coupons do **not** need this and work from the OAuth login alone.

1. Log into [www.kaufland.de](https://www.kaufland.de) in a normal
   browser.
2. Open DevTools → **Application/Storage → Cookies**.
3. Copy the value of the cookie named **`ALTSESSID`**.
4. Paste it into **Options → Coupon account settings → Session cookie**.

This cookie is short-lived; when it expires, a repair issue will prompt
you to refresh it.

---

## 🛠️ Options Flow

1. Go to **Settings → Devices & Services**.
2. Find your **Kaufland** entry and click **Configure**.
3. Choose from the menu:
   - **Weekly offers settings**: update interval, product filters.
   - **Coupon account settings**: toggle auto-activation, set/update the
     Marketplace session cookie *(only shown once an account is linked)*.
   - **Link a Kaufland account**: *(only shown if no account is linked yet)*.

---

## 🃏 Lovelace Cards

The `discounts` sensor attribute is compatible with community "discounts"
cards. Since the upstream card's shop-name detection didn't recognize
Kaufland, a small fork adds that support:

[![Discounts Card (Kaufland fork)](https://img.shields.io/badge/Lovelace-%20Discounts%20Card%20(Kaufland%20fork)-brightgreen?style=for-the-badge&logo=home-assistant)](https://github.com/dev-ath0m/lovelace-groceries-kaufland-fix)

That repository is itself just a small fork of
[`schblondie/discounts-card`](https://github.com/schblondie/discounts-card)
that adds Kaufland recognition — see its README for details and
installation instructions; all other card features come from the
original project.

---

## 💖 Credits & Acknowledgements

- **[FaserF](https://github.com/FaserF)** — original author of `ha-lidl`,
  `ha-rewe`, and the wider supermarket-integration family this project's
  structure, config-flow UX, and README are ported from.
- **[schblondie/discounts-card](https://github.com/schblondie/discounts-card)**
  — the Lovelace card this integration's `discounts` sensor attribute is
  designed to be compatible with.
- Kaufland's public API endpoints and Kaufland Card XTRA/Marketplace
  coupon APIs were identified through reverse-engineering of the official
  Kaufland Android app, with AI assistance.

## ⚠️ Disclaimer

This is an unofficial, community-maintained integration and is not
affiliated with or endorsed by Kaufland or FaserF. Data is fetched from
public/official-app endpoints and may break if Kaufland changes them.

## 📄 License

No formal license file has been added to this repository yet. Until one
is added, treat this code in the spirit of the MIT-licensed upstream
projects it is ported from (`ha-lidl`, `ha-rewe`) — free to use and
modify, provided "as is" with no warranty.
