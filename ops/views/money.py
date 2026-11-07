"""Founder-only money screens. Nothing here is reachable by any other role."""
from collections import defaultdict
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..rbac import ADMIN, requires
from ..models import Agency, Invoice, InvoiceLine, PayrollMonth, Payslip, Profile, Project, StudioSettings
from ..services import (add_months, leave_count, month_start, next_invoice_number, parse_month, payslip_figures,
                        refresh_overdue)

User = get_user_model()
__all__ = ["invoices", "invoice_update", "invoice_new", "invoice_print", "payroll", "payroll_save"]


@requires("invoices.manage")
def invoices(request):
    refresh_overdue()
    status = request.GET.get("status", "")
    qs = Invoice.objects.select_related("agency").prefetch_related("lines")
    if status:
        qs = qs.filter(status=status)
    month = month_start(timezone.localdate())
    sums = defaultdict(lambda: defaultdict(int))
    for i in Invoice.objects.prefetch_related("lines"):
        c = i.currency
        if i.issued_on >= month and i.status != "draft":
            sums[c]["invoiced"] += i.total
        if i.received_on and i.received_on >= month:
            sums[c]["received"] += i.received
        if i.status not in ("paid", "draft"):
            sums[c]["outstanding"] += i.balance
        if i.status == "overdue":
            sums[c]["overdue"] += i.balance
    unbilled = (Project.objects.filter(status=Project.CLOSED, invoice_line__isnull=True)
                .select_related("agency").order_by("agency__name"))
    owed = defaultdict(lambda: {"amount": 0, "overdue": 0, "symbol": "₹"})
    for i in Invoice.objects.exclude(status__in=("paid", "draft")).select_related("agency").prefetch_related("lines"):
        o = owed[i.agency.name]
        o["amount"] += i.balance
        o["symbol"] = i.symbol
        if i.status == "overdue":
            o["overdue"] += i.balance
    return render(request, "money/invoices.html", {
        "invoices": qs, "status": status, "statuses": Invoice.STATUS, "inr": sums["INR"], "usd": sums["USD"],
        "unbilled": unbilled, "owed": sorted(owed.items(), key=lambda x: -x[1]["amount"])})


@requires("invoices.manage")
def invoice_update(request, pk):
    """Inline row update: payment status + amount received."""
    inv = get_object_or_404(Invoice, pk=pk)
    if request.method == "POST":
        new = request.POST.get("status", inv.status)
        try:
            received = int(request.POST.get("received", "0").replace(",", "") or 0)
        except ValueError:
            received = inv.received
        if new == "paid" and received < inv.total:
            received = inv.total
        if received != inv.received:
            inv.received_on = timezone.localdate()
        if 0 < received < inv.total and new in ("sent", "overdue"):
            new = "part"
        inv.status, inv.received = new, received
        inv.save()
        messages.success(request, f"{inv.number} updated: {inv.get_status_display()}, balance {inv.symbol}{inv.balance:,}.")
    return redirect(request.POST.get("next") or "invoices")


@requires("invoices.manage")
def invoice_new(request):
    agencies = Agency.objects.filter(active=True)
    agency = agencies.filter(pk=request.GET.get("agency") or request.POST.get("agency")).first()
    candidates = []
    if agency:
        candidates = list(agency.projects.filter(invoice_line__isnull=True).exclude(status=Project.ON_HOLD)
                          .order_by("couple", "deliverable"))
    if request.method == "POST" and agency:
        chosen = [p for p in candidates if str(p.pk) in request.POST.getlist("pid")]
        extras = [(request.POST.get(f"x_couple_{i}", "").strip(), request.POST.get(f"x_film_{i}", "").strip(),
                   request.POST.get(f"x_amount_{i}", "").replace(",", "").strip()) for i in range(3)]
        extras = [(c, f, int(a)) for c, f, a in extras if c and f and a.isdigit()]
        number = request.POST.get("number", "").strip() or next_invoice_number(agency.currency)
        if Invoice.objects.filter(number=number).exists():
            messages.error(request, f"Invoice number {number} already exists.")
        elif not chosen and not extras:
            messages.error(request, "Tick at least one film or add a line.")
        else:
            with transaction.atomic():
                s = StudioSettings.get()
                today = timezone.localdate()
                inv = Invoice.objects.create(
                    number=number, agency=agency, issued_on=today, due_on=today + timedelta(days=agency.payment_terms_days),
                    currency=agency.currency, gst_percent=s.gst_percent if request.POST.get("gst") else 0,
                    status="sent" if "send" in request.POST else "draft", notes=request.POST.get("notes", "")[:250])
                for p in chosen:
                    amt = request.POST.get(f"amount_{p.pk}", "").replace(",", "")
                    InvoiceLine.objects.create(invoice=inv, project=p, couple=p.couple, film=p.deliverable,
                                               amount=int(amt) if amt.isdigit() else p.invoice_amount)
                for c, f, a in extras:
                    InvoiceLine.objects.create(invoice=inv, couple=c, film=f, amount=a)
            messages.success(request, f"Invoice {inv.number} saved.")
            return redirect("invoice_print", pk=inv.pk)
    return render(request, "money/invoice_new.html", {
        "agencies": agencies, "agency": agency, "candidates": candidates,
        "number": next_invoice_number(agency.currency if agency else "INR"), "studio": StudioSettings.get()})


