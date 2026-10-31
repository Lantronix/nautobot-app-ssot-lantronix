"""Source adapter: Lantronix devices, console ports and attached devices from Percepxion.

Framework-free apart from diffsync, so it runs under plain pytest with a fake client. Device
type resolution follows the NetBox plugin: EMG model mapping, a unique port-count match, stored
`show sysconfig` output, then a configured model rule. Anything unresolved is logged and left
out of the diff.
"""

from __future__ import annotations

from collections import Counter

from diffsync import Adapter
from diffsync.exceptions import ObjectAlreadyExists

from ..constants import DESCRIPTION_MAX, FACT_MAX, LABEL_MAX
from ..percepxion.client import PercepxionUnavailable
from ..percepxion.resolver import (
    emg_sku,
    is_default_port_name,
    parse_sysconfig,
    resolve_sku,
    sku_from_sysconfig,
    slug_for,
)
from ..sanitize import clean_text, safe_id, safe_ip, safe_name, safe_serial
from .models import LantronixConsolePort, LantronixDevice

STATE_MAX = 30


class PercepxionAdapter(Adapter):
    device = LantronixDevice
    console_port = LantronixConsolePort
    top_level = ("device",)

    def __init__(self, *args, job, config, client, **kwargs):
        super().__init__(*args, **kwargs)
        self.job = job
        self.config = config
        self.client = client
        self._sysconfig_jobs = None

    # -- device type ---------------------------------------------------------------------------

    def _sysconfig_slug(self, pxid: str) -> str | None:
        if self._sysconfig_jobs is None:
            self._sysconfig_jobs = self.client.latest_sysconfig_jobs(self.config.organization_id)
        job = self._sysconfig_jobs.get(pxid)
        if not job:
            return None
        return sku_from_sysconfig(parse_sysconfig(self.client.cli_output(job[0], pxid)))

    def resolve_type(self, dev) -> tuple[str | None, list[str]]:
        emg = emg_sku(dev.model)
        if emg:
            return slug_for(emg), [slug_for(emg)]
        candidates = resolve_sku(dev.model, dev.rj45_ports, dev.usb_ports)
        if len(candidates) == 1:
            return candidates[0], candidates
        if candidates:
            slug = self._sysconfig_slug(dev.device_id)
            if slug:
                return slug, candidates
            rule = self.config.model_rules.get((dev.model.upper(), dev.rj45_ports, dev.usb_ports))
            if rule:
                return rule, candidates
        return None, candidates

    # -- load ----------------------------------------------------------------------------------

    def load(self):
        log = self.job.logger
        org = self.config.organization_id
        devices = []
        outside = 0
        for dev in self.client.iter_devices():
            if org not in dev.org_ids:
                # Fail closed: a record with no organization counts as outside it. Only a count
                # is logged, so names from other tenants never reach the job log.
                outside += 1
                continue
            name, pxid = safe_name(dev.name), safe_id(dev.device_id)
            serial = safe_serial(dev.serial) if dev.serial else ""
            if not name or not pxid or serial is None:
                # An unusable serial is rejected, never treated as empty: empty relaxes the
                # identity guard.
                log.warning("Skipped a device with an unusable name, device ID or serial")
                continue
            devices.append((dev, name, pxid, serial))

        if outside:
            log.info(f"Skipped {outside} device(s) outside the configured organization")
        counts = Counter(name for _, name, _, _ in devices)
        loaded: dict[str, object] = {}
        for dev, name, pxid, serial in devices:
            if counts[name] > 1:
                log.warning(f"Skipped {name}: more than one Percepxion device has this name")
                continue
            slug, candidates = self.resolve_type(dev)
            if not slug:
                if candidates:
                    log.warning(
                        f"Skipped {name}: ambiguous model {clean_text(dev.model, 40)} (could be "
                        f"{', '.join(candidates)}). Run `show sysconfig` on it from Percepxion, "
                        "or add a model rule."
                    )
                else:
                    log.warning(
                        f"Skipped {name}: no Lantronix device type for model "
                        f"{clean_text(dev.model, 40)}"
                    )
                continue
            self.add(
                LantronixDevice(
                    percepxion_device_id=pxid,
                    name=name,
                    serial=serial,
                    mgmt_ip=safe_ip(dev.ip) or "",
                    device_type_slug=slug,
                )
            )
            loaded[pxid] = dev

        states = {}
        for pxid in loaded:
            try:
                states[pxid] = self.client.port_states(pxid, org)
            except PercepxionUnavailable as exc:
                # The device's ports stay out of the diff, so Nautobot keeps what it has.
                log.warning(f"Port state unavailable for {pxid}, ports left as they are: {exc}")

        for port in self.client.iter_ports():
            pxid = safe_id(port.device_id)
            if pxid not in states or port.number <= 0 or org not in port.org_ids:
                continue
            self._add_port(pxid, port, states[pxid].get(port.number))

    def _add_port(self, pxid, port, state) -> None:
        label = (
            "" if is_default_port_name(port.name, port.number) else clean_text(port.name, LABEL_MAX)
        )
        if state is not None and state.attached:
            hostname = clean_text(state.hostname, DESCRIPTION_MAX)
            attached = {
                "state": clean_text(state.state, STATE_MAX),
                "attached": True,
                "attached_type": clean_text(
                    " ".join(x for x in (state.make, state.os) if x), FACT_MAX
                ),
                "attached_model": clean_text(state.model, FACT_MAX),
                "attached_os_version": clean_text(state.os_version, FACT_MAX),
                "attached_serial": safe_serial(state.serial) or "",
                "attached_hostname": hostname,
                "attached_ip": safe_ip(state.ip) or "",
                "attached_last_seen": clean_text(state.last_seen, 40),
            }
        elif state is not None:
            hostname = ""
            attached = {"state": clean_text(state.state, STATE_MAX), "attached": False}
        else:
            # The view answered but didn't list this port: fall back to port search.
            hostname = clean_text(port.hostname, DESCRIPTION_MAX)
            facts = port.facts
            attached = {
                "attached": bool(facts.get("model")),
                "attached_type": clean_text(
                    " ".join(x for x in (facts.get("make", ""), facts.get("os", "")) if x), FACT_MAX
                ),
                "attached_model": clean_text(facts.get("model", ""), FACT_MAX),
                "attached_os_version": clean_text(facts.get("os_version", ""), FACT_MAX),
                "attached_serial": safe_serial(facts.get("serial", "")) or "",
                "attached_hostname": hostname,
            }
        model = LantronixConsolePort(
            percepxion_device_id=pxid,
            number=port.number,
            label=label,
            description=hostname,
            **attached,
        )
        try:
            self.add(model)
        except ObjectAlreadyExists:
            self.job.logger.warning(
                f"Duplicate record for port {port.number} on {pxid}; kept the first"
            )
            return
        self.get(LantronixDevice, {"percepxion_device_id": pxid}).add_child(model)
