"""The Percepxion to Nautobot SSoT data-source job."""

from __future__ import annotations

import logging

from diffsync.enum import DiffSyncFlags
from django.conf import settings
from nautobot.apps.jobs import register_jobs
from nautobot.core.utils.logging import sanitize
from nautobot_ssot.jobs.base import DataSource

from . import settings as app_settings
from .bootstrap import ensure_objects
from .diffsync.nautobot import NautobotAdapter
from .diffsync.percepxion import PercepxionAdapter
from .percepxion.client import AuthError, PartialData, PercepxionClient, PercepxionUnavailable
from .servicenow import unavailable_reason

name = "Lantronix SSoT"  # job grouping in the Nautobot UI

# Nautobot keeps a failed job's traceback on the job result, but the job's own log doesn't say
# why it failed. This logger has no handler of its own, so with Nautobot's default logging its
# warnings reach the worker's stderr (`docker logs`); a LOGGING setting can route it.
worker_log = logging.getLogger("nautobot_ssot_lantronix.sync")


class PercepxionDataSource(DataSource):
    """Lantronix console servers, console ports and attached devices from Percepxion."""

    class Meta:
        name = "Percepxion to Nautobot"
        data_source = "Percepxion"
        data_target = "Nautobot"
        description = (
            "Sync Lantronix SLC and EMG console servers, console port names and attached devices "
            "from Percepxion. Read-only toward Percepxion."
        )
        has_sensitive_variables = False

    def __init__(self):
        super().__init__()
        # Target-only objects are left alone: the app never deletes.
        self.diffsync_flags = DiffSyncFlags.SKIP_UNMATCHED_DST
        self._config = None

    @property
    def source_config(self):
        if self._config is None:
            raw = settings.PLUGINS_CONFIG.get("nautobot_ssot_lantronix", {})
            try:
                self._config = app_settings.load(raw)
            except app_settings.ConfigError as exc:
                raise RuntimeError(f"Lantronix SSoT settings: {exc}") from None
        return self._config

    def load_source_adapter(self):
        cfg = self.source_config
        client = PercepxionClient(
            cfg.url, cfg.username, cfg.password, verify=cfg.verify, ca_bundle=cfg.ca_bundle
        )
        self.source_adapter = PercepxionAdapter(job=self, config=cfg, client=client)
        try:
            client.login()
            self.source_adapter.load()
        except AuthError:
            raise RuntimeError("Percepxion rejected the login for the configured account") from None
        except (PercepxionUnavailable, PartialData) as exc:
            raise RuntimeError(f"Percepxion read failed, nothing changed: {exc}") from None

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        reason = sanitize(str(exc))
        self.logger.error(f"Sync failed: {reason}")
        worker_log.warning("Percepxion sync failed (job result %s): %s", task_id, reason)
        super().on_failure(exc, task_id, args, kwargs, einfo)

    def load_target_adapter(self):
        cfg = self.source_config
        if not self.dryrun:
            ensure_objects(cfg, self.logger)
        self.target_adapter = NautobotAdapter(job=self, sync=self.sync, config=cfg)
        self.target_adapter.load()


jobs = [PercepxionDataSource]

# Optional ServiceNow console-path job: only when nautobot-ssot's ServiceNow
# integration is enabled and importable, so core installs need no ServiceNow extras.
_servicenow_reason = unavailable_reason()
if _servicenow_reason is None:
    from .servicenow.jobs import jobs as servicenow_jobs

    jobs.extend(servicenow_jobs)
else:
    logging.getLogger(__name__).info(
        "ServiceNow console-path job not registered: %s", _servicenow_reason
    )

register_jobs(*jobs)
