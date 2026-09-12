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
