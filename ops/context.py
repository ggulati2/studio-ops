"""Menu and page-wide context. The menu is built from the RBAC table, so a
link only shows when the server would also allow the page."""
from .models import StudioSettings
from .rbac import ADMIN, DRIVE, EDITOR, QC, TL, perms_of, role_of
from .services import celebrated_award, trophy_counts

# (label, url name, permission needed, icon)
MENU = [
    ("Dashboard", "dashboard", "dashboard.view", "grid"),
    ("My pod", "pod", "pod.review", "users"),
    ("Today", "today", "work.log_own", "sun"),
    ("My month", "my_month", "work.log_own", "calendar"),
    ("My jobs", "my_jobs", "work.log_own", "film"),
    ("Waiting for QC", "qc_queue", "qc.review", "check"),
    ("Projects", "projects", "projects.manage", "film"),
    ("Planner", "planner", "planner.manage", "layout"),
    ("Deadlines", "deadlines", "deadlines.view", "calendar"),
    ("Delivered", "delivered", "delivered.view", "send"),
    ("Reports", "reports", "reports.manage", "file"),
    ("Invoices", "invoices", "invoices.manage", "receipt"),
    ("Hard drives", "drives", "drives.manage", "drive"),
    ("Send back", "drive_returns", "drives.manage", "truck"),
    ("Team & attendance", "team", "attendance.view_all", "users"),
    ("Extra work", "extra_admin", "extra.manage", "clock"),
    ("Extra work", "my_extra", "extra.log_own", "clock"),
    ("Payroll", "payroll", "payroll.manage", "wallet"),
    ("Employee of the month", "eotm", "eotm.manage", "trophy"),
    ("Leaderboard", "leaderboard", "leaderboard.view", "trophy"),
    ("Agencies", "agencies", "agencies.manage", "briefcase"),
    ("Settings", "settings", "settings.manage", "settings"),
]
# Founders reach these from inside other screens; keep their menu focused.
HIDE_FOR = {ADMIN: {"pod", "qc_queue", "drive_returns", "leaderboard"}}
ROLE_LABEL = {ADMIN: "Founder", TL: "Team lead", EDITOR: "Editor", QC: "Quality check", DRIVE: "Drive desk"}


def build_menu(user, path="/"):
    """[(label, href, icon, active)] — active is the item whose URL is the longest prefix of the page."""
    from django.urls import reverse
    r, perms = role_of(user), perms_of(user)
    hidden = HIDE_FOR.get(r, set())
    items = [(label, reverse(name), icon) for label, name, perm, icon in MENU if perm in perms and name not in hidden]
    best = max((h for _, h, _ in items if path == h or path.startswith(h)), key=len, default=None)
    return [(label, h, icon, h == best) for label, h, icon in items]


def nav(request):
    studio = StudioSettings.get()
    if not request.user.is_authenticated:
        return {"studio": studio}
    r = role_of(request.user)
    award = celebrated_award()
    return {
        "studio": studio,
        "nav_items": build_menu(request.user, request.path),
        "role": r,
        "role_label": ROLE_LABEL.get(r, ""),
        "is_founder": r == ADMIN,
        "my_perms": perms_of(request.user),
        "my_trophies": trophy_counts().get(request.user.id, 0) if r in (TL, EDITOR, QC) else None,
        "celebrating": bool(award and award.user_id == request.user.id),
        "current_award": award,
    }
