"""Constants for the Kaufland Weekly Offers integration."""

DOMAIN = "kaufland"
ATTRIBUTION = "Data provided by Kaufland (public store finder & offers pages)"
PLATFORMS = ["sensor", "button"]

CONF_STORE_CODE = "store_code"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_PRODUCT_FILTERS = "product_filters"

DEFAULT_UPDATE_INTERVAL = 24  # hours
MIN_UPDATE_INTERVAL = 1  # hours
MAX_UPDATE_INTERVAL = 168  # hours (1 week)

# Auto-discovery
DISCOVERY_RADIUS_KM = 20.0

# Sensor attributes
ATTR_DISCOUNTS = "discounts"
ATTR_VALID_FROM = "valid_from"
ATTR_VALID_UNTIL = "valid_until"

ISSUE_ID_CONNECTION = "connection_error"

# Config entry types: a "store" entry tracks public weekly offers for one
# store (no login), an "account" entry links a Kaufland account (OAuth) to
# automatically activate free Kaufland Card XTRA marketplace coupons.
CONF_ENTRY_TYPE = "entry_type"
ENTRY_TYPE_STORE = "store"
ENTRY_TYPE_ACCOUNT = "account"

CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_TOKEN_EXPIRES_AT = "token_expires_at"
CONF_ACCOUNT_EMAIL = "account_email"
CONF_AUTO_ACTIVATE_FREE_COUPONS = "auto_activate_free_coupons"

DEFAULT_AUTO_ACTIVATE_FREE_COUPONS = True
DEFAULT_COUPONS_UPDATE_INTERVAL = 6  # hours

ATTR_COUPONS = "coupons"
ATTR_ACTIVATED_COUPONS = "activated_coupons"
ATTR_LAST_ACTIVATION_ERROR = "last_activation_error"

ISSUE_ID_ACCOUNT_REAUTH = "account_reauth_required"
