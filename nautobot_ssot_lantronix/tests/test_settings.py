"""Settings loading. Pure pytest."""

import pytest

from nautobot_ssot_lantronix.settings import ConfigError, load, manage_cables_setting

ENV = {
    "PERCEPXION_URL": "https://px.example/api",
    "PERCEPXION_USERNAME": "svc",
    "PERCEPXION_PASSWORD": "s3cret-value",
    "PERCEPXION_ORG_ID": "org-0001",
}


def test_loads_from_environment_with_defaults():
    cfg = load({"default_location": "Lab"}, ENV)
    assert (cfg.url, cfg.organization_id, cfg.verify) == (
        "https://px.example/api",
        "org-0001",
        True,
    )
    assert cfg.adoption_locations == ()
    assert "s3cret-value" not in repr(cfg)


def test_missing_settings_are_named_without_values():
    with pytest.raises(ConfigError) as exc:
        load({}, {**ENV, "PERCEPXION_PASSWORD": ""})
    assert "PERCEPXION_PASSWORD" in str(exc.value) and "s3cret-value" not in str(exc.value)


def test_http_url_refused():
    with pytest.raises(ConfigError, match="https"):
        load({}, {**ENV, "PERCEPXION_URL": "http://px.example/api"})


@pytest.mark.parametrize(
    "raw,expected", [("false", False), ("0", False), ("no", False), ("true", True)]
)
def test_verify_from_environment(raw, expected):
    assert load({}, {**ENV, "PERCEPXION_VERIFY": raw}).verify is expected


def test_adoption_locations():
    assert load({"adoption_locations": ["Lab", "DC1"]}, ENV).adoption_locations == ("Lab", "DC1")
    assert load({"adoption_locations": ["*"]}, ENV).adoption_locations == ("*",)
    for bad in ("Lab", [""], [1]):
        with pytest.raises(ConfigError, match="adoption_locations"):
            load({"adoption_locations": bad}, ENV)


@pytest.mark.parametrize(
    ("raw", "expected"), [("false", False), ("0", False), ("no", False), ("true", True), ("", True)]
)
def test_read_port_config_from_environment(raw, expected):
    assert load({}, {**ENV, "PERCEPXION_READ_PORT_CONFIG": raw}).read_port_config is expected


def test_read_port_config_is_on_by_default_and_app_settings_win():
    assert load({}, ENV).read_port_config is True
    env = {**ENV, "PERCEPXION_READ_PORT_CONFIG": "true"}
    assert load({"read_port_config": False}, env).read_port_config is False


def test_manage_cables_is_off_by_default():
    assert load({}, ENV).manage_cables is False
    assert manage_cables_setting({}, {}) is False


@pytest.mark.parametrize(
    ("app", "env", "expected"),
    [
        ({"manage_cables": True}, "", True),
        ({"manage_cables": False}, "true", False),  # the app setting wins
        ({}, "true", True),
        ({}, "no", False),
    ],
)
def test_manage_cables_from_app_settings_or_environment(app, env, expected):
    assert load(app, {**ENV, "PERCEPXION_MANAGE_CABLES": env}).manage_cables is expected


SECRET = "w" * 40


def test_events_are_off_by_default():
    cfg = load({}, ENV)
    assert (cfg.webhook_secrets, cfg.sync_user) == ((), "")


def test_webhook_secrets_and_sync_user_from_app_settings_or_environment():
    cfg = load({"webhook_secrets": [SECRET], "sync_user": "px-sync"}, ENV)
    assert (cfg.webhook_secrets, cfg.sync_user) == ((SECRET,), "px-sync")
    env = {
        **ENV,
        "PERCEPXION_WEBHOOK_SECRETS": f"{SECRET}, {'v' * 32}",
        "PERCEPXION_SYNC_USER": "u",
    }
    cfg = load({}, env)
    assert (cfg.webhook_secrets, cfg.sync_user) == ((SECRET, "v" * 32), "u")
    assert SECRET not in repr(cfg)


@pytest.mark.parametrize("raw", ["short", ["w" * 40, "x"], {"a": 1}, [1]])
def test_bad_webhook_secrets_are_refused_without_their_value(raw):
    with pytest.raises(ConfigError) as exc:
        load({"webhook_secrets": raw}, ENV)
    assert "webhook_secrets" in str(exc.value) and "short" not in str(exc.value)
