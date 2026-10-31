"""Stock nautobot-ssot ServiceNow adapters plus console links."""

from __future__ import annotations

from diffsync.exceptions import ObjectAlreadyExists
from nautobot.dcim.models import ConsolePort, ConsoleServerPort
from nautobot_ssot.integrations.servicenow.diffsync.adapter_nautobot import NautobotDiffSync
from nautobot_ssot.integrations.servicenow.diffsync.adapter_servicenow import ServiceNowDiffSync

from ..constants import CF_DEVICE_ID
from .links import (
    DEFAULT_PARENT,
    REL_TABLE,
    REL_TYPE,
    REL_TYPE_TABLE,
    ConsoleCable,
    build_links,
    check_parent,
)
from .models import ConsoleLink

TOP_LEVEL = ["company", "location", "console_link"]  # links last, after the devices they join
QUERY_CHUNK = 100


def console_cables(device_names: set[str], logger) -> list[ConsoleCable]:
    """Lantronix console server port to console port cables, both ends in `device_names`."""
    found = []
    ports = ConsoleServerPort.objects.filter(cable__isnull=False).select_related("device", "cable")
    for server_port in ports:
        server = server_port.device
        if not server.cf.get(CF_DEVICE_ID):
            continue  # not a Lantronix console server managed through Percepxion
        cable = server_port.cable
        ends = [cable.termination_a, cable.termination_b]
        far = [
            end
            for end in ends
            if not (isinstance(end, ConsoleServerPort) and end.pk == server_port.pk)
        ]
        if len(far) != 1 or not isinstance(far[0], ConsolePort):
            continue  # cabled to something other than a console port
        attached = far[0].device
        if server.name not in device_names or attached.name not in device_names:
            logger.info(
                f"Skipped console cable {server.name} {server_port.name} -> {attached.name}: "
                "a device on it isn't part of this sync"
            )
            continue
        found.append(ConsoleCable(server.name, server_port.name, attached.name))
    return found


def link_mapping(device_table: str, rel_type_sys_id: str) -> dict:
    return {
        "table": REL_TABLE,
        "table_query": {"type": rel_type_sys_id},
        "mappings": [
            {
                "field": "parent_name",
                "reference": {"key": "parent", "table": device_table, "column": "name"},
            },
            {
                "field": "child_name",
                "reference": {"key": "child", "table": device_table, "column": "name"},
            },
            {
                "field": "relationship_type",
                "reference": {"key": "type", "table": REL_TYPE_TABLE, "column": "name"},
            },
            {"field": "port", "column": "port"},
        ],
    }


class NautobotConsolePathDiffSync(NautobotDiffSync):
    console_link = ConsoleLink
    top_level = TOP_LEVEL

    def __init__(self, *args, link_parent=DEFAULT_PARENT, **kwargs):
        super().__init__(*args, **kwargs)
        self.link_parent = check_parent(link_parent)

    def load(self):
        super().load()
        names = {device.name for device in self.get_all("device")}
        cables = console_cables(names, self.job.logger)
        for record in build_links(cables, self.link_parent, self.job.logger):
            self.add(ConsoleLink(**record))
        self.job.logger.info(
            f"Loaded {len(self.get_all('console_link'))} console links from Nautobot cables."
        )


class ServiceNowConsolePathDiffSync(ServiceNowDiffSync):
    console_link = ConsoleLink
    top_level = TOP_LEVEL

    @property
    def device_table(self) -> str:
        return self.mapping_data["device"]["table"]

    def ci_known(self, name: str) -> bool:
        return any(
            record.get("name") == name
            for record in self.sys_ids.get(self.device_table, {}).values()
        )

    def load(self):
        super().load()
        rel_type = self.relationship_type_record()
        self.register_sn_record(REL_TYPE_TABLE, rel_type)
        self.mapping_data["console_link"] = link_mapping(self.device_table, rel_type["sys_id"])
        self.load_console_links()

    def relationship_type_record(self) -> dict:
        found = self.client.get_all_by_query(REL_TYPE_TABLE, {"name": REL_TYPE})
        if len(found) != 1:
            raise RuntimeError(
                f"Expected one ServiceNow relationship type {REL_TYPE!r}, found {len(found)}"
            )
        return found[0]

    def load_console_links(self):
        """Read only the links that touch a synced CI, with one encoded query per chunk."""
        entry = self.mapping_data["console_link"]
        rel_type = entry["table_query"]["type"]
        mappings = entry["mappings"]
        fields = self.fields_for_mappings(mappings)
        sys_ids = sorted(device.sys_id for device in self.get_all("device") if device.sys_id)
        seen = set()
        for start in range(0, len(sys_ids), QUERY_CHUNK):
            joined = ",".join(sys_ids[start : start + QUERY_CHUNK])
            query = f"type={rel_type}^parentIN{joined}^ORchildIN{joined}"
            for record in self.client.all_table_entries(REL_TABLE, query, fields=fields):
                if record["sys_id"] in seen:
                    continue
                seen.add(record["sys_id"])
                attrs = self.map_record_to_attrs(record, mappings)
                if not attrs.get("parent_name") or not attrs.get("child_name"):
                    continue  # the other end isn't a CI in the synced class
                attrs["port"] = attrs.get("port") or ""
                self.register_sn_record(REL_TABLE, record)
                try:
                    self.add(ConsoleLink(**attrs))
                except ObjectAlreadyExists:
                    self.job.logger.warning(
                        f"Duplicate ServiceNow link {attrs['parent_name']} -> "
                        f"{attrs['child_name']}; kept the first"
                    )
        self.job.logger.info(
            f"Loaded {len(self.get_all('console_link'))} console links from ServiceNow."
        )
