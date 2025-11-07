"""SmartApp functionality to receive cloud-push notifications."""
import asyncio
import functools
import logging
import secrets
from urllib.parse import urlparse
from uuid import uuid4

from aiohttp import web
from pysmartapp import Dispatcher, SmartAppManager
from pysmartapp.const import SETTINGS_APP_ID
from pysmartthings import (
    APP_TYPE_WEBHOOK,
    CLASSIFICATION_AUTOMATION,
    App,
    AppOAuth,
    AppSettings,
    InstalledAppStatus,
    SmartThings,
    SourceType,
    Subscription,
    SubscriptionEntity,
)

from homeassistant.components import webhook
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import (
    APP_NAME_PREFIX,
    APP_OAUTH_CLIENT_NAME,
    APP_OAUTH_SCOPES,
    CONF_CLOUDHOOK_URL,
    CONF_INSTALLED_APP_ID,
    CONF_INSTANCE_ID,
    CONF_REFRESH_TOKEN,
    DATA_BROKERS,
    DATA_MANAGER,
    DOMAIN,
    IGNORED_CAPABILITIES,
    SETTINGS_INSTANCE_ID,
    SIGNAL_SMARTAPP_PREFIX,
    STORAGE_KEY,
    STORAGE_VERSION,
    SUBSCRIPTION_WARNING_LIMIT,
)

_LOGGER = logging.getLogger(__name__)


def _has_cloud(hass: HomeAssistant) -> bool:
    """Return True if the cloud component is loaded (Nabu Casa)."""
    return "cloud" in hass.config.components


def format_unique_id(app_id: str, location_id: str) -> str:
    """Format the unique id for a config entry."""
    return f"{app_id}_{location_id}"


async def find_app(hass: HomeAssistant, api):
    """Find an existing SmartApp for this installation of hass."""
    apps = await api.apps()
    for app in [app for app in apps if app.app_name.startswith(APP_NAME_PREFIX)]:
        # Load settings to compare instance id
        settings = await app.settings()
        if (
            settings.settings.get(SETTINGS_INSTANCE_ID)
            == hass.data[DOMAIN][CONF_INSTANCE_ID]
        ):
            return app


async def validate_installed_app(api, installed_app_id: str):
    """Ensure the specified installed SmartApp is valid and functioning."""
    installed_app = await api.installed_app(installed_app_id)
    if installed_app.installed_app_status != InstalledAppStatus.AUTHORIZED:
        raise RuntimeError(
            "Installed SmartApp instance '{}' ({}) is not AUTHORIZED but instead {}".format(
                installed_app.display_name,
                installed_app.installed_app_id,
                installed_app.installed_app_status,
            )
        )
    return installed_app


def validate_webhook_requirements(hass: HomeAssistant) -> bool:
    """Ensure Home Assistant is setup properly to receive webhooks."""
    # jeśli mamy cloudhook zapisany – OK
    if hass.data[DOMAIN].get(CONF_CLOUDHOOK_URL):
        return True
    # jeśli mamy HA z publicznym HTTPS – OK
    try:
        url = get_webhook_url(hass)
    except NoURLAvailableError:
        return False
    return url.lower().startswith("https://")


def get_webhook_url(hass: HomeAssista
