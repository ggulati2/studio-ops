"""Projects grouped as Agency → Couple → Films.

Every film belongs to a couple record (same agency + same couple name, spelled any
way: "Amit & Tanya" = "amit and tanya"). The grouped view is drawn from the same
filters as the flat list. A couple's films are loaded only when it is opened, and
everything a founder does to a couple (assign, status, move dates, add a film,
rename, merge) is saved in the background and only that couple is re-drawn."""
import difflib
from collections import Counter
from datetime import date, timedelta
from urllib.parse import urlsplit

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .. import services as S
from ..audit import record
from ..models import Couple, Project, couple_key
from ..rbac import requires
from ..services import team
from .admin import project_filter

__all__ = ["couple_block", "couple_action"]

DONE = (Project.CLOSED, Project.SENT_AGENCY)
INLINE_OPEN = 6          # a search that finds this many couples or fewer shows their films straight away
DUP_LIMIT = 400          # couples per agency above which the look-alike check is skipped


def _is_fetch(request):
    return request.headers.get("X-Requested-With") in ("fetch", "fetch-page")


def _page_qs(params):
    q = params.copy()
    for k in ("show", "qs"):
        q.pop(k, None)
    return q.urlencode()


def _back(params):
    qs = _page_qs(params)
    return reverse("projects") + ("?" + qs if qs else "")


def heal(films):
    """Films saved without going through Project.save() (imports, bulk updates) get their couple now."""
    for p in films:
        if not p.client_id and p.agency_id and p.couple:
            p.client = Couple.for_name(p.agency, p.couple)
            Project.objects.filter(pk=p.pk).update(client=p.client)


def couple_summary(films, all_films=None):
    """all_films = (count, delivered) over every film of the couple, whatever the filters show."""
    today = timezone.localdate()
    live = [p for p in films if p.status not in DONE and p.status != Project.ON_HOLD]
    nxt = min((p.deadline for p in live if p.deadline), default=None)
    total, done = all_films or (len(films), sum(1 for p in films if p.status in DONE))
    total = max(total, len(films))
    return {"n": len(films), "total": total, "hidden": total - len(films), "done": done, "open": len(live),
            "overdue": sum(1 for p in live if p.deadline and p.deadline < today and p.status != Project.CLIENT_REV),
            "next": nxt, "next_days": (nxt - today).days if nxt else None,
            "unassigned": sum(1 for p in live if not p.editor_id),
            "editors": sorted({p.editor.profile.name for p in live if p.editor_id}),
            "drives": sorted({p.drive.code for p in films if p.drive_id}),
            "kinds": [p.deliverable for p in films], "value": sum(p.invoice_amount for p in films),
            "pct": round(done / total * 100) if total else 0,
            "main_editor": (Counter(p.editor_id for p in live if p.editor_id).most_common(1) or [(None, 0)])[0][0]}


def _same_words(k):
    return " ".join(sorted(k.split()))


def look_alikes(couples):
    """Pairs of couples under one agency whose names look like the same people."""
    if len(couples) > DUP_LIMIT:
        return []
    items = [(g["c"], g["c"].key, _same_words(g["c"].key), g["s"]["total"]) for g in couples]
    out = []
    for i, (a, ka, sa, na) in enumerate(items):
        for b, kb, sb, nb in items[i + 1:]:
            m = difflib.SequenceMatcher(None, ka, kb)
            if sa == sb or (m.real_quick_ratio() >= .86 and m.quick_ratio() >= .86 and m.ratio() >= .86):
                keep, drop = (a, b) if na >= nb else (b, a)
                out.append((keep, drop))
                if len(out) >= 6:
                    return out
    return out


def build_groups(films, totals=None, sort=""):
    agencies = {}
    for p in films:
        a = agencies.setdefault(p.agency_id, {"a": p.agency, "couples": {}})
        g = a["couples"].get(p.client_id)
        if not g:
            p.client.agency = p.agency          # shared object: no extra query per couple
            g = a["couples"][p.client_id] = {"c": p.client, "films": []}
        g["films"].append(p)
    out = []
    for a in agencies.values():
        couples = list(a.pop("couples").values())
        for g in couples:
            g["s"] = couple_summary(g["films"], (totals or {}).get(g["c"].pk))
        if sort == "couple":
            couples.sort(key=lambda g: g["c"].name.lower())
        elif sort == "newest":
            couples.sort(key=lambda g: -g["c"].pk)
        else:   # what needs attention first: late, then by next deadline; finished couples last
            couples.sort(key=lambda g: (g["s"]["open"] == 0, g["s"]["overdue"] == 0, g["s"]["next"] or date.max,
                                        g["c"].name.lower()))
        a["list"] = couples
        a["s"] = {k: sum(g["s"][k] for g in couples) for k in ("n", "open", "overdue", "unassigned", "value", "done")}
        a["s"]["couples"] = len(couples)
        out.append(a)
    out.sort(key=lambda a: a["a"].name.lower())
    return out


