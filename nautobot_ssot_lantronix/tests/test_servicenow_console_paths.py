"""ServiceNow console paths under the Nautobot test runner.

Skipped where nautobot-ssot's ServiceNow extras aren't installed.
"""

import logging
import unittest

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from nautobot.dcim.models import (
    Cable,
    ConsolePort,
    ConsoleServerPort,
    Device,
    DeviceType,
    Location,
    LocationType,
    Manufacturer,
)
from nautobot.extras.models import Role, Status

try:
    from nautobot_ssot.integrations.servicenow.diffsync import models as sn_models

    from ..servicenow.adapters import (
        NautobotConsolePathDiffSync,
        ServiceNowConsolePathDiffSync,
        link_mapping,
    )
    from ..servicenow.jobs import ServiceNowConsolePathTarget
    from ..servicenow.links import REL_TYPE
    from ..servicenow.models import ConsoleLink
except ImportError as exc:  # pragma: no cover - depends on the install
    raise unittest.SkipTest(f"nautobot-ssot ServiceNow extras not installed: {exc}") from None

from ..bootstrap import ensure_objects
from ..constants import CF_DEVICE_ID
from .fakes import cfg

DEVICE_TABLE = "cmdb_ci_netgear"


class FakeJob:
    logger = logging.getLogger("test")
    debug = False


class FakeResult:
    def __init__(self, record):
        self.record = record

    def one(self):
        return self.record


class FakeResource:
    def __init__(self, client, table):
        self.client, self.table = client, table

    def create(self, payload):
        self.client.created.append((self.table, payload))
        return FakeResult({**payload, "sys_id": f"new-{len(self.client.created)}"})


class FakeClient:
    def __init__(self, links=()):
        self.links = list(links)
        self.created = []
        self.queries = []

    def get_all_by_query(self, table, query, limit=100):
        if table == "cmdb_rel_type" and query == {"name": REL_TYPE}:
            return [{"sys_id": "rt-1", "name": REL_TYPE}]
        return []

    def all_table_entries(self, table, query=None, fields=None, limit=10000):
        self.queries.append((table, query))
        return iter(self.links)

    def get_by_sys_id(self, table, sys_id):
        return None

    def resource(self, api_path):
        return FakeResource(self, api_path.rsplit("/", 1)[-1])


def target_adapter(client, devices=(("SLC9016-16E-A", "d-1"), ("switch-01", "d-2"))):
    adapter = ServiceNowConsolePathDiffSync(client=client, job=FakeJob(), sync=None)
    adapter.mapping_data = {"device": {"table": DEVICE_TABLE, "mappings": []}}
    for name, sys_id in devices:
        adapter.register_sn_record(DEVICE_TABLE, {"sys_id": sys_id, "name": name})
        adapter.add(sn_models.Device(name=name, sys_id=sys_id))
    adapter.register_sn_record("cmdb_rel_type", {"sys_id": "rt-1", "name": REL_TYPE})
    adapter.mapping_data["console_link"] = link_mapping(DEVICE_TABLE, "rt-1")
    return adapter


class NautobotSideTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        ensure_objects(cfg(), logging.getLogger("test"))
        device_ct = ContentType.objects.get_for_model(Device)
        lt = LocationType.objects.create(name="Site")
        lt.content_types.add(device_ct)
        active = Status.objects.get(name="Active")
        active.content_types.add(device_ct)
        location = Location.objects.create(name="Lab", location_type=lt, status=active)
        switch_role = Role.objects.create(name="Network Switch")
        switch_role.content_types.add(device_ct)

        def make(name, manufacturer, model, role, pxid=None):
            mfr, _ = Manufacturer.objects.get_or_create(name=manufacturer)
            dt, _ = DeviceType.objects.get_or_create(manufacturer=mfr, model=model)
            dev = Device(name=name, device_type=dt, role=role, status=active, location=location)
            if pxid:
                dev._custom_field_data[CF_DEVICE_ID] = pxid
            dev.validated_save()
            return dev

        console = Role.objects.get(name="Console Server")
        slc = make("SLC9016-16E-A", "Lantronix", "SLC9016-16E", console, pxid="00204AFIXTURE0001")
        other = make("other-console", "Acme", "CS-1", console)  # not Lantronix: no Percepxion ID
        switch = make("switch-01", "Cisco", "WS-C3560G-24PS", switch_role)
        cable_status = Status.objects.get(name="Connected")
        cable_status.content_types.add(ContentType.objects.get_for_model(Cable))

        def cable(server, port_name, attached, cp_name):
            csp = ConsoleServerPort.objects.create(device=server, name=port_name)
            cp = ConsolePort.objects.create(device=attached, name=cp_name)
            Cable(termination_a=csp, termination_b=cp, status=cable_status).validated_save()

        cable(slc, "Port-14", switch, "Console")
        cable(other, "Port-01", switch, "Console 2")

    def test_links_come_from_lantronix_console_cables_only(self):
        adapter = NautobotConsolePathDiffSync(job=FakeJob(), sync=None)
        adapter.load()
        links = [link.get_unique_id() for link in adapter.get_all("console_link")]
        self.assertEqual(links, [f"switch-01__SLC9016-16E-A__{REL_TYPE}"])
        self.assertEqual(adapter.get_all("console_link")[0].port, "14")

    def test_direction_setting_flips_the_link(self):
        adapter = NautobotConsolePathDiffSync(
            job=FakeJob(), sync=None, link_parent="console_server"
        )
        adapter.load()
        [link] = adapter.get_all("console_link")
        self.assertEqual((link.parent_name, link.child_name), ("SLC9016-16E-A", "switch-01"))

    def test_loaded_link_matches_what_servicenow_returns(self):
        source = NautobotConsolePathDiffSync(job=FakeJob(), sync=None)
        source.load()
        record = {"sys_id": "r-1", "parent": "d-2", "child": "d-1", "type": "rt-1", "port": "14"}
        target = target_adapter(FakeClient([record]))
        target.load_console_links()
        [mine], [theirs] = source.get_all("console_link"), target.get_all("console_link")
        self.assertEqual(mine.get_unique_id(), theirs.get_unique_id())
        self.assertEqual(mine.get_attrs(), theirs.get_attrs())  # a re-run is a no-op


class ServiceNowSideTest(TestCase):
    def test_load_reads_only_links_that_touch_synced_cis(self):
        records = [
            {"sys_id": "r-1", "parent": "d-1", "child": "d-2", "type": "rt-1", "port": "14"},
            {"sys_id": "r-2", "parent": "d-1", "child": "unknown", "type": "rt-1", "port": ""},
        ]
        client = FakeClient(records)
        adapter = target_adapter(client)
        adapter.load_console_links()
        [(table, query)] = client.queries
        self.assertEqual(table, "cmdb_rel_ci")
        self.assertEqual(query, "type=rt-1^parentINd-1,d-2^ORchildINd-1,d-2")
        [link] = adapter.get_all("console_link")
        self.assertEqual(
            (link.parent_name, link.child_name, link.port),
            ("SLC9016-16E-A", "switch-01", "14"),
        )

    def test_create_writes_one_relationship_with_resolved_sys_ids(self):
        client = FakeClient()
        adapter = target_adapter(client)
        ids = {
            "parent_name": "SLC9016-16E-A",
            "child_name": "switch-01",
            "relationship_type": REL_TYPE,
        }
        model = ConsoleLink.create(adapter, ids, {"port": "14"})
        self.assertIsNotNone(model)
        self.assertEqual(
            client.created,
            [("cmdb_rel_ci", {"parent": "d-1", "child": "d-2", "type": "rt-1", "port": "14"})],
        )

    def test_create_skips_when_a_ci_is_missing(self):
        client = FakeClient()
        adapter = target_adapter(client, devices=(("SLC9016-16E-A", "d-1"),))
        ids = {
            "parent_name": "SLC9016-16E-A",
            "child_name": "switch-01",
            "relationship_type": REL_TYPE,
        }
        with self.assertLogs("test", level="WARNING") as logs:
            self.assertIsNone(ConsoleLink.create(adapter, ids, {"port": "14"}))
        self.assertEqual(client.created, [])
        self.assertIn("no ServiceNow CI for switch-01", logs.output[0])

    def test_delete_never_deletes(self):
        adapter = target_adapter(FakeClient())
        link = ConsoleLink(parent_name="a", child_name="b", relationship_type=REL_TYPE, port="1")
        adapter.add(link)
        with self.assertLogs("test", level="WARNING"):
            self.assertIsNone(link.delete())
        self.assertEqual(len(adapter.get_all("console_link")), 1)

    def test_job_has_no_delete_option(self):
        self.assertNotIn("delete_records", ServiceNowConsolePathTarget._get_vars())
        self.assertIn("dryrun", ServiceNowConsolePathTarget._get_vars())
