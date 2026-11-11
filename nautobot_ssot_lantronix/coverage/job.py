"""Console coverage gate job. Read only.

Registered by the app (jobs.py); enable it under Jobs after install. The lantronix.oob Ansible
role oob_coverage_gate finds it by its name, "AOOB console coverage gate".
"""

import json

from nautobot.apps.jobs import BooleanVar, Job, MultiObjectVar, ObjectVar, StringVar
from nautobot.dcim.models import Location
from nautobot.extras.models import Role, Tag

from .core import DEFAULT_READY_STATES, Scope, coverage_report, split_list, summary_lines
from .orm import nautobot_console_server_roles, nautobot_targets


class ConsoleCoverageGate(Job):
    location = ObjectVar(model=Location, required=False)
    roles = MultiObjectVar(
        model=Role, required=False, query_params={"content_types": "dcim.device"}
    )
    tags = MultiObjectVar(model=Tag, required=False)
    devices = StringVar(
        required=False,
        label="Device names",
        description="Comma separated. Empty means every active device in scope",
    )
    ready_states = StringVar(
        required=False,
        default="Managed, Identified",
        description="Port states that count as a ready console path",
    )
    include_console_servers = BooleanVar(
        required=False,
        default=False,
        description="Also grade the SLCs themselves (their own console cables)",
    )

    class Meta:
        name = "AOOB console coverage gate"
        description = (
            "Devices in scope with no console cable to a synced SLC port, or whose "
            "port isn't Managed or Identified. Writes console-coverage.json "
            "(aoob.console-coverage/v1)"
        )
        read_only = True
        has_sensitive_variables = False

    def run(
        self,
        location=None,
        roles=None,
        tags=None,
        devices="",
        ready_states="",
        include_console_servers=False,
    ):
        if self.user is None:
            self.fail("No requesting user; refusing to report unrestricted data")
            return ""
        scope = Scope(
            site=location.name if location else None,
            roles=[r.name for r in roles or []],
            tags=[t.name for t in tags or []],
            devices=split_list(devices),
            exclude_roles=[] if include_console_servers else nautobot_console_server_roles(),
        )
        ready = split_list(ready_states) or list(DEFAULT_READY_STATES)
        report = coverage_report("nautobot", nautobot_targets(self.user), scope, ready)
        for line in summary_lines(report):
            if line.startswith("FAIL"):
                self.logger.failure(line)
            elif line.startswith("ok"):
                self.logger.success(line)
            else:
                self.logger.info(line)
        body = json.dumps(report, indent=2)
        self.create_file("console-coverage.json", body)
        if report["result"] != "pass":
            self.fail(f"{report['summary']['uncovered']} device(s) without a ready console path")
        return body
