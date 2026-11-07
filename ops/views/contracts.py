"""Contract editors: what they've earned under their pay basis, what's been paid, what's due."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render

from .. import services as S
from ..audit import record
from ..forms import ContractPaymentForm
from ..models import ContractPayment, Profile, Project
from ..rbac import requires

__all__ = ["contractors", "contractor_detail", "contract_payment_delete", "my_payments"]
User = get_user_model()


def _contractors():
    return (User.objects.filter(profile__employment=Profile.CONTRACT).select_related("profile")
            .order_by("-profile__active", "first_name"))


@requires("payroll.manage")
def contractors(request):
    month = S.parse_month(request.GET.get("m"))
    rows = [{"u": u, "st": S.contract_statement(u, month)} for u in _contractors()]
    totals = {k: sum(r["st"][k] for r in rows) for k in ("earned", "paid", "balance")}
    return render(request, "money/contractors.html", {"rows": rows, "month": month, "totals": totals,
                                                      "prev": S.add_months(month, -1), "next": S.add_months(month, 1)})


@requires("payroll.manage")
def contractor_detail(request, pk):
    u = get_object_or_404(User, pk=pk, profile__employment=Profile.CONTRACT)
    month = S.parse_month(request.GET.get("m") or request.POST.get("m"))
    form = ContractPaymentForm(request.POST if request.POST.get("action") == "pay" else None)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "pay" and form.is_valid():
            pay = form.save(commit=False)
            pay.user, pay.created_by = u, request.user
            pay.save()
            record(request, "contract.payment", u.username, f"₹{pay.amount} on {pay.paid_on} ({pay.get_method_display()})")
            messages.success(request, f"Payment of ₹{pay.amount:,} to {u.profile.name} recorded.")
            return redirect(f"{request.path}?m={month:%Y-%m}")
        if action == "fee":
            p = get_object_or_404(Project, pk=request.POST.get("project"), editor=u)
            try:
                p.contractor_fee = max(0, int(request.POST.get("fee", "0").replace(",", "") or 0))
            except ValueError:
                messages.error(request, "Enter the fee as a number.")
                return redirect(f"{request.path}?m={month:%Y-%m}")
            p.save(update_fields=["contractor_fee"])
            record(request, "contract.fee", str(p), f"₹{p.contractor_fee}")
            messages.success(request, f"Fee for {p} set to ₹{p.contractor_fee:,}.")
            return redirect(f"{request.path}?m={month:%Y-%m}")
    st = S.contract_statement(u, month)
    open_jobs = Project.objects.filter(editor=u).exclude(status=Project.CLOSED) if u.profile.contract_basis == "project" else []
    return render(request, "money/contractor.html", {
        "u": u, "st": st, "month": month, "form": form, "open_jobs": open_jobs,
        "prev": S.add_months(month, -1), "next": S.add_months(month, 1),
        "history": ContractPayment.objects.filter(user=u)[:50]})


@requires("payroll.manage")
def contract_payment_delete(request, pk):
    pay = get_object_or_404(ContractPayment, pk=pk)
    if request.method == "POST":
        record(request, "contract.payment_deleted", pay.user.username, f"₹{pay.amount} on {pay.paid_on}")
        pay.delete()
        messages.info(request, "Payment removed.")
    return redirect("contractor_detail", pk=pay.user_id)


@requires("payslips.view_own")
def my_payments(request):
    u = request.user
    if not u.profile.is_contract:
        return redirect("my_payslips")
    month = S.parse_month(request.GET.get("m"))
    return render(request, "team/my_payments.html", {
        "st": S.contract_statement(u, month), "month": month, "prev": S.add_months(month, -1),
        "next": S.add_months(month, 1), "history": ContractPayment.objects.filter(user=u)[:24]})
