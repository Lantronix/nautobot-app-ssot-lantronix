"""Read console paths from Nautobot for the coverage gate (ORM, inside Nautobot).

Every query is restricted to what `user` may view: devices, console ports, cables, the SLC's
port and device, and its primary IP. A cable the user can't view, or whose far end they can't
view, is reported as peer_not_visible with no SLC details. An SLC primary IP the user can't view
is not used. user=None means unrestricted (shell use only).
"""

from ..constants import CF_MGMT_IP, PORT_CF
from .core import DEFAULT_SSH_BASE_PORT, ConsoleLink, Slc, Target

APP = "nautobot_ssot_lantronix"
CF_PORT_STATE = PORT_CF["state"]


def _restrict(qs, user):
    return qs.restrict(user, "view") if user is not None else qs


def _visible_ids(model, ids, user) -> set:
    if not ids:
        return set()
    return set(_restrict(model.objects.filter(pk__in=ids), user).values_list("pk", flat=True))


def _app_settings() -> dict:
    from django.conf import settings

    return (getattr(settings, "PLUGINS_CONFIG", {}) or {}).get(APP, {}) or {}


def nautobot_ssh_base_port() -> int:
    try:
        return int(_app_settings().get("ssh_base_port", DEFAULT_SSH_BASE_PORT))
    except (TypeError, ValueError):
        return DEFAULT_SSH_BASE_PORT


def nautobot_console_server_roles() -> list[str]:
    """The role the SSoT app gives synced SLCs: the default exclusion for the gate."""
    return [str(_app_settings().get("default_role") or "Console Server")]


def _primary_ip_pk(device):
    ip = device.primary_ip
    return ip.pk if ip is not None else None


def nautobot_slc(device, cache: dict, base: int, ip_visible: bool = True) -> Slc:
    if device.pk in cache:
        return cache[device.pk]
    cf = device.cf or {}
    host, source = None, None
    if device.primary_ip is not None and ip_visible:
        host, source = device.primary_ip.host, "primary_ip"
    elif cf.get(CF_MGMT_IP):
        host, source = cf[CF_MGMT_IP], "percepxion_mgmt_ip"
    slc = Slc(
        id=str(device.pk),
        name=device.name,
        synced=bool(cf.get("percepxion_device_id")),
        host=host,
        host_source=source,
        ssh_base_port=base,
        manufacturer=device.device_type.manufacturer.name.lower(),
        percepxion_id=cf.get("percepxion_device_id") or None,
    )
    cache[device.pk] = slc
    return slc


def nautobot_targets(user=None):
    from nautobot.dcim.models import Cable, ConsolePort, ConsoleServerPort, Device
    from nautobot.ipam.models import IPAddress

    base = nautobot_ssh_base_port()
    qs = _restrict(Device.objects.all(), user)
    qs = qs.select_related("location", "role", "platform", "status").prefetch_related("tags")
    ports: dict = {}
    for cp in _restrict(ConsolePort.objects.filter(device__in=qs), user).select_related("cable"):
        ports.setdefault(cp.device_id, []).append(cp)

    cable_ids = [cp.cable_id for cps in ports.values() for cp in cps if cp.cable_id]
    ok_cables = _visible_ids(Cable, cable_ids, user)
    peers = {
        cp.pk: (cp.get_cable_peer() if cp.cable_id in ok_cables else None)
        for cps in ports.values()
        for cp in cps
    }
    csps = [p for p in peers.values() if isinstance(p, ConsoleServerPort)]
    ok_ports = _visible_ids(ConsoleServerPort, [p.pk for p in csps], user)
    ok_devices = _visible_ids(Device, list({p.device_id for p in csps}), user)
    slc_ips = [_primary_ip_pk(p.device) for p in csps if p.device_id in ok_devices]
    ok_ips = _visible_ids(IPAddress, [i for i in slc_ips if i], user)

    cache: dict = {}
    out = []
    for d in qs:
        result = []
        for cp in sorted(ports.get(d.pk, []), key=lambda p: p.name):
            link = ConsoleLink(console_port=cp.name, cabled=bool(cp.cable_id))
            peer = peers.get(cp.pk)
            if cp.cable_id and cp.cable_id not in ok_cables:
                link.peer_visible = False
            elif isinstance(peer, ConsoleServerPort):
                if peer.pk in ok_ports and peer.device_id in ok_devices:
                    link.peer_is_console_server_port = True
                    ip_ok = _primary_ip_pk(peer.device) in ok_ips
                    link.slc = nautobot_slc(peer.device, cache, base, ip_ok)
                    link.slc_port = peer.name
                    link.port_state = (peer.cf or {}).get(CF_PORT_STATE) or None
                else:
                    link.peer_visible = False
            result.append(link)
        out.append(
            Target(
                id=str(d.pk),
                name=d.name or str(d.pk),
                active=bool(d.status and d.status.name == "Active"),
                site=d.location.name if d.location_id else None,
                role=d.role.name if d.role_id else None,
                platform=(d.platform.network_driver or None) if d.platform_id else None,
                tags=[t.name for t in d.tags.all()],
                links=result,
            )
        )
    return out
