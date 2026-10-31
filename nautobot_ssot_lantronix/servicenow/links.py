"""Console cables to ServiceNow CI relationships. No Nautobot or ServiceNow imports."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

REL_TYPE = "Connects to::Connected by"
REL_TABLE = "cmdb_rel_ci"
REL_TYPE_TABLE = "cmdb_rel_type"
PARENT_CHOICES = ("console_server", "attached_device")
# The attached device depends on the console server for its management path, so it is the
# parent: ServiceNow impact flows from child to parent (decided 2026-10-30).
DEFAULT_PARENT = "attached_device"

_PORT_NUMBER = re.compile(r"(\d{1,4})$")


@dataclass(frozen=True)
class ConsoleCable:
    """One cable from a Lantronix console server port to another device's console port."""

    console_server: str
    port_name: str
    attached_device: str


def port_number(port_name: str) -> int | None:
    """Port-14 -> 14. `cmdb_rel_ci.port` is an integer column, so only the number is stored."""
    match = _PORT_NUMBER.search(port_name or "")
    return int(match.group(1)) if match else None


def check_parent(parent: str) -> str:
    if parent not in PARENT_CHOICES:
        raise ValueError(
            f"servicenow_console_link_parent must be one of {', '.join(PARENT_CHOICES)}"
        )
    return parent


def build_links(
    cables: Iterable[ConsoleCable], parent: str = DEFAULT_PARENT, logger=None
) -> list[dict]:
    """Relationship records keyed by (parent, child, type), one per server and attached-device pair.

    A device with two console ports on the same server gets one relationship carrying the lowest
    port number; the others are logged. Self-links are skipped.
    """
    check_parent(parent)
    ports: dict[tuple[str, str], list[str]] = {}
    for cable in cables:
        if cable.console_server == cable.attached_device:
            if logger:
                logger.warning(f"Skipped {cable.console_server} {cable.port_name}: self-cabled")
            continue
        ports.setdefault((cable.console_server, cable.attached_device), []).append(cable.port_name)

    def order(name: str) -> tuple[int, str]:
        number = port_number(name)
        return (number if number is not None else 10**6, name)

    links = []
    for (server, attached), names in sorted(ports.items()):
        names = sorted(names, key=order)
        number = port_number(names[0])
        if len(names) > 1 and logger:
            logger.info(
                f"{attached} has {len(names)} console cables to {server}; {names[0]} is written, "
                f"not written: {', '.join(names[1:])}"
            )
        if parent == "console_server":
            parent_name, child_name = server, attached
        else:
            parent_name, child_name = attached, server
        links.append(
            {
                "parent_name": parent_name,
                "child_name": child_name,
                "relationship_type": REL_TYPE,
                "port": "" if number is None else str(number),
            }
        )
    return links


def link_id(link: dict) -> str:
    return "__".join((link["parent_name"], link["child_name"], link["relationship_type"]))


def stale_links(source_ids: Iterable[str], target_ids: Iterable[str]) -> list[str]:
    """Links in ServiceNow with no Nautobot cable behind them. Logged, never deleted."""
    return sorted(set(target_ids) - set(source_ids))
