"""Fakes shared by the pytest and Nautobot-runner tests. No pytest import."""

import logging
from pathlib import Path

from ..percepxion.client import PercepxionUnavailable
from ..percepxion.records import PxDevice, PxPort
from ..settings import SourceConfig

ORG = "org-0001"
FIXTURE = Path(__file__).parent / "fixtures" / "stat_view_1.json"
SYSCONFIG = (
    "Model: SLC9016-16U-16E   Serial Number: 000000000002\n"
    "Power Supply: AC, 2 power supplies   Ethernet Switch: Installed/16 port\n"
)


def device(pxid="dev-0001", name="SLC-A", model="SLC9016", rj45=16, usb=0, serial="000000000001"):
    return PxDevice(pxid, name, serial, model, rj45, usb, frozenset({ORG}), None, "SLC")


def port(number, name=None, pxid="dev-0001", hostname="", **facts):
    return PxPort(pxid, number, name or f"Port-{number:02d}", hostname, facts, frozenset({ORG}))


class FakeClient:
    def __init__(self, devices, ports, states=None, state_error=None, sysconfig=None):
        self.devices, self.ports = devices, ports
        self.states = states or {}
        self.state_error = state_error
        self.sysconfig = sysconfig or {}

    def iter_devices(self):
        return iter(self.devices)

    def iter_ports(self):
        return iter(self.ports)

    def port_states(self, pxid, org=None):
        if self.state_error and pxid in self.state_error:
            raise PercepxionUnavailable("POST /v1/telemetry/stat/view returned HTTP 500")
        return self.states.get(pxid, {})

    def latest_sysconfig_jobs(self, tenant_id=None):
        return {pxid: ("job-1", "t") for pxid in self.sysconfig}

    def cli_output(self, job, pxid):
        return self.sysconfig.get(pxid)


class Job:
    logger = logging.getLogger("test")


def cfg(rules=None):
    return SourceConfig(
        url="https://px.example",
        username="u",
        password="p",
        organization_id=ORG,
        default_location="Lab",
        default_role="Console Server",
        default_status="Active",
        model_rules=rules or {},
    )


RULES = {("SLC9016", 16, 0): "lantronix-slc9016-16e"}
