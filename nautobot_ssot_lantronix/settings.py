"""Settings for the Percepxion source.

Endpoint, organization and credentials come only from environment variables (or the app's
PLUGINS_CONFIG, which the deployment fills from the environment). They are never stored in the
Nautobot database, job inputs or logs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .percepxion.resolver import SLC9000_SLUGS

ENV = {
    "url": "PERCEPXION_URL",
    "username": "PERCEPXION_USERNAME",
    "password": "PERCEPXION_PASSWORD",
    "organization_id": "PERCEPXION_ORG_ID",
    "ca_bundle": "PERCEPXION_CA_BUNDLE",
}


class ConfigError(ValueError):
    """Missing or invalid settings. Messages name settings, never their values."""


@dataclass(frozen=True)
class SourceConfig:
    url: str
    username: str
    password: str = field(repr=False)
    organization_id: str
    default_location: str
    default_role: str
    default_status: str
    ssh_base_port: int = 3000
    verify: bool = True
    ca_bundle: str | None = None
    # (MODEL, rj45, usb) -> device-type slug, for SLC 9000 units with no stored sysconfig.
    model_rules: dict[tuple[str, int, int], str] = field(default_factory=dict)


def _rules(raw: object) -> dict[tuple[str, int, int], str]:
    rules: dict[tuple[str, int, int], str] = {}
    if not raw:
        return rules
    if not isinstance(raw, dict):
        raise ConfigError("model_rules must map 'MODEL|RJ45|USB' to a device-type slug")
    for key, slug in raw.items():
        parts = str(key).split("|")
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            raise ConfigError(f"model_rules key {key!r} is not 'MODEL|RJ45|USB'")
        if slug not in SLC9000_SLUGS:
            raise ConfigError(f"model_rules value for {key!r} is not a Lantronix SLC 9000 type")
        rules[(parts[0].upper(), int(parts[1]), int(parts[2]))] = slug
    return rules


def load(app_settings: dict, environ: dict | None = None) -> SourceConfig:
    env = os.environ if environ is None else environ
    values = {k: (app_settings.get(k) or env.get(v) or "") for k, v in ENV.items()}
    missing = [ENV[k] for k in ("url", "username", "password", "organization_id") if not values[k]]
    if missing:
        raise ConfigError("Missing settings: " + ", ".join(missing))
    if not str(values["url"]).startswith("https://"):
        raise ConfigError("PERCEPXION_URL must use https")
    verify_raw = app_settings.get("verify", env.get("PERCEPXION_VERIFY", "true"))
    verify = str(verify_raw).strip().lower() not in ("0", "false", "no")
    try:
        port = int(app_settings.get("ssh_base_port", 3000))
    except (TypeError, ValueError):
        raise ConfigError("ssh_base_port must be an integer") from None
    return SourceConfig(
        url=str(values["url"]).rstrip("/"),
        username=str(values["username"]),
        password=str(values["password"]),
        organization_id=str(values["organization_id"]),
        default_location=str(app_settings.get("default_location") or ""),
        default_role=str(app_settings.get("default_role") or "Console Server"),
        default_status=str(app_settings.get("default_status") or "Active"),
        ssh_base_port=port,
        verify=verify,
        ca_bundle=str(values["ca_bundle"]) or None,
        model_rules=_rules(app_settings.get("model_rules")),
    )
