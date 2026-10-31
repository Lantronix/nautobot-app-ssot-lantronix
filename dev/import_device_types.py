"""Import devicetype-library YAML files into Nautobot (dev and demo use).

Usage inside the stack, with DEVICE_TYPE_DIR set to a directory of Lantronix YAML files:

    nautobot-server nbshell --command "exec(open('/source/dev/import_device_types.py').read())"

Existing types are left as they are.
"""

import os
from pathlib import Path

import yaml
from nautobot.dcim.models import (
    ConsolePortTemplate,
    ConsoleServerPortTemplate,
    DeviceType,
    InterfaceTemplate,
    Manufacturer,
    PowerPortTemplate,
)

COMPONENTS = {
    "console-ports": ConsolePortTemplate,
    "console-server-ports": ConsoleServerPortTemplate,
    "interfaces": InterfaceTemplate,
    "power-ports": PowerPortTemplate,
}
KEEP = {"name", "type", "label", "description", "mgmt_only", "maximum_draw", "allocated_draw"}

created = skipped = 0
for path in sorted(Path(os.environ["DEVICE_TYPE_DIR"]).glob("*.yaml")):
    data = yaml.safe_load(path.read_text())
    manufacturer, _ = Manufacturer.objects.get_or_create(name=data["manufacturer"])
    if DeviceType.objects.filter(manufacturer=manufacturer, model=data["model"]).exists():
        skipped += 1
        continue
    dt = DeviceType(
        manufacturer=manufacturer,
        model=data["model"],
        part_number=data.get("part_number", ""),
        u_height=data.get("u_height", 1),
        is_full_depth=data.get("is_full_depth", True),
        comments=data.get("comments", ""),
    )
    dt.validated_save()
    for key, model in COMPONENTS.items():
        for item in data.get(key) or []:
            model.objects.create(device_type=dt, **{k: v for k, v in item.items() if k in KEEP})
    created += 1
print(f"device types created={created} skipped={skipped}")
