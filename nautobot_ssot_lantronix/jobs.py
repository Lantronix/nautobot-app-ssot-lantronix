"""The Percepxion to Nautobot SSoT data-source job, the focused sync and change watch that
keep it current between full syncs, and the console coverage gate job."""

from __future__ import annotations

import logging

from diffsync.enum import DiffSyncFlags
from django.conf import settings
from django.core.cache import cache
from nautobot.apps.jobs import ChoiceVar, Job, register_jobs
from nautobot.core.utils.logging import sanitize
from nautobot_ssot.jobs.base import DataSource

from . import cables
from . import settings as app_settings
from .bootstrap import ensure_objects
from .constants import CF_DEVICE_ID, MANUFACTURER
from .coverage.job import ConsoleCoverageGate
from .diffsync.nautobot import NautobotAdapter
from .diffsync.percepxion import PercepxionAdapter
from .percepxion.client import AuthError, PartialData, PercepxionClient, PercepxionUnavailable
from .percepxion.sync_requests import SyncRequests
from .percepxion.watch import changed, fingerprint
from .servicenow import unavailable_reason

name = "Lantronix SSoT"  # job grouping in the Nautobot UI

# Nautobot keeps a failed job's traceback on the job result, but the job's own log doesn't say
# why it failed. This logger has no handler of its own, so with Nautobot's default logging its
# warnings reach the worker's stderr (`docker logs`); a LOGGING setting can route it.
worker_log = logging.getLogger("nautobot_ssot_lantronix.sync")

FOCUS_DELAY = 5  # seconds a focused sync waits for more requests to join it
REQUEUE_DELAY = 15  # seconds before a focused sync that found another sync running tries again
WATCH_CACHE_TTL = 24 * 60 * 60
WATCH_KEY = "nautobot_ssot_lantronix:watch"


def _cable_choices():
    """Per-run choices for console cable management. Blank follows the app setting, so runs
    started through the API or a schedule with no inputs (where a checkbox would arrive as
    off) do what the configuration says."""
    on = app_settings.manage_cables_setting(
        settings.PLUGINS_CONFIG.get("nautobot_ssot_lantronix", {})
    )
    return (
        ("", f"App setting ({'on' if on else 'off'})"),
        ("on", "On for this run"),
        ("off", "Off for this run"),
    )


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
        # Nautobot's defaults (300 s soft, 600 s hard) are too short for a few hundred
        # devices: the run reads port state one device at a time. A hard kill lands mid-write.
        # Both can be overridden on the job's edit page.
        soft_time_limit = 7200
        time_limit = 7500

    manage_cables = ChoiceVar(
        choices=_cable_choices(),
        required=False,
        default="",
        label="Manage console cables",
        description=(
            "Make console cables on synced SLC ports match what each SLC reports. Leave it at "
            "the app setting for scheduled and API runs."
        ),
    )

    def __init__(self):
        super().__init__()
        # Target-only objects are left alone: the app never deletes.
        self.diffsync_flags = DiffSyncFlags.SKIP_UNMATCHED_DST
        self._config = None
        self._cables_choice = ""
        self._focus = None  # Percepxion device IDs, for a focused sync

    def run(self, *args, **kwargs):
        self._cables_choice = kwargs.get("manage_cables") or ""
        super().run(*args, **kwargs)

    def cables_enabled(self) -> bool:
        if self._cables_choice in ("on", "off"):
            return self._cables_choice == "on"
        return self.source_config.manage_cables

    def sync_data(self, memory_profiling):
        super().sync_data(memory_profiling)
        if self.source_adapter is None or not self.cables_enabled():
            return
        # After the device and port sync, from the same Percepxion read: nothing is re-read.
        cables.manage_cables(self.source_adapter, self.logger, dryrun=self.dryrun, user=self.user)

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
        if not cfg.verify and not cfg.ca_bundle:
            self.logger.warning(
                "TLS certificate verification is off for the Percepxion endpoint "
                "(verify or PERCEPXION_VERIFY). The server's identity isn't checked."
            )
        self.source_adapter = PercepxionAdapter(
            job=self, config=cfg, client=client, focus=self._focus
        )
        try:
            client.login()
            self.source_adapter.load()
        except AuthError:
            raise RuntimeError("Percepxion rejected the login for the configured account") from None
        except (PercepxionUnavailable, PartialData) as exc:
            raise RuntimeError(f"Percepxion read failed, nothing changed: {exc}") from None
        finally:
            client.logout()  # everything Percepxion is asked for has been read by now

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
        self.source_adapter.keep_unread_ssh_ports(self.target_adapter)


class PercepxionFocusedSync(PercepxionDataSource):
    """The SSoT sync for only the console servers that changed. Events, the change watch and
    the events API request it; requests made while one waits to start join it."""

    class Meta:
        name = "Percepxion focused sync"
        data_source = "Percepxion"
        data_target = "Nautobot"
        description = (
            "Sync only the Lantronix console servers that events or the change watch reported "
            "as changed. Started by them; a manual run with nothing waiting does nothing."
        )
        has_sensitive_variables = False
        soft_time_limit = 1800
        time_limit = 1900

    def run(self, *args, **kwargs):
        devices = sync_requests().take()
        if not devices:
            self.logger.info("No console servers are waiting for a focused sync")
            return
        if other_sync_running(self.job_result):
            # Requests are never dropped for a running sync: they wait for the next run.
            request_sync(devices, self.user, delay=REQUEUE_DELAY)
            self.logger.info(
                "Another Percepxion sync is running; this focused sync runs again after it"
            )
            return
        self._focus = devices
        self.logger.info(f"Focused sync of {len(devices)} console server(s)")
        super().run(*args, **kwargs)


