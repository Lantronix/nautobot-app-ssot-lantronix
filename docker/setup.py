"""Prepare Nautobot for the Lantronix SSoT job. Safe to run again; existing objects aren't changed.

Imports the device types in docker/device-types and creates the location new console servers
go in. Run from the docker directory:
    docker compose exec nautobot nautobot-server nbshell \
      --command "exec(open('/opt/nautobot-lantronix/setup.py').read())"

Optional environment variable, with its default in brackets:
    LANTRONIX_DEFAULT_LOCATION [Console Servers]
"""

import os
from pathlib import Path

import yaml
from django.contrib.contenttypes.models import ContentType
from nautobot.dcim.models import (
    ConsolePortTemplate,
    ConsoleServerPortTemplate,
    Device,
    DeviceType,
    InterfaceTemplate,
    Location,
    LocationType,
    Manufacturer,
    PowerPortTemplate,
)
from nautobot.extras.models import Status

COMPONENTS = {
    "console-ports": ConsolePortTemplate,
    "console-server-ports": ConsoleServerPortTemplate,
    "interfaces": InterfaceTemplate,
    "power-ports": PowerPortTemplate,
}
KEEP = {"name", "type", "label", "description", "mgmt_only", "maximum_draw", "allocated_draw"}

created = 0
for path in sorted(Path("/opt/nautobot-lantronix/device-types").glob("*.yaml")):
    data = yaml.safe_load(path.read_text())
    manufacturer, _ = Manufacturer.objects.get_or_create(name=data["manufacturer"])
    if DeviceType.objects.filter(manufacturer=manufacturer, model=data["model"]).exists():
        continue
    device_type = DeviceType(
        manufacturer=manufacturer,
        model=data["model"],
        part_number=data.get("part_number", ""),
        u_height=data.get("u_height", 1),
        is_full_depth=data.get("is_full_depth", True),
        comments=data.get("comments", ""),
    )
    device_type.validated_save()
    for key, model in COMPONENTS.items():
        for item in data.get(key) or []:
            fields = {k: v for k, v in item.items() if k in KEEP}
            model.objects.create(device_type=device_type, **fields)
    created += 1

location_name = os.environ.get("LANTRONIX_DEFAULT_LOCATION", "Console Servers")
location_type, _ = LocationType.objects.get_or_create(name="Site")
location_type.content_types.add(ContentType.objects.get_for_model(Device))
location, _ = Location.objects.get_or_create(
    name=location_name,
    defaults={"location_type": location_type, "status": Status.objects.get(name="Active")},
)

print(f"Setup complete: location={location.name} device types added={created}")