def _totals(ids):
    rows = (Project.objects.filter(client_id__in=ids).order_by().values("client_id")
            .annotate(n=Count("id"), done=Count("id", filter=Q(status__in=DONE))))
    return {r["client_id"]: (r["n"], r["done"]) for r in rows}


def _body_ctx(params):
    return {"editors": team(), "statuses": Project.STATUSES, "deliverables": Project.DELIVERABLES,
            "back": _back(params), "page_qs": _page_qs(params)}


def grouped_context(request, qs, meta):
    films = list(qs.select_related("client"))
    heal(films)
    groups = build_groups(films, _totals({p.client_id for p in films}), meta["f"]["sort"])
    n_couples = sum(len(a["list"]) for a in groups)
    expand = len(groups) == 1 or bool(meta["q"]) or bool(meta["f"]["agency"])
    inline = bool(meta["q"]) and n_couples <= INLINE_OPEN
    if inline:
        S.plan_others(films)
    for a in groups:
        a["open"] = expand
        a["dups"] = look_alikes(a["list"]) if not meta["q"] else []
        for g in a["list"]:
            g["open"] = inline
    ctx = _body_ctx(request.GET)
    ctx.update({"groups": groups, "total": len(films), "n_couples": n_couples,
                "total_value": sum(p.invoice_amount for p in films)})
    return ctx


def _couple_group(couple, params):
    qs, _ = project_filter(params)
    films = S.plan_others(qs.filter(client=couple).select_related("client"))
    return {"c": couple, "films": films, "s": couple_summary(films, _totals([couple.pk]).get(couple.pk))}


def _agency_group(agency_id, params):
    qs, meta = project_filter(params)
    films = list(qs.filter(agency_id=agency_id).select_related("client"))
    groups = build_groups(films, _totals({p.client_id for p in films}), meta["f"]["sort"])
    return groups[0] if groups else None


def render_couple(request, couple, params, open_=True):
    g = _couple_group(couple, params)
    g["open"] = open_
    ctx = _body_ctx(params)
    ctx["g"] = g
    return render_to_string("founder/_couple.html", ctx, request=request)


def summary_swaps(request, p, nxt):
    """After one film changes inside a couple block: fresh couple + agency summary lines."""
    params = QueryDict(urlsplit(nxt).query)
    a = _agency_group(p.agency_id, params)
    out = []
    if a:
        out.append({"sel": f'[data-agency-sum="{p.agency_id}"]', "html": render_to_string(
            "founder/_agency_summary.html", {"a": a}, request=request)})
    g = next((g for g in (a["list"] if a else []) if g["c"].pk == p.client_id), None) or _couple_group(p.client, params)
    out.append({"sel": f'[data-couple-sum="{p.client_id}"]', "html": render_to_string(
        "founder/_couple_summary.html", {"g": g}, request=request)})
    return out


@requires("projects.manage")
def couple_block(request, pk):
    """One couple, opened: its films (same filters as the page) and the couple actions."""
    couple = get_object_or_404(Couple.objects.select_related("agency"), pk=pk)
    return HttpResponse(render_couple(request, couple, request.GET, open_=True))


def _date(v):
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return False


def _merge(src, dst):
    """Move every film of src under dst and remove src."""
    n = src.films.update(client=dst, couple=dst.name)
    changed = []
    if not dst.wedding_date and src.wedding_date:
        dst.wedding_date = src.wedding_date
        changed.append("wedding_date")
    if src.notes and src.notes not in dst.notes:
        dst.notes = (dst.notes + " · " + src.notes).strip(" ·")[:300]
        changed.append("notes")
    if changed:
        dst.save(update_fields=changed + ["key", "name"])
    src.delete()
    return n