@requires("invoices.manage")
def invoice_print(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("agency").prefetch_related("lines"), pk=pk)
    rows, last = [], None
    for i, l in enumerate(inv.lines.all()):
        rows.append({"couple": l.couple if l.couple != last else "", "film": l.film, "amount": l.amount, "stripe": i % 2 == 0})
        last = l.couple
    return render(request, "print/invoice.html", {"inv": inv, "rows": rows, "studio": StudioSettings.get()})


def _people():
    return (User.objects.filter(profile__isnull=False, profile__active=True).exclude(profile__role=Profile.ADMIN)
            .exclude(profile__employment=Profile.CONTRACT)
            .select_related("profile").order_by("first_name"))


@requires("payroll.manage")
def payroll(request):
    month = parse_month(request.GET.get("m"))
    run = PayrollMonth.objects.filter(month=month).first()
    s = StudioSettings.get()
    rows = []
    if run and run.finalized:
        for slip in run.slips.select_related("user__profile"):
            rows.append({"u": slip.user, "f": {"salary": slip.salary, "leaves_taken": slip.leaves_taken,
                                               "unpaid_leaves": slip.unpaid_leaves, "leave_deduction": slip.leave_deduction,
                                               "extra_pay": slip.extra_pay, "net": slip.net},
                         "other": slip.other, "other_note": slip.other_note, "slip": slip})
    else:
        saved = {sl.user_id: sl for sl in run.slips.all()} if run else {}
        for u in _people():
            f = payslip_figures(u, month)
            sl = saved.get(u.id)
            other = sl.other if sl else 0
            f["net"] += other
            rows.append({"u": u, "f": f, "other": other, "other_note": sl.other_note if sl else "", "slip": sl})
    watch_over = [r for r in rows if r["f"]["unpaid_leaves"] > 0]
    watch_close = [r for r in rows if r["f"]["unpaid_leaves"] == 0 and r["f"]["leaves_taken"] >= s.free_leaves_per_month - 1
                   and r["f"]["leaves_taken"] > 0]
    totals = {k: sum(r["f"][k] for r in rows) for k in ("salary", "leave_deduction", "extra_pay", "net")}
    totals["other"] = sum(r["other"] for r in rows)
    totals["unpaid"] = sum(r["f"]["unpaid_leaves"] for r in rows)
    return render(request, "money/payroll.html", {
        "month": month, "prev": add_months(month, -1), "next": add_months(month, 1), "run": run, "rows": rows,
        "watch_over": watch_over, "watch_close": watch_close, "totals": totals, "s": s,
        "explain": next((r for r in rows if r["f"]["unpaid_leaves"] > 0), rows[0] if rows else None)})


@requires("payroll.manage")
def payroll_save(request):
    """Save adjustments (draft) or finalize: freezes every figure into payslips."""
    month = parse_month(request.POST.get("m"))
    run, _ = PayrollMonth.objects.get_or_create(month=month)
    if run.finalized:
        messages.error(request, "This month is already finalized.")
        return redirect(f"/payroll/?m={month:%Y-%m}")
    with transaction.atomic():
        for u in _people():
            f = payslip_figures(u, month)
            try:
                other = int(request.POST.get(f"other_{u.id}", "0").replace(",", "") or 0)
            except ValueError:
                other = 0
            Payslip.objects.update_or_create(payroll=run, user=u, defaults={
                "salary": f["salary"], "leaves_taken": f["leaves_taken"], "unpaid_leaves": f["unpaid_leaves"],
                "leave_deduction": f["leave_deduction"], "extra_pay": f["extra_pay"], "other": other,
                "other_note": request.POST.get(f"note_{u.id}", "")[:100], "net": f["net"] + other})
        if "finalize" in request.POST:
            run.finalized, run.finalized_on = True, timezone.now()
            run.save()
            messages.success(request, f"{month:%B %Y} finalized. Each person can now see their own payslip.")
        else:
            messages.success(request, "Adjustments saved.")
    return redirect(f"/payroll/?m={month:%Y-%m}")
