"""diffsync models. create/update write to Nautobot; the Percepxion adapter only loads them.

Rules carried from the NetBox plugin: a device's type and location are set once at create and
never changed, nothing is deleted, cables are never touched, and console server ports come from
the device type (the app updates them, never creates them).
"""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from diffsync import DiffSyncModel
from django.core.exceptions import ValidationError

from ..constants import CF_DEVICE_ID, CF_MGMT_IP, PORT_CF, port_name

ATTACHED_ATTRS = (
    "state",
    "attached",
    "attached_type",
    "attached_model",
    "attached_os_version",
    "attached_serial",
    "attached_hostname",
    "attached_ip",
)


class LantronixDevice(DiffSyncModel):
    _modelname: ClassVar[str] = "device"
    _identifiers: ClassVar[tuple] = ("percepxion_device_id",)
    _attributes: ClassVar[tuple] = ("name", "serial", "mgmt_ip")
    _children: ClassVar[dict] = {"console_port": "console_ports"}

    percepxion_device_id: str
    name: str
    serial: str = ""
    mgmt_ip: str = ""
    # Create-only: not diffed, read from the source object at create time.
    device_type_slug: str = ""
    console_ports: list = []
    pk: UUID | None = None

    @classmethod
    def create(cls, adapter, ids, attrs):
        log = adapter.job.logger
        pxid = ids["percepxion_device_id"]
        source = adapter.job.source_adapter.get(cls, ids)
        try:
            device = adapter.adopt_or_new(pxid, attrs["name"], attrs.get("serial", ""), source)
        except ValidationError as exc:
            log.warning(f"Device {attrs['name']}: not saved ({'; '.join(exc.messages)})")
            return None
        if device is None:
            return None
        model = super().create(adapter=adapter, ids=ids, attrs=attrs)
        model.pk = device.pk
        return model

    def update(self, attrs):
        from nautobot.dcim.models import Device

        device = Device.objects.get(pk=self.pk)
        if "name" in attrs:
            device.name = attrs["name"]
        if attrs.get("serial"):  # never blank a serial Nautobot already has
            device.serial = attrs["serial"]
        if "mgmt_ip" in attrs:
            device._custom_field_data[CF_MGMT_IP] = attrs["mgmt_ip"] or None
        try:
            device.validated_save()
        except ValidationError as exc:
            self.adapter.job.logger.warning(
                f"Device {device.name}: update not saved ({'; '.join(exc.messages)})"
            )
            return None
        return super().update(attrs)

    def delete(self):
        # Never deletes. The job's diff flags skip target-only objects, so this only
        # runs if someone changes the flags.
        self.adapter.job.logger.warning(f"Not deleting {self.name}: the app never deletes devices")
        return None


class LantronixConsolePort(DiffSyncModel):
    _modelname: ClassVar[str] = "console_port"
    _identifiers: ClassVar[tuple] = ("percepxion_device_id", "number")
    _attributes: ClassVar[tuple] = ("label", "description", *ATTACHED_ATTRS)

    percepxion_device_id: str
    number: int
    label: str = ""
    description: str = ""
    state: str = ""
    attached: bool = False
    attached_type: str = ""
    attached_model: str = ""
    attached_os_version: str = ""
    attached_serial: str = ""
    attached_hostname: str = ""
    attached_ip: str = ""
    # Not diffed: it moves on every heartbeat. Written whenever the port is written, from the
    # source record, so it dates the attached-device facts without a change on every run.
    attached_last_seen: str = ""
    pk: UUID | None = None

    @classmethod
    def create(cls, adapter, ids, attrs):
        # "Create" means the port exists on the device (from its device type) but wasn't loaded:
        # the device is new this run. Find it and write the attributes.
        from nautobot.dcim.models import ConsoleServerPort, Device

        name = port_name(ids["number"])
        device = Device.objects.filter(
            **{f"_custom_field_data__{CF_DEVICE_ID}": ids["percepxion_device_id"]}
        ).first()
        port = (
            ConsoleServerPort.objects.filter(device=device, name=name).first() if device else None
        )
        if port is None:
            return None  # the device type has no such port, or the device wasn't created
        source = adapter.job.source_adapter.get(cls, ids)
        _write_port(port, {**attrs, "attached_last_seen": source.attached_last_seen})
        model = super().create(adapter=adapter, ids=ids, attrs=attrs)
        model.pk = port.pk
        return model

    def update(self, attrs):
        from nautobot.dcim.models import ConsoleServerPort

        source = self.adapter.job.source_adapter.get(LantronixConsolePort, self.get_unique_id())
        values = {**attrs, "attached_last_seen": source.attached_last_seen}
        _write_port(ConsoleServerPort.objects.get(pk=self.pk), values)
        return super().update(attrs)

    def delete(self):
        return None


def _write_port(port, attrs: dict) -> None:
    if "label" in attrs:
        port.label = attrs["label"]
    if "description" in attrs:
        port.description = attrs["description"]
    for attr, key in PORT_CF.items():
        if attr in attrs:
            value = attrs[attr]
            port._custom_field_data[key] = value if value not in ("",) else None
    port.validated_save()
