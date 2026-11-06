"""Drive desk (and founders): hard drive register and send-backs.
The drive desk never sees projects' money, clients' rates or payroll."""
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from ..rbac import ADMIN, requires
from ..forms import DriveForm, DriveReturnForm
from ..models import DriveReturn, HardDrive, Project
from ..services import store_image

__all__ = ["drives", "drive_edit", "drive_returns", "drive_return_status"]


def _next_code(kind="HDD"):
    nums = [int(c.split("-")[-1]) for c in HardDrive.objects.filter(code__startswith=kind + "-").values_list("code", flat=True)
            if c.split("-")[-1].isdigit()]
    return f"{kind}-{(max(nums) + 1) if nums else 1:02d}"


def _save_photos(form, target):
    for f in form.cleaned_data.get("photos_upload") or []:
        target.photos.add(store_image(f))


@requires("drives.manage")
def drives(request):
    qs = HardDrive.objects.select_related("agency").prefetch_related("photos", "projects")
    status, q = request.GET.get("status", ""), request.GET.get("q", "").strip()
    if status:
        qs = qs.filter(status=status)
    else:
        qs = qs.exclude(status="sent")
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(contents__icontains=q) | Q(agency__name__icontains=q) | Q(serial__icontains=q))
    form = DriveForm(request.POST or None, request.FILES or None, initial={"code": _next_code()})
    if request.method == "POST" and form.is_valid():
        d = form.save()
        _save_photos(form, d)
        messages.success(request, f"{d.code} recorded. Write {d.code} on its label.")
        return redirect("drives")
    all_ = HardDrive.objects.all()
    counts = {"in_studio": all_.exclude(status="sent").count(), "in_use": all_.filter(status="in_use").count(),
              "ready": all_.filter(status="ready").count(), "damaged": all_.filter(status="damaged").count(),
              "sent": all_.filter(status="sent").count()}
    return render(request, "drives/register.html", {"drives": qs, "form": form, "counts": counts,
                                                    "status": status, "q": q, "statuses": HardDrive.STATUS})


@requires("drives.manage")
def drive_edit(request, pk):
    d = get_object_or_404(HardDrive, pk=pk)
    form = DriveForm(request.POST or None, request.FILES or None, instance=d)
    if request.method == "POST" and form.is_valid():
        form.save()
        _save_photos(form, d)
        messages.success(request, f"{d.code} saved.")
        return redirect("drives")
    return render(request, "drives/edit.html", {"d": d, "form": form,
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
