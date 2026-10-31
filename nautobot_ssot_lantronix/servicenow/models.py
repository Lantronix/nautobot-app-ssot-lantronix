"""ConsoleLink: one `cmdb_rel_ci` row per console cable. Needs nautobot-ssot's ServiceNow code."""

from __future__ import annotations

import uuid

from diffsync import DiffSyncModel
from nautobot_ssot.integrations.servicenow.diffsync.models import ServiceNowCRUDMixin


class ConsoleLink(ServiceNowCRUDMixin, DiffSyncModel):
    """`Connects to::Connected by` between a Lantronix console server CI and an attached CI."""

    _modelname = "console_link"
    _identifiers = ("parent_name", "child_name", "relationship_type")
    _attributes = ("port",)

    parent_name: str
    child_name: str
    relationship_type: str
    port: str = ""

    sys_id: str | None = None
    pk: uuid.UUID | None = None

    @classmethod
    def create(cls, adapter, ids, attrs):
        # Both CIs must already be in ServiceNow (loaded, or created earlier in this run).
        missing = [
            name for name in (ids["parent_name"], ids["child_name"]) if not adapter.ci_known(name)
        ]
        if missing:
            adapter.job.logger.warning(
                f"Console link {ids['parent_name']} -> {ids['child_name']} not written: "
                f"no ServiceNow CI for {', '.join(missing)}"
            )
            return None
        return super().create(adapter, ids=ids, attrs=attrs)

    def delete(self):
        # Never deletes. The job's flags skip target-only objects, so this only runs if
        # someone changes them.
        self.adapter.job.logger.warning(
            f"Not deleting console link {self.parent_name} -> {self.child_name}: "
            "links are never deleted"
        )
        return None
