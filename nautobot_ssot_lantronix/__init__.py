"""Nautobot SSoT app: Lantronix console servers from Percepxion."""

from importlib import metadata

from nautobot.apps import NautobotAppConfig

try:
    __version__ = metadata.version("nautobot-app-ssot-lantronix")
except metadata.PackageNotFoundError:  # source checkout on PYTHONPATH (dev stack)
    __version__ = "0.1.0"


class LantronixSSoTConfig(NautobotAppConfig):
    name = "nautobot_ssot_lantronix"
    verbose_name = "Lantronix SSoT"
    description = (
        "Sync Lantronix console servers, console ports and attached devices from Percepxion."
    )
    version = __version__
    author = "Lantronix"
    base_url = "ssot-lantronix"
    required_settings = []
    min_version = "3.1.0"
    max_version = "3.99"
    default_settings = {
        "default_location": "",
        "default_role": "Console Server",
        "default_status": "Active",
        "ssh_base_port": 3000,
        "model_rules": {},
        # Optional ServiceNow console paths: "attached_device" or "console_server".
        "servicenow_console_link_parent": "attached_device",
    }


config = LantronixSSoTConfig
