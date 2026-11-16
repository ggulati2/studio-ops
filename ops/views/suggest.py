"""Hard drives <-> projects, matched by the couple's name.

- Receiving or editing a drive: type a couple in Contents and pick its films; they get
  linked to the drive (their footage is on it).
- Adding a film: as the couple's name is typed, drives that look like they hold it are
  suggested (a folder listed on the drive, other films of that couple, or the drive's
  Contents note); picking one sets "Footage on"."""
import re

from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone

from ..audit import record
from ..models import DriveFolder, HardDrive, Project
from ..rbac import requires

__all__ = ["suggest_films", "suggest_footage", "film_label", "link_drive_films"]

NOISE = {"and", "wedding", "the", "of", "with", "weds"}


def words(q):
    return [w for w in re.split(r"[\s&_,.+/\\()\-]+", (q or "").lower()) if len(w) >= 2 and w not in NOISE][:5]


def film_label(p):
    return f"{p.couple} · {p.deliverable}" + (f" · {p.agency.code}" if p.agency_id and p.agency.code else "")


def _film(p):
    return {"id": p.pk, "label": film_label(p), "couple": p.couple, "film": p.deliverable,
            "agency": p.agency.code if p.agency_id else "", "status": p.get_status_display(),
            "closed": p.status == Project.CLOSED, "drive": p.drive.code if p.drive_id else ""}


@requires("drives.manage")
def suggest_films(request):
    ws = words(request.GET.get("q"))
    if not ws:
        return JsonResponse({"films": []})
    q = Q()
    for w in ws:
        q &= Q(couple__icontains=w)
    films = sorted(Project.objects.filter(q).select_related("agency", "drive")[:60],
                   key=lambda p: (p.status == Project.CLOSED, p.couple.lower(), p.deliverable))[:30]
    return JsonResponse({"films": [_film(p) for p in films]})


@requires("projects.manage")
def suggest_footage(request):
    """Drives that look like they hold this couple's footage."""
    ws = words(request.GET.get("q"))
    if not ws:
        return JsonResponse({"drives": []})
    out, seen = [], set()

    def add(d, why, folder=None, films=()):
        if d.pk in seen or len(out) >= 8:
            return
        seen.add(d.pk)
        out.append({"id": d.pk, "code": d.code, "agency": d.agency.code if d.agency_id else "", "agency_id": d.agency_id,
                    "status": d.get_status_display(), "why": why, "folder": folder, "films": list(films)})

    live = HardDrive.objects.exclude(status="sent")
    fq, pq, cq = Q(), Q(), Q()
    for w in words(request.GET.get("q")):
        fq &= Q(couple_guess__icontains=w) | Q(path__icontains=w)
        pq &= Q(couple__icontains=w)
        cq &= Q(contents__icontains=w)
    for f in (DriveFolder.objects.filter(fq, drive__in=live).select_related("drive__agency")
              .order_by("status", "-added_on")[:12]):
        pending = f.status == DriveFolder.PENDING
        add(f.drive, f"folder {f.name}" + (" (waiting on Projects)" if pending else ""),
            f.pk if pending else None, f.films_list)
    for p in Project.objects.filter(pq, drive__in=live).select_related("drive__agency").order_by("-id")[:12]:
        add(p.drive, f"has {p.couple} · {p.deliverable}")
    for d in live.filter(cq).select_related("agency")[:6]:
        add(d, f"contents: {d.contents}")
    return JsonResponse({"drives": out})


def link_drive_films(request, d):
    """Link the films picked in a drive's Contents box (hidden films_link) to drive d.
    Films taken off the box are unlinked. Without JS nothing is sent and nothing changes."""
    if "films_link" not in request.POST:
        return 0
    ids = {int(x) for x in request.POST.get("films_link", "").split(",") if x.strip().isdigit()}
    gone = list(d.projects.exclude(pk__in=ids).values_list("pk", flat=True))
    if gone:
        Project.objects.filter(pk__in=gone).update(drive=None)
    new = list(Project.objects.filter(pk__in=ids).exclude(drive=d).select_related("agency"))
    Project.objects.filter(pk__in=[p.pk for p in new]).update(drive=d, received_online=False, cloud_link="")
    if new or gone:
        record(request, "drive.films", d.code, f"linked {', '.join(film_label(p) for p in new) or '-'}; "
                                              f"unlinked {len(gone)}")
    return len(new)


def films_summary(projects):
    """'Amit & Tanya – Teaser, Trailer; Riya & Arjun – Highlight'"""
    couples = {}
    for p in sorted(projects, key=lambda p: (p.couple.lower(), p.deliverable)):
        couples.setdefault(p.couple, []).append(p.deliverable)
    return "; ".join(f"{c} – {', '.join(fs)}" for c, fs in couples.items())


def accept_folder_for(request, folder_id, jobs):
    """A new film was given footage from a listed folder: that folder is now decided."""
    if not folder_id or not jobs or not str(folder_id).isdigit():
        return
    f = DriveFolder.objects.filter(pk=folder_id, status=DriveFolder.PENDING, drive_id=jobs[0].drive_id).first()
    if f:
        f.status, f.client, f.decided_by, f.decided_on = DriveFolder.ACCEPTED, jobs[0].client, request.user, timezone.now()
        f.save(update_fields=["status", "client", "decided_by", "decided_on"])
