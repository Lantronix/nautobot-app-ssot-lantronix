"""Settings loading. Pure pytest."""

import pytest

from nautobot_ssot_lantronix.settings import ConfigError, load

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
