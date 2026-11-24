"""Settings for the Percepxion source.

Endpoint, organization and credentials come only from environment variables (or the app's
PLUGINS_CONFIG, which the deployment fills from the environment). They are never stored in the
Nautobot database, job inputs or logs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .percepxion.events import usable_secrets
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
    # Read each port's SSH port from the unit's configuration in Percepxion (Open console).
    read_port_config: bool = True
    # (MODEL, rj45, usb) -> device-type slug, for SLC 9000 units with no stored sysconfig.
    model_rules: dict[tuple[str, int, int], str] = field(default_factory=dict)
    # Location names an existing device must be in (or under) to be adopted. Empty means
    # default_location only; ("*",) means any location.
    adoption_locations: tuple[str, ...] = ()
    # Make console cables on synced SLC ports match what each SLC reports (Percepxion becomes
    # the system of record for them). Off by default.
    manage_cables: bool = False
    # Signing secrets for the events URL (webhook-payload v1); empty turns the URL off.
    webhook_secrets: tuple[str, ...] = field(default=(), repr=False)
    # Nautobot user that focused syncs started by events run as.
    sync_user: str = ""


def manage_cables_setting(app_settings: dict, environ: dict | None = None) -> bool:
    """The ``manage_cables`` setting on its own, so the job form can show it without loading
    (and validating) the rest of the settings."""
    env = os.environ if environ is None else environ
    raw = app_settings.get("manage_cables", env.get("PERCEPXION_MANAGE_CABLES", ""))
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _webhook_secrets(app_settings: dict, env) -> tuple[str, ...]:
    raw = app_settings.get("webhook_secrets", env.get("PERCEPXION_WEBHOOK_SECRETS"))
    try:
        return tuple(usable_secrets(raw))
    except ValueError as exc:
        raise ConfigError(f"webhook_secrets (PERCEPXION_WEBHOOK_SECRETS): {exc}") from None


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


def _locations(raw: object) -> tuple[str, ...]:
    if raw in (None, "", [], ()):
        return ()
    if not isinstance(raw, (list, tuple)) or not all(isinstance(x, str) and x for x in raw):
        raise ConfigError("adoption_locations must be a list of location names, or ['*']")
    return tuple(raw)


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
    read_raw = app_settings.get("read_port_config", env.get("PERCEPXION_READ_PORT_CONFIG", "true"))
    read_port_config = str(read_raw).strip().lower() not in ("0", "false", "no")
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
        read_port_config=read_port_config,
        ca_bundle=str(values["ca_bundle"]) or None,
        model_rules=_rules(app_settings.get("model_rules")),
        adoption_locations=_locations(app_settings.get("adoption_locations")),
        manage_cables=manage_cables_setting(app_settings, env),
        webhook_secrets=_webhook_secrets(app_settings, env),
        sync_user=str(app_settings.get("sync_user") or env.get("PERCEPXION_SYNC_USER") or ""),
    )
