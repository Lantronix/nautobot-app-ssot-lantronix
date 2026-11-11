"""Create the Nautobot objects the sync relies on, reusing any that exist.

Runs on real syncs only, so a dry run writes nothing.
"""

from __future__ import annotations

from .constants import (
    CF_DEVICE_ID,
    CF_FIRMWARE,
    CF_MGMT_IP,
    MANUFACTURER,
    NETWORK_DRIVER,
    PLATFORM_NAME,
    PORT_CF_LABELS,
)

LINK_NAME = "Lantronix: Open console"
_OCTET = r"(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])"
IPV4_RE = rf"^({_OCTET}\.){{3}}{_OCTET}$"


def ensure_objects(config, logger) -> None:
    from django.contrib.contenttypes.models import ContentType
    from nautobot.dcim.models import ConsoleServerPort, Device, Manufacturer, Platform
    from nautobot.extras.choices import CustomFieldTypeChoices
    from nautobot.extras.models import CustomField, CustomLink, Role

    manufacturer, _ = Manufacturer.objects.get_or_create(name=MANUFACTURER)
    if not Platform.objects.filter(network_driver=NETWORK_DRIVER).exists():
        Platform.objects.get_or_create(
            name=PLATFORM_NAME,
            defaults={"manufacturer": manufacturer, "network_driver": NETWORK_DRIVER},
        )
        logger.info(f"Created platform {PLATFORM_NAME}")

    device_ct = ContentType.objects.get_for_model(Device)
    port_ct = ContentType.objects.get_for_model(ConsoleServerPort)
    role, created = Role.objects.get_or_create(name=config.default_role)
    role.content_types.add(device_ct)
    if created:
        logger.info(f"Created role {config.default_role}")

    fields = [
        (CF_DEVICE_ID, "Percepxion device ID", CustomFieldTypeChoices.TYPE_TEXT, device_ct),
        (CF_MGMT_IP, "Percepxion management IP", CustomFieldTypeChoices.TYPE_TEXT, device_ct),
        (CF_FIRMWARE, "Firmware version", CustomFieldTypeChoices.TYPE_TEXT, device_ct),
    ]
    for key, label in PORT_CF_LABELS.items():
        cf_type = (
            CustomFieldTypeChoices.TYPE_BOOLEAN
            if key == "lx_attached"
            else CustomFieldTypeChoices.TYPE_TEXT
        )
        fields.append((key, label, cf_type, port_ct))
    for key, label, cf_type, ct in fields:
        cf, created = CustomField.objects.get_or_create(
            key=key, defaults={"label": label, "type": cf_type, "grouping": "Lantronix"}
        )
        cf.content_types.add(ct)
        if key == CF_MGMT_IP and cf.validation_regex != IPV4_RE:
            # The value goes into the Open console URL: only a dotted IPv4 address is allowed.
            cf.validation_regex = IPV4_RE
            cf.save()
        if created:
            logger.info(f"Created custom field {key}")

    # SSH to the SLC's console port from the port page. The text renders empty (so Nautobot
    # hides the button) unless the device is synced, the port is one of the unit's numbered
    # Port-NN ports and there is an address: the primary IP, else the Percepxion-reported one.
    text, target_url = console_link(config.ssh_base_port)
    link, created = CustomLink.objects.get_or_create(
        name=LINK_NAME,
        defaults={
            "content_type": port_ct,
            "text": text,
            "target_url": target_url,
            "button_class": "primary",
            "new_window": False,
            "weight": 100,
        },
    )
    # The app owns what the button opens. Weight, group and style are the admin's to change.
    if not created and (link.text, link.target_url, link.content_type) != (
        text,
        target_url,
        port_ct,
    ):
        link.text, link.target_url, link.content_type = text, target_url, port_ct
        link.validated_save()


def console_link(ssh_base_port: int) -> tuple[str, str]:
    """Jinja text and URL of the Open console custom link."""
    numbered = "obj.name[:5] == 'Port-' and (obj.name[5:] | int(-1)) > 0"
    address = "obj.device.primary_ip or obj.device.cf.lx_mgmt_ip"
    text = (
        "{% if obj.device.cf.percepxion_device_id and "
        f"{numbered} and ({address}) %}}Open console{{% endif %}}"
    )
    target_url = (
        "{% set host = (obj.device.primary_ip.host if obj.device.primary_ip "
        "else obj.device.cf.lx_mgmt_ip) | string %}"
        "ssh://{{ '[' ~ host ~ ']' if ':' in host else host }}:"
        f"{{{{ {int(ssh_base_port)} + (obj.name[5:] | int) }}}}"
    )
    return text, target_url
