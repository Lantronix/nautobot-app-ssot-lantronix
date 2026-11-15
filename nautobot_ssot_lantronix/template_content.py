"""Console access on Nautobot device pages.

The Open console custom link sits on a console server port. These put the same link on the
page of the device you want to reach, found through its console cable, and list a console
server's ports in use on its own page. Rendering never contacts Percepxion.
"""

from __future__ import annotations

import ipaddress

from nautobot.apps.ui import TemplateExtension

from .constants import CF_DEVICE_ID, CF_MGMT_IP, PORT_CF
from .coverage.core import port_number
from .coverage.orm import nautobot_ssh_base_port, synced_ssh_port
from .sanitize import host_ip

CF_SSH_PORT = PORT_CF["ssh_port"]
CF_ATTACHED = PORT_CF["attached"]
TEMPLATES = "nautobot_ssot_lantronix/"


def _viewable(user, model, pk) -> bool:
    return model.objects.restrict(user, "view").filter(pk=pk).exists()


def ssh_target(port, base: int) -> str | None:
    """``host:port`` an SSH session to this console server port opens, or None.

    The same rules as the Open console custom link: a synced unit, a numbered port, the
    device's primary IP or else the address Percepxion reports, and the port's own SSH port
    when one is synced. None where SSH to the port is turned off on the unit.
    """
    device = port.device
    number = port_number(port.name)
    cf = device.cf or {}
    if not cf.get(CF_DEVICE_ID) or number is None:
        return None
    raw = device.primary_ip.host if device.primary_ip is not None else cf.get(CF_MGMT_IP)
    clean = host_ip(raw)
    if clean is None:
        return None
    tcp_port = synced_ssh_port((port.cf or {}).get(CF_SSH_PORT))
    if tcp_port is None:
        tcp_port = base + number
    if not 1 <= tcp_port <= 65535:
        return None
    ip = ipaddress.ip_address(clean)
    return f"[{ip}]:{tcp_port}" if ip.version == 6 else f"{ip}:{tcp_port}"


def server_port_target(user, port, base: int) -> str | None:
    """``ssh_target`` for a port, or None unless ``user`` may view the port and its device."""
    if not _viewable(user, type(port), port.pk) or not _viewable(
        user, type(port.device), port.device_id
    ):
        return None
    return ssh_target(port, base)


def device_console_paths(user, device) -> list[dict]:
    """Console access to ``device`` itself: one entry per console port of the device that is
    cabled to a synced console server port the user may view."""
    from nautobot.dcim.models import Cable, ConsolePort, ConsoleServerPort

    base = nautobot_ssh_base_port()
    paths = []
    ports = ConsolePort.objects.restrict(user, "view").filter(device=device)
    for console_port in ports.order_by("name"):
        if not console_port.cable_id or not _viewable(user, Cable, console_port.cable_id):
            continue
        peer = console_port.get_cable_peer()
        if not isinstance(peer, ConsoleServerPort):
            continue
        target = server_port_target(user, peer, base)
        if target:
            paths.append({"console_port": console_port, "server_port": peer, "ssh_target": target})
    return paths


def served_ports(user, device) -> list[dict]:
    """A console server's ports worth listing on its page: those with a cable in Nautobot or
    a device attached according to Percepxion, each with its console link."""
    from nautobot.dcim.models import ConsoleServerPort

    base = nautobot_ssh_base_port()
    rows = []
    for port in ConsoleServerPort.objects.restrict(user, "view").filter(device=device):
        if not port.cable_id and not (port.cf or {}).get(CF_ATTACHED):
            continue
        peer = port.get_cable_peer() if port.cable_id else None
        if peer is not None and not _viewable(user, type(peer.device), peer.device_id):
            peer = None
        rows.append({"port": port, "peer": peer, "ssh_target": ssh_target(port, base)})
    return rows


class DeviceConsole(TemplateExtension):
    """Open console on device pages. ``buttons`` and ``right_page`` are the hooks that work
    across the Nautobot versions the app supports."""

    model = "dcim.device"

    def _user(self):
        return getattr(self.context.get("request"), "user", None)

    def buttons(self):
        user = self._user()
        paths = device_console_paths(user, self.context["object"]) if user else []
        if not paths:
            return ""
        return self.render(
            TEMPLATES + "device_console_buttons.html",
            extra_context={"paths": paths, "several": len(paths) > 1},
        )

    def right_page(self):
        user = self._user()
        device = self.context["object"]
        if not user or not (device.cf or {}).get(CF_DEVICE_ID):
            return ""
        rows = served_ports(user, device)
        if not rows:
            return ""
        return self.render(TEMPLATES + "console_ports_in_use.html", extra_context={"rows": rows})


template_extensions = [DeviceConsole]
