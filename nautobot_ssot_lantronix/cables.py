"""Console cables on synced SLC ports, made to match what each SLC reports.

Runs after the device and port sync when ``manage_cables`` is on. The decisions come from
``percepxion/cabling.py``, shared with the NetBox plugin; this module builds its inputs from the
run's source adapter and Nautobot, and writes the plan in one transaction. Nautobot's job
context records each write in the change log.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .constants import CF_DEVICE_ID, MANUFACTURER
from .diffsync.models import LantronixConsolePort
from .percepxion.cabling import (
    OTHER,
    CablePlan,
    ConsolePortRef,
    Create,
    Delete,
    ObservedPort,
    Target,
    plan_cables,
)

TAG = "lantronix-discovered"
TAG_DESCRIPTION = "Console cable created from what a Lantronix SLC reports on the port"
CABLE_LABEL = "Observed by SLC discovery"

_PORT = re.compile(r"^Port-(\d{1,3})$")
_REASONS = {
    "disconnected": "the SLC reports the port Disconnected",
    "replaced": "the SLC reports a different device on the port",
    "moved": "the SLC reports this device on {label} now",
}
# Job log level per finding kind. attached_unmatched is expected while discovery names devices
# that aren't in Nautobot (or are outside the SLCs' locations); the rest need a person. A kind
# not listed here logs as a warning.
_FINDING_LEVELS = {
    "attached_unmatched": "info",
    "attached_ambiguous": "warning",
    "console_port_unclear": "warning",
    "duplicate_attachment": "warning",
    "port_cabled_elsewhere": "warning",
    "console_port_cabled_elsewhere": "warning",
    "hostname_only_match": "warning",
    "still_reported_elsewhere": "warning",
}


class _Stale(Exception):
    """Nautobot changed between the read and the write; nothing is written this run."""


@dataclass
class Snapshot:
    observed: list[ObservedPort] = field(default_factory=list)
    cables: dict = field(default_factory=dict)
    targets: list[Target] = field(default_factory=list)
    names: dict = field(default_factory=dict)  # port pk -> "SLC-A Port-03", "edge-01 Console"
    owner: dict = field(default_factory=dict)  # console port pk -> device pk


def _far_end(term, kind, snap: Snapshot):
    """The pk of the ``kind`` port at the other end of ``term``'s cable, OTHER when the cable
    ends on anything else, None when uncabled. Uses the cable API Nautobot 3.1 and 3.2 share."""
    if term.cable_id is None:
        return None
    peer = term.get_cable_peer()
    if not isinstance(peer, kind):
        return OTHER
    snap.names.setdefault(peer.pk, f"{peer.device.name} {peer.name}")
    snap.owner.setdefault(peer.pk, peer.device_id)
    return peer.pk


def _location_scope(slcs, config) -> set:
    """Location pks an attached device may sit in: each synced SLC's location and everything
    under it, narrowed to the adoption scope (``adoption_locations``, else ``default_location``;
    ``["*"]`` adds no limit). A scope name that matches no location, or several, allows nothing,
    as it does for adoption."""
    from nautobot.dcim.models import Location

    pks = set()
    for location in {d.location for d in slcs if d.location_id}:
        pks.update(location.descendants(include_self=True).values_list("pk", flat=True))
    names = (config.adoption_locations or (config.default_location,)) if config else ("*",)
    if "*" in names:
        return pks
    allowed = set()
    for name in names:
        found = list(Location.objects.filter(name=name)[:2])
        if len(found) == 1:
            allowed.update(found[0].descendants(include_self=True).values_list("pk", flat=True))
    return pks & allowed


def snapshot(source) -> Snapshot:
    """Plan inputs for the SLCs this run synced: their ports with a telemetry state, those
    ports' cables, and the devices an SLC reports as attached. Candidate devices are limited
    to the SLCs' locations (see ``_location_scope``), so a device elsewhere with the same
    serial or name is never cabled to them."""
    from django.db.models import Q
    from django.db.models.functions import Lower
    from nautobot.dcim.models import ConsolePort, ConsoleServerPort, Device

    snap = Snapshot()
    ports = [p for p in source.get_all(LantronixConsolePort) if p.observed]
    pxids = sorted({p.percepxion_device_id for p in ports})
    slcs = {}
    for dev in Device.objects.filter(
        device_type__manufacturer__name=MANUFACTURER,
        **{f"_custom_field_data__{CF_DEVICE_ID}__in": pxids},
    ):
        # Two devices with one Percepxion ID: the target adapter warns and uses the first.
        slcs.setdefault(dev._custom_field_data.get(CF_DEVICE_ID), dev)
    by_number = {}
    for csp in ConsoleServerPort.objects.filter(device__in=list(slcs.values())):
        match = _PORT.match(csp.name)
        if match:
            by_number[(csp.device_id, int(match.group(1)))] = csp

    for p in ports:
        dev = slcs.get(p.percepxion_device_id)
        csp = by_number.get((dev.pk, p.number)) if dev else None
        if csp is None:  # the device isn't in Nautobot yet (a dry run), or has no such port
            continue
        label = f"{dev.name} {csp.name}"
        snap.names[csp.pk] = label
        snap.observed.append(
            ObservedPort(
                dev.pk,
                csp.pk,
                p.state,
                p.attached,
                p.attached_serial,
                p.attached_hostname,
                label,
            )
        )
        far = _far_end(csp, ConsolePort, snap)
        if far is not None:
            snap.cables[csp.pk] = far

    serials = {_norm(o.serial) for o in snap.observed if o.attached} - {""}
    hosts = {_norm(o.hostname) for o in snap.observed if o.attached} - {""}
    scope = _location_scope(slcs.values(), getattr(source, "config", None))
    if not (serials or hosts) or not scope:
        return snap
    wanted = Q(lx_serial__in=serials) | Q(lx_name__in=hosts)
    devices = list(
        Device.objects.annotate(lx_serial=Lower("serial"), lx_name=Lower("name"))
        .filter(wanted)
        .filter(location__in=scope)
    )
    refs = defaultdict(list)
    names = {d.pk: d.name or "" for d in devices}
    for cp in ConsolePort.objects.filter(device__in=devices).order_by("name"):
        snap.names[cp.pk] = f"{names[cp.device_id]} {cp.name}"
        snap.owner[cp.pk] = cp.device_id
        refs[cp.device_id].append(
            ConsolePortRef(cp.pk, cp.name, _far_end(cp, ConsoleServerPort, snap))
        )
    snap.targets = [Target(d.pk, d.name or "", d.serial or "", tuple(refs[d.pk])) for d in devices]
    return snap


def _norm(value: str) -> str:
    return (value or "").strip().lower()


def _delete_text(d: Delete, snap: Snapshot) -> str:
    far = snap.names.get(snap.cables.get(d.port), "a console port")
    why = _REASONS.get(d.reason, d.reason).format(label=d.label)
    return f"console cable {snap.names.get(d.port, d.label)} to {far}: {why}"


def _create_text(c: Create, snap: Snapshot) -> str:
    return (
        f"console cable {snap.names.get(c.port, c.label)} to "
        f"{snap.names.get(c.console_port, c.target_name)}: the SLC reports {c.target_name} "
        f"attached (matched on {c.matched_on})"
    )


def manage_cables(source, logger, dryrun: bool, user=None) -> CablePlan:
    """Plan and, unless ``dryrun``, write this run's console cable changes. Returns the plan."""
    snap = snapshot(source)
    plan = plan_cables(snap.observed, snap.cables, snap.targets)
    for f in plan.findings:
        log = getattr(logger, _FINDING_LEVELS.get(f.kind, "warning"))
        log(f"Console cable on {f.label} not changed ({f.kind}): {f.detail}")
    deletes = [_delete_text(d, snap) for d in plan.deletes]
    creates = [_create_text(c, snap) for c in plan.creates]
    if dryrun:
        for text in deletes:
            logger.info(f"Dry run, would remove {text}")
        for text in creates:
            logger.info(f"Dry run, would create {text}")
    elif not plan.is_empty():
        from django.core.exceptions import ValidationError
        from django.db import IntegrityError, transaction

        try:
            with transaction.atomic():
                _write(plan, snap, user)
        except (ValidationError, IntegrityError, _Stale) as exc:
            detail = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            logger.error(f"Console cable changes rolled back, none written: {detail}")
            return plan
        for text in deletes:
            logger.info(f"Removed {text}")
        for text in creates:
            logger.info(f"Created {text}")
    verb = "would create" if dryrun else "created"
    logger.info(
        f"Console cables: {verb} {len(plan.creates)}, "
        f"{'would remove' if dryrun else 'removed'} {len(plan.deletes)}, "
        f"unchanged {plan.unchanged}, findings {len(plan.findings)}"
    )
    return plan


