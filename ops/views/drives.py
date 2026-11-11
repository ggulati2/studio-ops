"""Drive desk (and founders): hard drive register and send-backs.
The drive desk never sees projects' money, clients' rates or payroll."""
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from ..audit import record
from ..rbac import ADMIN, has_perm, requires
from ..forms import DriveForm, DriveReturnForm, ReceiveDriveForm
from ..models import DriveFolder, DriveReturn, HardDrive, Project
from ..services import store_image

__all__ = ["drives", "drive_edit", "drive_returns", "drive_return_status", "drive_delete"]


def _next_code(kind="HDD"):
    nums = [int(c.split("-")[-1]) for c in HardDrive.objects.filter(code__startswith=kind + "-").values_list("code", flat=True)
            if c.split("-")[-1].isdigit()]
    return f"{kind}-{(max(nums) + 1) if nums else 1:02d}"


def _save_photos(form, target):
    for f in form.cleaned_data.get("photos_upload") or []:
        target.photos.add(store_image(f))


@requires("drives.manage")
def drives(request):
    """Drive register: filters and search redraw in place (live.js); the search also
    looks inside folder names, couples and films on each drive."""
    g = request.GET
    qs = HardDrive.objects.select_related("agency").prefetch_related("photos", "folders")
    status, q = g.get("status", ""), g.get("q", "").strip()
    f = {"agency": g.get("agency", ""), "kind": g.get("kind", ""), "folders": g.get("folders", ""),
         "sort": g.get("sort", "")}
    if status == "all":
        pass
    elif status in dict(HardDrive.STATUS):
        qs = qs.filter(status=status)
    else:
        status = ""
        qs = qs.exclude(status="sent")
    if f["agency"] == "studio":
        qs = qs.filter(studio_owned=True)
    elif f["agency"].isdigit():
        qs = qs.filter(agency_id=f["agency"])
    if f["kind"] in ("HDD", "SSD", "Card"):
        qs = qs.filter(kind=f["kind"])
    if f["folders"] in (DriveFolder.PENDING, DriveFolder.ACCEPTED, DriveFolder.DECLINED):
        qs = qs.filter(folders__status=f["folders"]).distinct()
    elif f["folders"] == "none":
        qs = qs.filter(folders__isnull=True)
    hits = []
    if q:   # every word must match somewhere ("zara kabir" finds "Zara_Kabir_Wedding")
        hq = Q()
        for w in q.replace("_", " ").split()[:6]:
            qs = qs.filter(Q(code__icontains=w) | Q(contents__icontains=w) | Q(agency__name__icontains=w)
                           | Q(serial__icontains=w) | Q(agency__code__iexact=w) | Q(location__icontains=w)
                           | Q(folders__path__icontains=w) | Q(folders__couple_guess__icontains=w)
                           | Q(projects__couple__icontains=w))
            hq &= (Q(path__icontains=w) | Q(couple_guess__icontains=w) | Q(client__name__icontains=w)
                   | Q(drive__code__icontains=w))
        qs = qs.distinct()
        hits = list(DriveFolder.objects.filter(hq).select_related("drive__agency", "client")
                    .order_by("drive__code", "path")[:60])
    qs = qs.order_by({"newest": "-received_on", "oldest": "received_on", "agency": "agency__name"}.get(f["sort"], "code"), "code")
    form = ReceiveDriveForm(request.POST or None, request.FILES or None, initial={"code": _next_code()})
    if request.method == "POST" and form.is_valid():
        d = form.save()
        _save_photos(form, d)
        from .folders import add_folders
        made, _ = add_folders(request, d, form.cleaned_data.get("folder_list", ""))
        record(request, "drive.receive", d.code, f"{made} folder(s)")
        messages.success(request, f"{d.code} recorded. Write {d.code} on its label."
                         + (f" {made} folder(s) listed; they're waiting on Projects for a decision." if made
                            else " You can paste its folders below."))
        return redirect("drive_edit", d.pk)
    from django.db.models import Count
    by = dict(HardDrive.objects.order_by().values_list("status").annotate(n=Count("id")))
    chips = [("", "In studio", sum(n for k, n in by.items() if k != "sent"))] + \
            [(k, label, by.get(k, 0)) for k, label in HardDrive.STATUS] + [("all", "All", sum(by.values()))]
    drives_list = list(qs)
    for d in drives_list:
        fs = d.folders.all()
        d.n_folders = len(fs)
        d.n_waiting = sum(1 for x in fs if x.status == DriveFolder.PENDING)
    active = sum(1 for k in ("agency", "kind", "folders") if f[k]) + (1 if q else 0)
    from ..models import Agency
    return render(request, "drives/register.html", {
        "drives": drives_list, "form": form, "chips": chips, "hits": hits, "f": f, "active": active,
        "status": status, "q": q, "statuses": HardDrive.STATUS, "agencies": Agency.objects.order_by("name"),
        "waiting": DriveFolder.objects.filter(status=DriveFolder.PENDING).count(),
        "can_delete": has_perm(request.user, "projects.manage")})


