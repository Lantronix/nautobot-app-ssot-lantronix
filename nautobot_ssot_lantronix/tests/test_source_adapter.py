"""Percepxion source adapter with a fake client."""

import json
import logging

import pytest

from nautobot_ssot_lantronix.diffsync.models import LantronixConsolePort, LantronixDevice
from nautobot_ssot_lantronix.diffsync.percepxion import PercepxionAdapter
from nautobot_ssot_lantronix.percepxion.records import PxDevice
from nautobot_ssot_lantronix.percepxion.telemetry import parse_port_states
from nautobot_ssot_lantronix.tests.fakes import (
    FIXTURE,
    RULES,
    SYSCONFIG,
    FakeClient,
    Job,
    cfg,
    device,
    port,
)


def load(client, rules=None):
    adapter = PercepxionAdapter(job=Job(), config=cfg(rules), client=client)
    adapter.load()
    return adapter


def get_port(adapter, number, pxid="dev-0001"):
    return adapter.get(LantronixConsolePort, {"percepxion_device_id": pxid, "number": number})


def test_ambiguous_model_skipped_and_logged(caplog):
    with caplog.at_level(logging.WARNING):
        a = load(FakeClient([device()], []))
    assert a.get_all(LantronixDevice) == []
    assert "ambiguous model" in caplog.text and "show sysconfig" in caplog.text


def test_sysconfig_resolves_exact_type():
    client = FakeClient([device(model="SLC9032", usb=16)], [], sysconfig={"dev-0001": SYSCONFIG})
    dev = load(client).get(LantronixDevice, {"percepxion_device_id": "dev-0001"})
    assert dev.device_type_slug == "lantronix-slc9016-16u-16e"


def test_model_rule_resolves_type():
    rules = {("SLC9016", 16, 0): "lantronix-slc9016-16e"}
    dev = load(FakeClient([device()], []), rules).get(
        LantronixDevice, {"percepxion_device_id": "dev-0001"}
    )
    assert dev.device_type_slug == "lantronix-slc9016-16e"


def test_emg_maps_to_sku():
    emg = device("dev-0002", "EMG-A", model="EMG851101", rj45=8)
    dev = load(FakeClient([emg], [])).get(LantronixDevice, {"percepxion_device_id": "dev-0002"})
    assert dev.device_type_slug == "lantronix-emg851110s"


def test_other_org_and_duplicate_names_skipped():
    other = PxDevice("dev-9", "SLC-X", "", "EMG851101", 8, 0, frozenset({"org-2"}), None, "")
    dup1 = device("dev-0002", "EMG-A", model="EMG851101", rj45=8)
    dup2 = device("dev-0003", "EMG-A", model="EMG851101", rj45=8)
    assert load(FakeClient([other, dup1, dup2], [])).get_all(LantronixDevice) == []


def fixture_slc(**kw):
    states = {"dev-0001": parse_port_states(json.loads(FIXTURE.read_text()))}
    ports = [port(1, name="console-a"), port(2, hostname="Switch", model="WS-C3560CG-8TC-S")]
    ports += [port(3), port(14, name="switch-01", hostname="switch-01")]
    return load(FakeClient([device()], ports, states=states, **kw), RULES)


def test_port_label_follows_device_name_and_default_is_empty():
    a = fixture_slc()
    assert get_port(a, 1).label == "console-a"
    assert get_port(a, 3).label == ""


def test_managed_port_carries_attached_device():
    p = get_port(fixture_slc(), 14)
    assert (p.state, p.attached, p.attached_type) == ("Managed", True, "Cisco IOS")
    assert (p.attached_model, p.attached_hostname, p.description) == (
        "WS-C3560CG-8TC-S",
        "switch-01",
        "switch-01",
    )
    assert p.attached_ip == "192.0.2.57"


def test_leftover_port_shows_nothing_attached_despite_port_search_facts():
    p = get_port(fixture_slc(), 2)
    assert (p.state, p.attached, p.attached_model, p.description) == ("Disconnected", False, "", "")


def test_identifying_port_has_state_only():
    p = get_port(fixture_slc(), 3)
    assert (p.state, p.attached, p.attached_model) == ("Identifying", False, "")


def test_ports_are_children_of_their_device():
    a = fixture_slc()
    dev = a.get(LantronixDevice, {"percepxion_device_id": "dev-0001"})
    assert len(dev.console_ports) == 4


def test_telemetry_failure_leaves_that_devices_ports_out(caplog):
    with caplog.at_level(logging.WARNING):
        a = fixture_slc(state_error={"dev-0001"})
    assert a.get(LantronixDevice, {"percepxion_device_id": "dev-0001"})
    assert a.get_all(LantronixConsolePort) == []
    assert "Port state unavailable for dev-0001" in caplog.text


@pytest.mark.parametrize("bad", ["SLC A", "x" * 200, "<script>"])
def test_unsafe_device_names_skipped(bad):
    assert (
        load(FakeClient([device(name=bad, model="EMG851101")], [])).get_all(LantronixDevice) == []
    )


def test_unusable_serial_rejects_device_instead_of_blanking_it():
    bad = device(model="EMG851101", rj45=8, serial="0001<script>")
    assert load(FakeClient([bad], [])).get_all(LantronixDevice) == []


def test_absent_serial_still_loads():
    ok = device(model="EMG851101", rj45=8, serial="")
    dev = load(FakeClient([ok], [])).get(LantronixDevice, {"percepxion_device_id": "dev-0001"})
    assert dev.serial == ""


def test_device_with_no_organization_is_skipped_and_not_named(caplog):
    orphan = PxDevice("dev-7", "SECRET-NAME", "", "EMG851101", 8, 0, frozenset(), None, "")
    with caplog.at_level(logging.INFO):
        a = load(FakeClient([orphan], []))
    assert a.get_all(LantronixDevice) == []
    assert "SECRET-NAME" not in caplog.text and "1 device(s) outside" in caplog.text
