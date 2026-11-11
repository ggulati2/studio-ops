from django import template

register = template.Library()


@register.filter
def get(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.filter
def money(v):
    """Indian grouping: 1234567 -> 12,34,567."""
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return v
    sign, s = ("-" if n < 0 else ""), str(abs(n))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return sign + ",".join(groups) + "," + tail


@register.simple_tag(takes_context=True)
def qs(context, **kwargs):
    q = context["request"].GET.copy()
    for k, v in kwargs.items():
        q[k] = v
    return "?" + q.urlencode()


FLAG = {"Overdue": "bad", "Due soon": "warn", "On track": "info", "Needs an editor": "warn",
        "Delivered on time": "ok", "Delivered late": "bad", "Delivered": "ok", "Late – client approval": "gold",
        "Sent on time": "ok", "Sent late": "bad", "Sent": "ok"}
STATUS = {"in_progress": "info", "tl_review": "qc", "tl_revisions": "warn", "qc": "qc", "revisions": "warn", "qc_passed": "ok", "sent_agency": "info",
          "closed": "ok", "client_rev": "gold", "not_started": "", "on_hold": ""}
INV = {"draft": "", "sent": "info", "part": "info", "paid": "ok", "overdue": "bad"}
DRIVE = {"in_use": "info", "idle": "idle", "backup_done": "ok", "ready": "warn", "sent": "", "damaged": "bad"}


@register.filter
def flag_class(v):
    return FLAG.get(v, "")


@register.filter
def status_class(v):
    return STATUS.get(v, "")


@register.filter
def inv_class(v):
    return INV.get(v, "")


@register.filter
def drive_class(v):
    return DRIVE.get(v, "")


@register.filter
def cal_class(p):
    """Calendar chip colour for a project."""
    if p.status == "closed":
        return "st-done"
    if p.status in ("qc", "tl_review", "qc_passed"):
        return "st-qc"
    if not p.editor_id:
        return "st-noeditor"
    d = p.days_left
    if d is not None and d < 0:
        return "st-overdue"
    if d is not None and d <= 3:
        return "st-soon"
    return "st-track"


@register.filter
def hours(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return v
    return f"{v:.1f}".rstrip("0").rstrip(".") if v else "0"



@register.filter
def hue(pk):
    """A stable colour (0-359) per film, so the same film looks the same everywhere in the planner."""
    try:
        return (int(pk) * 137) % 360
    except (TypeError, ValueError):
        return 0
