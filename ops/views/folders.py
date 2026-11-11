"""Project folders on drives.

The drive desk (or a founder) pastes the folder list of a drive; each folder waits on
Projects for a founder to decide: Add as project (films are created under that couple,
linked to the drive) or Decline (the folder just stays listed on the drive). Either way it
can be found later from the Drive register search."""
from datetime import date

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .. import services as S
from ..audit import record
from ..folders import parse_many
from ..models import Agency, Couple, DriveFolder, HardDrive, Project
from ..rbac import requires
from ..services import team

__all__ = ["drive_folders_add", "drive_folder_status", "folder_decide", "folders_bulk"]

INBOX = 25


def _is_fetch(request):
    return request.headers.get("X-Requested-With") == "fetch"


def _reply(request, say, back, swap=None, refresh=False, remove=None):
    if _is_fetch(request):
        bad = any(level == "error" for level, _ in say)
        return JsonResponse({"ok": not bad, "messages": [{"level": l, "text": t} for l, t in say],
                             "swap": swap or [], "refresh": refresh, "remove": remove or []})
    for level, text in say:
        getattr(messages, level)(request, text)
    return redirect(back)


def _list_swap(request, d):
    html = render_to_string("drives/_folders.html", {"d": d, "folders": d.folders.select_related("client")},
                            request=request)
    return [{"sel": "[data-drive-folders]", "mode": "outer", "html": html}]


def inbox_context():
    qs = DriveFolder.objects.filter(status=DriveFolder.PENDING).select_related("drive__agency").order_by("added_on", "path")
    items = list(qs[:INBOX])
    keys = {(f.drive.agency_id, Couple.key_for(f.couple_guess)) for f in items if f.drive.agency_id}
    existing = {}
    if keys:
        for c in Couple.objects.filter(agency_id__in={a for a, _ in keys}, key__in={k for _, k in keys}).prefetch_related("films"):
            existing[(c.agency_id, c.key)] = c
    for f in items:
        c = existing.get((f.drive.agency_id, Couple.key_for(f.couple_guess)))
        f.existing = [p.deliverable for p in c.films.all()] if c else []
    return {"folders": items, "folders_total": qs.count() if len(items) == INBOX else len(items),
            "deliverables": Project.DELIVERABLES}


def add_folders(request, d, text):
    """List pasted folder names on drive d. Returns (how many were new, parsed rows)."""
    rows = parse_many(text)[:500]
    have = {p.lower() for p in d.folders.values_list("path", flat=True)}
    made = 0
    for r in rows:
        if r["path"].lower() in have:
            continue
        try:
            with transaction.atomic():
                DriveFolder.objects.create(drive=d, path=r["path"], name=r["name"], couple_guess=r["couple"],
                                           films_guess=",".join(r["films"]), event_date=r["date"],
                                           added_by=request.user)
            made += 1
        except IntegrityError:
            pass
    return made, rows


@requires("drives.manage")
def drive_folders_add(request, pk):
    d = get_object_or_404(HardDrive, pk=pk)
    back = reverse("drive_edit", args=[d.pk])
    if request.method != "POST":
        return redirect(back)
    made, rows = add_folders(request, d, request.POST.get("folders", ""))
    skipped = len(rows) - made
    if not rows:
        say = [("error", "Paste at least one folder name (one per line).")]
    else:
        record(request, "drive.folders", d.code, f"{made} folder(s) added")
        say = [("success", f"{d.code}: {made} folder(s) added" + (f", {skipped} already listed" if skipped else "")
                + ". They're waiting on Projects for a founder to decide." if made else
                f"{d.code}: those folders are already listed.")]
    return _reply(request, say, back, swap=_list_swap(request, d))


@requires("drives.manage")
def drive_folder_status(request, pk):
    f = get_object_or_404(DriveFolder.objects.select_related("drive"), pk=pk)
    back = reverse("drive_edit", args=[f.drive_id])
    if request.method != "POST":
        return redirect(back)
    op = request.POST.get("op")
    if op == "remove" and f.status != DriveFolder.ACCEPTED:
        f.delete()
        say = [("success", f"Removed {f.path} from {f.drive.code}.")]
    elif op == "review" and f.status == DriveFolder.DECLINED:
        f.status, f.decided_by, f.decided_on = DriveFolder.PENDING, None, None
        f.save(update_fields=["status", "decided_by", "decided_on"])
        say = [("success", f"{f.path} is back on Projects for a decision.")]
    else:
        say = [("error", "That can't be done to this folder.")]
    return _reply(request, say, back, swap=_list_swap(request, f.drive))


def _date(v):
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return False