def _write(plan: CablePlan, snap: Snapshot, user) -> None:
    """Every delete, then every create, then one note per attached device. Raises on any
    failure so the caller's transaction rolls the whole batch back."""
    from django.contrib.contenttypes.models import ContentType
    from django.core.exceptions import ValidationError
    from nautobot.dcim.models import Cable, ConsolePort, ConsoleServerPort, Device
    from nautobot.extras.models import Note, Status, Tag

    notes = defaultdict(list)  # device pk -> note lines
    for d in plan.deletes:
        csp = ConsoleServerPort.objects.get(pk=d.port)
        cable = csp.cable
        peer = csp.get_cable_peer() if cable is not None else None
        expected = snap.cables.get(d.port)
        if not isinstance(peer, ConsolePort) or peer.pk != expected:
            raise _Stale(f"the cable on {snap.names.get(d.port, d.label)} changed during the run")
        cable.delete()
        why = _REASONS.get(d.reason, d.reason).format(label=d.label)
        notes[snap.owner[expected]].append(
            f"Removed the console cable to {snap.names.get(d.port, d.label)}: {why}."
        )

    if plan.creates:
        status = Status.objects.get_for_model(Cable).filter(name="Connected").first()
        if status is None:
            raise ValidationError("status Connected isn't available for cables")
        tag, _ = Tag.objects.get_or_create(name=TAG, defaults={"description": TAG_DESCRIPTION})
        tag.content_types.add(ContentType.objects.get_for_model(Cable))
    for c in plan.creates:
        cable = Cable(
            termination_a=ConsoleServerPort.objects.get(pk=c.port),
            termination_b=ConsolePort.objects.get(pk=c.console_port),
            status=status,
            label=CABLE_LABEL,
        )
        cable.validated_save()
        cable.tags.add(tag)
        notes[c.target].append(
            f"Created a console cable to {snap.names.get(c.port, c.label)}: the SLC reports "
            f"this device attached (matched on {c.matched_on})."
        )

    for device in Device.objects.filter(pk__in=list(notes)):
        lines = "\n".join(f"- {line}" for line in notes[device.pk])
        Note.objects.create(assigned_object=device, user=user, note=f"Lantronix SSoT:\n\n{lines}")
