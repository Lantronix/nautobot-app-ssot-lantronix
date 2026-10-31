"""Development Nautobot settings for the Lantronix SSoT app. Values come from the environment."""

import json
import os

from nautobot.core.settings import *  # noqa: F403
from nautobot.core.settings_funcs import parse_redis_connection

ALLOWED_HOSTS = os.getenv("NAUTOBOT_ALLOWED_HOSTS", "*").split()
SECRET_KEY = os.environ["NAUTOBOT_SECRET_KEY"]

DATABASES = {
    "default": {
        "NAME": os.getenv("NAUTOBOT_DB_NAME", "nautobot"),
        "USER": os.getenv("NAUTOBOT_DB_USER", "nautobot"),
        "PASSWORD": os.environ["NAUTOBOT_DB_PASSWORD"],
        "HOST": os.getenv("NAUTOBOT_DB_HOST", "db"),
        "PORT": "5432",
        "ENGINE": "django.db.backends.postgresql",
    }
}
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": parse_redis_connection(redis_database=1),
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}
CELERY_BROKER_URL = parse_redis_connection(redis_database=0)
INSTALLATION_METRICS_ENABLED = False

PLUGINS = ["nautobot_ssot", "nautobot_ssot_lantronix"]
PLUGINS_CONFIG = {
    "nautobot_ssot": {"hide_example_jobs": True},
    "nautobot_ssot_lantronix": {
        # Endpoint, organization and credentials: PERCEPXION_* environment variables.
        "default_location": os.getenv("LANTRONIX_DEFAULT_LOCATION", "Lab"),
        "default_role": "Console Server",
        "default_status": "Active",
        "ssh_base_port": 3000,
        # e.g. {"SLC9016|16|0": "lantronix-slc9016-16e"} for units with no stored sysconfig
        "model_rules": json.loads(os.getenv("LANTRONIX_MODEL_RULES", "{}")),
    },
}