def accept(request, f, name, agency, films, editor=None, deadline=None, agency_deadline=None):
    """Create the films for a folder under its couple. Returns messages."""
    couple = Couple.for_name(agency, name)
    have = set(couple.films.values_list("deliverable", flat=True))
    made, skipped, planned = [], [], 0
    for d in films:
        if d in have:
            skipped.append(d)
            continue
        rate = agency.rates.filter(deliverable=d).values_list("amount", flat=True).first() or 0
        p = Project(couple=couple.name, client=couple, agency=agency, deliverable=d, editor=editor,
                    deadline=deadline or None, agency_deadline=agency_deadline or None, invoice_amount=rate, drive=f.drive)
        p.save()
        if editor:
            planned += S.auto_plan(p)[0]
        made.append(d)
    if f.event_date and not couple.wedding_date:
        couple.wedding_date = f.event_date
        couple.save(update_fields=["wedding_date", "name", "key"])
    f.status, f.client, f.decided_by, f.decided_on = DriveFolder.ACCEPTED, couple, request.user, timezone.now()
    f.save(update_fields=["status", "client", "decided_by", "decided_on"])
    record(request, "folder.accept", str(f), f"{couple} · {', '.join(made) or 'no new films'}")
    msg = f"{couple}: " + (f"added {', '.join(made)}" if made else "no new films") + f" (footage on {f.drive.code})."
    if editor and made:
        msg += f" Given to {editor.profile.name}" + (f", {planned} day(s) planned." if planned else ".")
    say = [("success", msg)]
    if skipped:
        say.append(("info", f"{couple} already had {', '.join(skipped)}, so those weren't added again."))
    return say


def _decline(request, f):
    f.status, f.decided_by, f.decided_on = DriveFolder.DECLINED, request.user, timezone.now()
    f.save(update_fields=["status", "decided_by", "decided_on"])
    record(request, "folder.decline", str(f), "")


@requires("projects.manage")
def folder_decide(request, pk):
    f = get_object_or_404(DriveFolder.objects.select_related("drive__agency"), pk=pk)
    back = request.POST.get("next") or reverse("projects")
    back = S.safe_next(request, back, reverse("projects"))
    if request.method != "POST":
        return redirect(back)
    P = request.POST
    if f.status != DriveFolder.PENDING:
        return _reply(request, [("info", f"{f.path} was already decided.")], back, refresh=True)
    if P.get("op") == "decline":
        _decline(request, f)
        return _reply(request, [("success", f"Declined {f.name}. It stays listed on {f.drive.code}.")], back,
                      remove=[f'[data-folder="{f.pk}"]'], refresh=True)
    name = " ".join((P.get("couple") or f.couple_guess).split())[:120]
    raw = P.get("agency", "")
    agency = Agency.objects.filter(pk=raw).first() if raw.isdigit() else f.drive.agency
    films = [x for x in P.getlist("films") if x in Project.DELIVERABLES]
    raw = P.get("editor", "")
    editor = team().filter(pk=raw).first() if raw.isdigit() else None
    ed, ad = _date(P.get("deadline")), _date(P.get("agency_deadline"))
    err = ("Give the couple a name." if not name else "Pick the agency." if not agency
           else "Tick at least one film to add." if not films
           else "One of the dates isn't valid." if ed is False or ad is False
           else "The editor's deadline can't be after the agency's." if ed and ad and ed > ad else "")
    if err:
        return _reply(request, [("error", f"{f.name}: {err}")], back)
    with transaction.atomic():
        say = accept(request, f, name, agency, films, editor, ed, ad)
    return _reply(request, say, back, remove=[f'[data-folder="{f.pk}"]'], refresh=True)


@requires("projects.manage")
def folders_bulk(request):
    back = S.safe_next(request, request.POST.get("next"), reverse("projects"))
    if request.method != "POST":
        return redirect(back)
    P = request.POST
    ids = [int(x) for x in P.getlist("ids") if x.isdigit()]
    folders = list(DriveFolder.objects.filter(pk__in=ids, status=DriveFolder.PENDING).select_related("drive__agency"))
    if not folders:
        return _reply(request, [("error", "Tick the folders first.")], back)
    if P.get("op") == "decline":
        for f in folders:
            _decline(request, f)
        return _reply(request, [("success", f"Declined {len(folders)} folder(s). They stay listed on their drives.")],
                      back, refresh=True)
    films = [x for x in P.getlist("films") if x in Project.DELIVERABLES]
    say, done, left = [], 0, []
    with transaction.atomic():
        for f in folders:
            mine = films or f.films_list
            if not f.drive.agency or not mine:
                left.append(f.name)
                continue
            accept(request, f, f.couple_guess, f.drive.agency, mine)
            done += 1
    say.append(("success", f"Added {done} folder(s) as projects."))
    if left:
        say.append(("warning", f"Not added (needs an agency or films picked one by one): {', '.join(left)}."))
    return _reply(request, say, back, refresh=True)
