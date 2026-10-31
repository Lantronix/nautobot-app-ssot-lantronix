"""Names shared by the adapters and bootstrap. No Nautobot imports."""

MANUFACTURER = "Lantronix"
PLATFORM_NAME = "Lantronix ITM OS"
NETWORK_DRIVER = "lantronix_itmgos"  # matches the Netmiko driver
CF_DEVICE_ID = "percepxion_device_id"
CF_MGMT_IP = "lx_mgmt_ip"  # management IP Percepxion reports; never written to IPAM

# diffsync attribute -> console server port custom field key
PORT_CF = {
    "state": "lx_port_state",
    "attached": "lx_attached",
    "attached_type": "lx_attached_type",
    "attached_model": "lx_attached_model",
    "attached_os_version": "lx_attached_os_version",
    "attached_serial": "lx_attached_serial",
    "attached_hostname": "lx_attached_hostname",
    "attached_ip": "lx_attached_ip",
    "attached_last_seen": "lx_attached_last_seen",
}

PORT_CF_LABELS = {
    "lx_port_state": "Port state",
    "lx_attached": "Device attached",
    "lx_attached_type": "Attached type",
    "lx_attached_model": "Attached model",
    "lx_attached_os_version": "Attached OS version",
    "lx_attached_serial": "Attached serial",
    "lx_attached_hostname": "Attached hostname",
    "lx_attached_ip": "Attached management IP",
    "lx_attached_last_seen": "Attached last seen",
}

LABEL_MAX = 64
DESCRIPTION_MAX = 200
FACT_MAX = 100


def port_name(number: int) -> str:
    """Console server port name in the Lantronix device types: Port-01, Port-17, Port-48."""
    return f"Port-{number:02d}"


def slug_to_model(slug: str) -> str:
    """Device-type library slug to the Nautobot device type model: lantronix-slc9016 -> SLC9016."""
    return slug.removeprefix("lantronix-").upper()
