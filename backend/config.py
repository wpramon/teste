import os

NETWORK_CODE = os.getenv("GAM_NETWORK_CODE", "95377733")

APPLICATION_NAME = os.getenv(
    "GAM_APPLICATION_NAME",
    "GAM Line Item Copier"
)

API_VERSION = os.getenv(
    "GAM_API_VERSION",
    "v202608"
)

ADMANAGER_SCOPE = "https://www.googleapis.com/auth/admanager"