@requires("projects.manage")
def couple_action(request, pk):
    couple = get_object_or_404(Couple.objects.select_related("agency"), pk=pk)
    params = QueryDict(request.POST.get("qs", ""))
    back = _back(params)
    if request.method != "POST":
        return redirect(back)
    P = request.POST
    act = P.get("op", "")  # not "action": a field with that name hides form.action in the browser
    say, refresh = [], False
    ids = [int(x) for x in P.getlist("ids") if x.isdigit()]
    films = couple.films.select_related("agency", "editor__profile")
    films = list(films.filter(pk__in=ids) if ids else films)
    live = [p for p in films if p.status not in DONE and p.status != Project.ON_HOLD]

    if act == "assign":
        raw = P.get("editor", "")
        ed = team().filter(pk=raw).first() if raw.isdigit() else None
        if not ed:
            say.append(("error", "Pick an editor."))
        elif not live:
            say.append(("info", f"{couple} has no open films to assign."))
        else:
            made = short = moved = 0
            with transaction.atomic():
                for p in live:
                    if p.editor_id != ed.pk:
                        p.editor = ed
                        p.save(update_fields=["editor"])
                        moved += 1
                        m, sh = S.auto_plan(p)
                        made, short = made + m, short + sh
            record(request, "couple.assign", str(couple), f"{moved} film(s) -> {ed.profile.name}")
            say.append(("success", f"{couple}: {moved} film(s) given to {ed.profile.name}."
                        + (f" {made} day(s) added to the Planner." if made else "")
                        if moved else f"{couple}: every open film is already with {ed.profile.name}."))
            if short:
                say.append(("warning", f"{short} day(s) of work didn't fit before the deadlines. Check the Planner."))

    elif act == "status":
        from .admin import _sent_date, apply_status
        new = P.get("status", "")
        sent_on = _sent_date(P.get("sent_on")) if new == Project.SENT_AGENCY else None
        if new not in dict(Project.STATUSES):
            say.append(("error", "Pick a status."))
        elif sent_on is False:
            say.append(("error", "The sent date can't be in the future."))
        else:
            n, late = 0, []
            with transaction.atomic():
                for p in films:
                    if p.status != new:
                        apply_status(p, new, sent_on)
                        n += 1
                        if new == Project.SENT_AGENCY and p.delivered_late:
                            late.append(p.deliverable)
            label = dict(Project.STATUSES)[new]
            when = f" on {sent_on or timezone.localdate():%-d %b}" if new == Project.SENT_AGENCY else ""
            record(request, "couple.status", str(couple), f"{n} film(s) -> {label}{when}")
            say.append(("success", f"{couple}: {n} film(s) set to {label}{when}." if n else f"{couple}: already {label}."))
            if late:
                say.append(("warning", f"Sent after the agency's deadline: {', '.join(late)}."))

    elif act == "shift":
        try:
            days = int(P.get("days", "0"))
        except ValueError:
            days = 0
        which = P.get("which", "both")
        if not days or abs(days) > 365:
            say.append(("error", "Enter how many days to move the deadlines (for example 3 or -2)."))
        else:
            moved, skipped, made = 0, [], 0
            with transaction.atomic():
                for p in live:
                    ed = p.deadline + timedelta(days=days) if p.deadline and which in ("both", "editor") else p.deadline
                    ad = (p.agency_deadline + timedelta(days=days) if p.agency_deadline and which in ("both", "agency")
                          else p.agency_deadline)
                    if (ed, ad) == (p.deadline, p.agency_deadline):
                        continue
                    if ed and ad and ed > ad:
                        skipped.append(p.deliverable)
                        continue
                    replan = ed != p.deadline and p.editor_id
                    p.deadline, p.agency_deadline = ed, ad
                    p.save(update_fields=["deadline", "agency_deadline"])
                    moved += 1
                    if replan:
                        made += S.auto_plan(p)[0]
            what = {"both": "deadlines", "editor": "editor's deadlines", "agency": "agency's deadlines"}.get(which, "deadlines")
            record(request, "couple.shift", str(couple), f"{what} {days:+d} day(s) on {moved} film(s)")
            say.append(("success", f"{couple}: {what} moved {abs(days)} day(s) {'later' if days > 0 else 'earlier'} "
                                   f"on {moved} film(s)." + (f" Planner updated ({made} day(s))." if made else "")))
            if skipped:
                say.append(("warning", f"Not moved: {', '.join(skipped)} (the editor's deadline would land after the agency's)."))

    elif act == "add":
        d = P.get("deliverable", "")
        ed, ad = _date(P.get("deadline")), _date(P.get("agency_deadline"))
        raw = P.get("editor", "")
        editor = team().filter(pk=raw).first() if raw.isdigit() else None
        amount = (P.get("invoice_amount") or "").replace(",", "").strip()
        if d not in Project.DELIVERABLES:
            say.append(("error", "Pick which film to add."))
        elif ed is False or ad is False:
            say.append(("error", "One of the dates isn't valid."))
        elif ed and ad and ed > ad:
            say.append(("error", "The editor's deadline can't be after the agency's."))
        elif amount and (not amount.isdigit() or int(amount) > 100_000_000):
            say.append(("error", "The invoice amount must be a whole number."))
        else:
            rate = couple.agency.rates.filter(deliverable=d).values_list("amount", flat=True).first() or 0
            p = Project(couple=couple.name, client=couple, agency=couple.agency, deliverable=d, editor=editor,
                        deadline=ed or None, agency_deadline=ad or None,
                        invoice_amount=int(amount) if amount else rate)
            p.save()
            record(request, "project.new", str(p), f"added to {couple}")
            made, short = S.auto_plan(p) if editor else (0, 0)
            say.append(("success", f"Added {d} for {couple}" + (f", given to {editor.profile.name}" if editor else "")
                        + "." + (f" {made} day(s) planned." if made else "")))
            if short:
                say.append(("warning", f"{short} day(s) of work didn't fit before the deadline. Check the Planner."))

    elif act == "edit":
        name = " ".join((P.get("name") or "").split())[:120]
        wd = _date(P.get("wedding_date"))
        notes = (P.get("notes") or "").strip()[:300]
        if not name:
            say.append(("error", "The couple needs a name."))
        elif wd is False:
            say.append(("error", "The wedding date isn't valid."))
        else:
            other = Couple.objects.filter(agency=couple.agency, key=couple_key(name)).exclude(pk=couple.pk).first()
            with transaction.atomic():
                if other:
                    couple.wedding_date, couple.notes = wd or couple.wedding_date, notes
                    n = _merge(couple, other)
                    record(request, "couple.merge", str(other), f"{couple.name} merged in ({n} film(s))")
                    say.append(("success", f"{other} already existed under {couple.agency}, so the {n} film(s) "
                                           f"were moved there."))
                    refresh = True
                else:
                    old = couple.name
                    couple.name, couple.wedding_date, couple.notes = name, wd or None, notes
                    couple.save()
                    if couple.name != old:
                        couple.films.update(couple=couple.name)
                        record(request, "couple.rename", couple.name, f"{old} -> {couple.name}")
                        refresh = True     # the agency's order and look-alike hints may change
                    say.append(("success", f"Saved {couple}."))

    elif act == "merge":
        dst = Couple.objects.filter(pk=P.get("into") or 0, agency=couple.agency).exclude(pk=couple.pk).first()
        if not dst:
            say.append(("error", "Couldn't find the couple to merge into."))
        else:
            with transaction.atomic():
                name = couple.name
                n = _merge(couple, dst)
            record(request, "couple.merge", str(dst), f"{name} merged in ({n} film(s))")
            say.append(("success", f"Merged {name} into {dst}: {n} film(s) moved."))
            refresh = True
    else:
        say.append(("error", "Unknown action."))

    if _is_fetch(request):
        bad = any(level == "error" for level, _ in say)
        swap = []
        if not refresh and not bad:
            swap.append({"sel": f'[data-couple="{couple.pk}"]', "mode": "outer",
                         "html": render_couple(request, couple, params, open_=True)})
            a = _agency_group(couple.agency_id, params)
            if a:
                swap.append({"sel": f'[data-agency-sum="{couple.agency_id}"]', "html": render_to_string(
                    "founder/_agency_summary.html", {"a": a}, request=request)})
        return JsonResponse({"ok": not bad, "messages": [{"level": l, "text": t} for l, t in say],
                             "swap": swap, "refresh": refresh})
    for level, text in say:
        getattr(messages, level)(request, text)
    return redirect(back)
