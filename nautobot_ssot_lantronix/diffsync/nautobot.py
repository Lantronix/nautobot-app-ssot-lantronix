"""Target adapter: the Lantronix devices and console ports already in Nautobot.

Loads devices carrying a Percepxion device ID and their `Port-NN` console server ports. Creating
a device runs the identity guard: an unlinked device is adopted only if it's a Lantronix
device, on the Lantronix platform or none, with a matching or empty-and-same-name serial.
Anything else with the same name is reported and left alone.
"""

from __future__ import annotations

import re

from diffsync import Adapter

from ..constants import (
    CF_DEVICE_ID,
    CF_MGMT_IP,
    MANUFACTURER,
    NETWORK_DRIVER,
    PORT_CF,
    slug_to_model,
)
from .models import LantronixConsolePort, LantronixDevice

_PORT = re.compile(r"^Port-(\d{1,3})$")


class NautobotAdapter(Adapter):
    device = LantronixDevice
    console_port = LantronixConsolePort
    top_level = ("device",)

    def __init__(self, *args, job, sync=None, config, **kwargs):
        super().__init__(*args, **kwargs)
        self.job = job
        self.sync = sync
        self.config = config

    # -- load ----------------------------------------------------------------------------------

    def load(self):
        from nautobot.dcim.models import ConsoleServerPort, Device

        devices = Device.objects.filter(device_type__manufacturer__name=MANUFACTURER)
        linked = {}
        for dev in devices:
            pxid = (dev._custom_field_data or {}).get(CF_DEVICE_ID)
            if not pxid:
                continue
            if pxid in linked:
                self.job.logger.warning(
                    f"{dev.name} and {linked[pxid].name} share Percepxion device ID {pxid}; "
                    "using the first"
                )
                continue
            linked[pxid] = dev
            self.add(
                LantronixDevice(
                    percepxion_device_id=pxid,
                    name=dev.name,
                    serial=dev.serial or "",
                    mgmt_ip=(dev._custom_field_data or {}).get(CF_MGMT_IP) or "",
                    pk=dev.pk,
                )
            )
        ports = ConsoleServerPort.objects.filter(device__in=list(linked.values()))
        by_pk = {dev.pk: pxid for pxid, dev in linked.items()}
        for port in ports:
            match = _PORT.match(port.name)
            if not match:
                continue
            pxid = by_pk[port.device_id]
            cf = port._custom_field_data or {}
            values = {attr: cf.get(key) for attr, key in PORT_CF.items()}
            model = LantronixConsolePort(
                percepxion_device_id=pxid,
                number=int(match.group(1)),
                label=port.label or "",
                description=port.description or "",
                attached=bool(values.pop("attached")),
                **{k: (v or "") for k, v in values.items()},
                pk=port.pk,
            )
            self.add(model)
            self.get(LantronixDevice, {"percepxion_device_id": pxid}).add_child(model)

    # -- create support ------------------------------------------------------------------------

    def adopt_or_new(self, pxid, name, serial, source):
        """Return the Nautobot device for a Percepxion device the target didn't load, or None."""
        from nautobot.dcim.models import Device, DeviceType, Location, Platform
        from nautobot.extras.models import Role, Status

        log = self.job.logger
        platform = Platform.objects.filter(network_driver=NETWORK_DRIVER).first()
        existing = Device.objects.filter(name=name).select_related(
            "device_type__manufacturer", "platform"
        )
        if serial:
            existing = existing | Device.objects.filter(
                serial=serial, device_type__manufacturer__name=MANUFACTURER
            )
        existing = list(existing.distinct())
        if len(existing) > 1:
            log.warning(
                f"Not adopting {name}: more than one Nautobot device matches by name or serial"
            )
            return None
        if existing:
            dev = existing[0]
            reason = self._guard(dev, serial, platform)
            if reason:
                log.warning(f"Conflict: Nautobot device {dev.name} left unchanged ({reason})")
                return None
            dev._custom_field_data[CF_DEVICE_ID] = pxid
            dev._custom_field_data[CF_MGMT_IP] = source.mgmt_ip or None
            dev.name = name
            if serial and not dev.serial:
                dev.serial = serial
            if dev.platform is None:
                dev.platform = platform
            dev.validated_save()
            log.info(f"Linked existing device {name} to Percepxion")
            return dev

        model = slug_to_model(source.device_type_slug) if source else ""
        device_type = DeviceType.objects.filter(
            manufacturer__name=MANUFACTURER, model=model
        ).first()
        if device_type is None:
            log.warning(f"Skipped {name}: device type {model} isn't in Nautobot; import it first")
            return None
        location = Location.objects.filter(name=self.config.default_location).first()
        role = Role.objects.filter(name=self.config.default_role).first()
        status = Status.objects.filter(name=self.config.default_status).first()
        missing = [
            label
            for label, obj in (("location", location), ("role", role), ("status", status))
            if obj is None
        ]
        if missing:
            log.error(
                f"Skipped {name}: configured default {', '.join(missing)} not found in Nautobot"
            )
            return None
        dev = Device(
            name=name,
            device_type=device_type,
            location=location,
            role=role,
            status=status,
            serial=serial,
            platform=platform,
        )
        dev._custom_field_data[CF_DEVICE_ID] = pxid
        dev._custom_field_data[CF_MGMT_IP] = source.mgmt_ip or None
        dev.validated_save()
        return dev

    @staticmethod
    def _guard(dev, serial, platform) -> str | None:
        if dev._custom_field_data.get(CF_DEVICE_ID):
            return "already linked to another Percepxion device"
        if dev.device_type.manufacturer.name != MANUFACTURER:
            return f"manufacturer is {dev.device_type.manufacturer.name}"
        if dev.platform is not None and dev.platform != platform:
            return f"platform is {dev.platform.name}"
        if dev.serial and not serial:
            return "Percepxion reports no serial for it, so it can't be matched by name alone"
        if dev.serial and serial and dev.serial != serial:
            return "serial differs"
        return None