class PercepxionChangeWatch(Job):
    """Read each linked console server's port state and request a focused sync for the ones
    whose ports changed since the last check. Bridges the time until Percepxion sends events;
    with events, schedule it rarely or not at all."""

    class Meta:
        name = "Percepxion change watch"
        description = (
            "Check each Lantronix console server's port state in Percepxion and sync the ones "
            "whose ports changed since the last check. Schedule it every few minutes."
        )
        has_sensitive_variables = False
        is_singleton = True
        soft_time_limit = 1800
        time_limit = 1900

    def run(self):
        from nautobot.dcim.models import Device

        raw = settings.PLUGINS_CONFIG.get("nautobot_ssot_lantronix", {})
        try:
            cfg = app_settings.load(raw)
        except app_settings.ConfigError as exc:
            raise RuntimeError(f"Lantronix SSoT settings: {exc}") from None
        linked = {
            (d._custom_field_data or {}).get(CF_DEVICE_ID)
            for d in Device.objects.filter(device_type__manufacturer__name=MANUFACTURER).only(
                "_custom_field_data"
            )
        } - {None, ""}
        if not linked:
            self.logger.info("No console servers linked to Percepxion to watch")
            return {"watched": 0, "changed": 0, "unread": 0}
        org = cfg.organization_id
        current, unread = {}, 0
        client = PercepxionClient(
            cfg.url, cfg.username, cfg.password, verify=cfg.verify, ca_bundle=cfg.ca_bundle
        )
        try:
            client.login()
            for dev in client.iter_devices():
                if dev.device_id not in linked or org not in dev.org_ids:
                    continue
                try:
                    current[dev.device_id] = fingerprint(client.port_states(dev.device_id, org))
                except PercepxionUnavailable:
                    unread += 1  # keeps its last fingerprint, so it's no change
        except AuthError:
            raise RuntimeError("Percepxion rejected the login for the configured account") from None
        except (PercepxionUnavailable, PartialData) as exc:
            raise RuntimeError(f"Percepxion read failed: {exc}") from None
        finally:
            client.logout()
        previous = cache.get(WATCH_KEY) or {}
        moved = changed(previous, current)
        kept = {d: fp for d, fp in previous.items() if d in linked and d not in current}
        cache.set(WATCH_KEY, {**kept, **current}, WATCH_CACHE_TTL)
        if moved:
            request_sync(moved, self.user)
            self.logger.info(
                f"Ports changed on {len(moved)} console server(s); focused sync requested"
            )
        else:
            self.logger.info(f"No port changes on {len(current)} console server(s)")
        return {"watched": len(current), "changed": len(moved), "unread": unread}


# -- focused sync requests -------------------------------------------------------------------
#
# Events and the change watch ask for some console servers to be synced. Requests made while a
# focused sync waits to start join it (percepxion/sync_requests).


def sync_requests() -> SyncRequests:
    return SyncRequests(cache, "nautobot_ssot_lantronix:percepxion")


def focused_job_model():
    """The focused sync's Job record, or None before Nautobot has registered it."""
    from nautobot.extras.models import Job as JobModel

    return JobModel.objects.filter(
        module_name=__name__, job_class_name=PercepxionFocusedSync.__name__
    ).first()


def request_sync(device_ids, user, delay=FOCUS_DELAY) -> bool:
    """Ask for a focused sync of these console servers (Percepxion device IDs), run as
    ``user``. Returns True if this call enqueued the job, False if the IDs joined one already
    waiting. Raises RuntimeError when the focused sync job isn't enabled."""
    from nautobot.extras.models import JobResult

    requests = sync_requests()
    if not requests.add(device_ids):
        return False
    try:
        job_model = focused_job_model()
        if job_model is None or not job_model.enabled:
            raise RuntimeError("The Percepxion focused sync job isn't enabled")
        JobResult.enqueue_job(
            job_model,
            user,
            job_kwargs={"dryrun": False, "memory_profiling": False, "manage_cables": ""},
            celery_kwargs={"countdown": delay},
        )
    except Exception:
        requests.release()
        raise
    return True


def other_sync_running(job_result) -> bool:
    """True while another Percepxion sync (full or focused) is running."""
    from nautobot.extras.choices import JobResultStatusChoices
    from nautobot.extras.models import JobResult

    names = (PercepxionDataSource.__name__, PercepxionFocusedSync.__name__)
    running = JobResult.objects.filter(
        job_model__module_name=__name__,
        job_model__job_class_name__in=names,
        status=JobResultStatusChoices.STATUS_STARTED,
    )
    if job_result is not None:
        running = running.exclude(pk=job_result.pk)
    return running.exists()


jobs = [PercepxionDataSource, PercepxionFocusedSync, PercepxionChangeWatch, ConsoleCoverageGate]

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
