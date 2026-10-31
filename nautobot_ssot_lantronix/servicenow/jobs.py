"""Nautobot to ServiceNow, stock models plus console paths. Registered by ../jobs.py."""

from __future__ import annotations

from django.conf import settings
from django.templatetags.static import static
from django.urls import reverse
from nautobot_ssot.integrations.servicenow.jobs import ServiceNowDataTarget
from nautobot_ssot.integrations.servicenow.servicenow import ServiceNowClient
from nautobot_ssot.integrations.servicenow.utils import get_servicenow_parameters
from nautobot_ssot.jobs.base import DataMapping

from .adapters import NautobotConsolePathDiffSync, ServiceNowConsolePathDiffSync
from .links import DEFAULT_PARENT, REL_TYPE, stale_links

name = "Lantronix SSoT"  # job grouping in the Nautobot UI


class ServiceNowConsolePathTarget(ServiceNowDataTarget):
    """Stock Nautobot to ServiceNow sync plus one CI relationship per Lantronix console cable."""

    delete_records = (
        None  # removes the stock "delete records" option; this job never deletes
    )

    class Meta:
        name = "Nautobot ⟹ ServiceNow (with console paths)"
        data_target = "ServiceNow"
        data_target_icon = static("nautobot_ssot_servicenow/ServiceNow_logo.svg")
        description = (
            "Stock Nautobot to ServiceNow sync plus a 'Connects to::Connected by' relationship "
            "for each "
            "Lantronix console cable. Never deletes in ServiceNow."
        )
        has_sensitive_variables = False

    @classmethod
    def data_mappings(cls):
        return (
            *super().data_mappings(),
            DataMapping(
                "Console cable", reverse("dcim:cable_list"), f"CI relationship ({REL_TYPE})", None
            ),
        )

    @property
    def link_parent(self) -> str:
        raw = settings.PLUGINS_CONFIG.get("nautobot_ssot_lantronix", {})
        return raw.get("servicenow_console_link_parent") or DEFAULT_PARENT

    def load_source_adapter(self):
        self.logger.info("Loading current data from Nautobot...")
        self.source_adapter = NautobotConsolePathDiffSync(
            job=self, sync=self.sync, site_filter=self.site_filter, link_parent=self.link_parent
        )
        self.source_adapter.load()

    def load_target_adapter(self):
        configs = get_servicenow_parameters()
        client = ServiceNowClient(
            instance=configs.get("instance"),
            username=configs.get("username"),
            password=configs.get("password"),
            worker=self,
        )
        self.logger.info("Loading current data from ServiceNow...")
        self.target_adapter = ServiceNowConsolePathDiffSync(
            client=client, job=self, sync=self.sync, site_filter=self.site_filter
        )
        self.target_adapter.load()

    def calculate_diff(self):
        source = [link.get_unique_id() for link in self.source_adapter.get_all("console_link")]
        target = [link.get_unique_id() for link in self.target_adapter.get_all("console_link")]
        for uid in stale_links(source, target):
            parent, child, _ = uid.split("__", 2)
            self.logger.warning(
                f"Stale console link in ServiceNow, no Nautobot cable behind it: "
                f"{parent} -> {child}. "
                "Left in place; retire it in ServiceNow if the cable is gone."
            )
        super().calculate_diff()

    def run(self, *args, **kwargs):
        kwargs["delete_records"] = False
        super().run(*args, **kwargs)


jobs = [ServiceNowConsolePathTarget]
