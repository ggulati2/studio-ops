"""Menu and page-wide context. The menu is built from the RBAC table, so a
link only shows when the server would also allow the page."""
from .models import StudioSettings
from .rbac import ADMIN, DRIVE, EDITOR, INTERN, QC, TL, perms_of, role_of
from .services import celebrated_award, trophy_counts

# (label, url name, permission needed, icon)
MENU = [
    ("Dashboard", "dashboard", "dashboard.view", "grid"),
    ("My pod", "pod", "pod.review", "users"),
    ("Today", "today", "work.log_own", "sun"),
    ("My month", "my_month", "work.log_own", "calendar"),
    ("My leave", "my_leave", "work.log_own", "plane"),
    ("My jobs", "my_jobs", "work.log_own", "film"),
    ("QC dashboard", "qc_queue", "qc.review", "check"),
    ("Projects", "projects", "projects.manage", "film"),
    ("Planner", "planner", "planner.manage", "layout"),
    ("Delays", "delays", "projects.manage", "alert"),
    ("Deadlines", "deadlines", "deadlines.view", "calendar"),
    ("Delivered", "delivered", "delivered.view", "send"),
    ("Reports", "reports", "reports.manage", "file"),
    ("Invoices", "invoices", "invoices.manage", "receipt"),
    ("Hard drives", "drives", "drives.manage", "drive"),
    ("Send back", "drive_returns", "drives.manage", "truck"),
    ("Leave requests", "leave_requests", "leave.approve", "plane"),
    ("Team & attendance", "team", "attendance.view_all", "users"),
    ("Extra work", "extra_admin", "extra.manage", "clock"),
    ("Extra work", "my_extra", "extra.log_own", "clock"),
    ("Payroll", "payroll", "payroll.manage", "wallet"),
    ("Contract editors", "contractors", "payroll.manage", "briefcase"),
    ("Trailblazer of the month", "eotm", "eotm.manage", "trophy"),
    ("Leaderboard", "leaderboard", "leaderboard.view", "trophy"),
    ("Agencies", "agencies", "agencies.manage", "briefcase"),
    ("Settings", "settings", "settings.manage", "settings"),
]
# Founders reach these from inside other screens; keep their menu focused.
HIDE_FOR = {ADMIN: {"pod", "qc_queue", "drive_returns", "leaderboard"}}
ROLE_LABEL = {ADMIN: "Founder", TL: "Team lead", EDITOR: "Editor", INTERN: "Intern", QC: "Quality check", DRIVE: "Drive desk"}


def build_menu(user, path="/"):
    """[(label, href, icon, active)] — active is the item whose URL is the longest prefix of the page."""
    from django.urls import reverse
    r, perms = role_of(user), perms_of(user)
    hidden = HIDE_FOR.get(r, set())
    items = [(label, reverse(name), icon) for label, name, perm, icon in MENU if perm in perms and name not in hidden]
    best = max((h for _, h, _ in items if path == h or path.startswith(h)), key=len, default=None)
    return [(label, h, icon, h == best) for label, h, icon in items]


def _pending_delays(r):
    if r != ADMIN:
        return 0
    from .models import DelayReport
    return DelayReport.objects.filter(status=DelayReport.PENDING).count()


def _pending_leave(r):
    if r != ADMIN:
        return 0
    from .models import LeaveRequest
    return LeaveRequest.objects.filter(status=LeaveRequest.PENDING).count()


def _badges():
    """Menu counts and trophy data change rarely but show on every page: one cache
    read instead of four queries. Dropped on any change (ops/signals.py)."""
    from django.core.cache import cache
    from .models import DelayReport, LeaveRequest
    b = cache.get("nav_badges")
    if b is None:
        award = celebrated_award()
        b = {"delays": DelayReport.objects.filter(status=DelayReport.PENDING).count(),
             "leave": LeaveRequest.objects.filter(status=LeaveRequest.PENDING).count(),
             "trophies": dict(trophy_counts()), "award": award,
             "award_month": award.month if award else None}
        cache.set("nav_badges", b, 60 * 10)
    from django.utils import timezone
    from .services import add_months, month_start
    if b["award_month"] and b["award_month"] != add_months(month_start(timezone.localdate()), -1):
        cache.delete("nav_badges")     # a new month started: the celebrated award changes
        return _badges()
    return b


def nav(request):
    studio = StudioSettings.get()
    if not request.user.is_authenticated:
        return {"studio": studio}
    r = role_of(request.user)
    b = _badges()
    award = b["award"]
    return {
        "studio": studio,
        "nav_items": build_menu(request.user, request.path),
        "role": r,
        "role_label": ROLE_LABEL.get(r, ""),
        "is_founder": r == ADMIN,
        "my_perms": perms_of(request.user),
        "is_contract": bool(getattr(getattr(request.user, "profile", None), "is_contract", False)),
        "pending_delays": b["delays"] if r == ADMIN else 0,
        "pending_leave": b["leave"] if r == ADMIN else 0,
        "my_trophies": b["trophies"].get(request.user.id, 0) if r in (TL, EDITOR, INTERN, QC) else None,
        "celebrating": bool(award and award.user_id == request.user.id),
        "current_award": award,
    }
