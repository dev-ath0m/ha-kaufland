# Kaufland Weekly Offers – Home Assistant Integration

A Home Assistant custom integration that shows the current weekly grocery
offers ("Angebote") for your local Kaufland store on your dashboard.

**No Kaufland account or login is required.** The integration only uses
Kaufland's publicly accessible store finder and weekly offers pages –
the same data anyone can see by visiting
[filiale.kaufland.de](https://filiale.kaufland.de) without signing in.

## Features

- Search for your Kaufland store by postcode or city, then pick it from
  a list – just like the existing REWE / Lidl / Norma integrations.
- Automatic discovery of the nearest Kaufland store based on your Home
  Assistant location, if one is within range.
- A sensor with the number of current offers and a `discounts` attribute
  containing the full list (title, category, price, old price, discount,
  price per unit, image) – compatible with common "discounts" Lovelace
  cards.
- Optional per-product "filter" sensors that track whether a specific
  keyword (e.g. "Bananen") is currently on offer and at what price.
- A "Force Update" button to refresh offers on demand.
- Configurable update interval (default: every 24 hours).

## Installation

1. Copy the `custom_components/kaufland` folder into your Home Assistant
   `config/custom_components/` directory (or install via HACS as a
   custom repository).
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration** and search
   for "Kaufland".
4. Enter your postcode or city, then select your store from the list.

## How it works

Kaufland's store locator (`filiale.kaufland.de`) exposes a public JSON
endpoint listing every German store (`.klstorefinder.json`), and the
weekly offers overview page is server-rendered per-store based on a
`x-aem-variant` selection cookie. This integration fetches that public
page for your selected store and parses out the current offers – no
authentication, cookies from a personal account, or app API keys are
involved.

## Disclaimer

This is an unofficial, community-maintained integration and is not
affiliated with or endorsed by Kaufland. Data is scraped from public
pages and may break if Kaufland changes their website structure.