@requires("drives.manage")
def drive_edit(request, pk):
    d = get_object_or_404(HardDrive, pk=pk)
    form = DriveForm(request.POST or None, request.FILES or None, instance=d)
    if request.method == "POST" and form.is_valid():
        form.save()
        _save_photos(form, d)
        messages.success(request, f"{d.code} saved.")
        return redirect("drives")
    return render(request, "drives/edit.html", {"d": d, "form": form, "folders": d.folders.select_related("client"),
                                                "can_delete": has_perm(request.user, "projects.manage"),
                                                "jobs": d.projects.only("couple", "deliverable", "status")})


@requires("drives.manage")
def drive_returns(request):
    ready = HardDrive.objects.filter(status__in=("ready", "backup_done")).select_related("agency")
    sel = ready.filter(pk=request.GET.get("d")).first() or ready.first()
    form = None
    if sel:
        open_jobs = sel.open_jobs
        form = DriveReturnForm(request.POST or None, request.FILES or None,
                               initial={"sent_to": f"{sel.agency.name} – {sel.agency.contact_person}" if sel.agency else ""})
        if request.method == "POST" and form.is_valid():
            if open_jobs.exists():
                messages.error(request, f"{sel.code} still has open jobs; it can't go back yet.")
            else:
                r = form.save(commit=False)
                r.drive, r.recorded_by = sel, request.user
                r.save()
                _save_photos(form, r)
                sel.status = "sent"
                sel.save(update_fields=["status"])
                messages.success(request, f"{sel.code} recorded as sent back.")
                return redirect("drive_returns")
    return render(request, "drives/returns.html", {
        "ready": ready, "sel": sel, "form": form,
        "jobs": sel.projects.all() if sel else [], "open_jobs": sel.open_jobs if sel else [],
        "recent": DriveReturn.objects.select_related("drive", "drive__agency")[:12],
        "statuses": DriveReturn.STATUS})


@requires("drives.manage")
def drive_return_status(request, pk):
    r = get_object_or_404(DriveReturn, pk=pk)
    if request.method == "POST" and request.POST.get("status") in dict(DriveReturn.STATUS):
        r.status = request.POST["status"]
        r.save(update_fields=["status"])
    return redirect("drive_returns")


@requires("projects.manage")
def drive_delete(request, pk):
    """Delete a drive entry (founders). Its folder list and send-back records go with it;
    films that were on it stay on Projects, just no longer linked to a drive."""
    d = get_object_or_404(HardDrive, pk=pk)
    if request.method != "POST":
        return redirect("drive_edit", d.pk)
    code, films = d.code, d.projects.count()
    d.delete()
    record(request, "drive.delete", code, f"{films} film(s) unlinked")
    text = f"Deleted {code}." + (f" {films} film(s) stay on Projects without a drive." if films else "")
    if request.headers.get("X-Requested-With") == "fetch":
        from django.http import JsonResponse
        return JsonResponse({"ok": True, "messages": [{"level": "success", "text": text}],
                             "remove": [f'[data-drive-cell="{pk}"]']})
    messages.success(request, text)
    return redirect("drives")
