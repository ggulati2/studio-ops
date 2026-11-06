"""Role-based access control.

One table says what each role may do. Views declare the permission they
need with @requires("perm"); menus are built from the same table, so the
UI and the server can never disagree. Object-level rules (an editor's own
records, a team lead's pod) live in the scope_* helpers below and are
applied inside the views on every query.

Change who can do what by editing ROLE_PERMISSIONS only.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import Profile

ADMIN, TL, EDITOR, QC, DRIVE = Profile.ADMIN, Profile.TL, Profile.EDITOR, Profile.QC, Profile.DRIVE

# permission -> plain-English meaning (shown in Settings → Roles & permissions)
PERMISSIONS = {
    "dashboard.view": "See the studio dashboard",
    "projects.manage": "Create, edit and assign any project",
    "planner.manage": "Plan who works on what",
    "deadlines.view": "See every deadline",
    "delivered.view": "See delivered films and their links",
    "reports.manage": "Make team and agency status reports",
    "invoices.manage": "Create invoices and record payments",
    "payroll.manage": "See salaries, run payroll",
    "people.manage": "Add people, set roles, reset passwords",
    "attendance.view_all": "See everyone's attendance and value vs salary",
    "extra.manage": "Offer extra work and see extra pay",
    "eotm.manage": "Choose Employee of the Month",
    "agencies.manage": "Manage client details and rates",
    "settings.manage": "Change studio settings and holidays",
    "audit.view": "Read the audit log",
    "backup.download": "Download a full data backup",
    "drives.manage": "Receive and send back hard drives",
    "pod.review": "Review films from their own pod",
    "qc.review": "Pass or fail films in QC",
    "work.log_own": "Log their own day and jobs",
    "extra.log_own": "Accept and log their own extra work",
    "payslips.view_own": "See their own payslips",
    "leaderboard.view": "See the leaderboard",
}

_ADMIN_ONLY = {"dashboard.view", "projects.manage", "planner.manage", "deadlines.view", "delivered.view",
               "reports.manage", "invoices.manage", "payroll.manage", "people.manage", "attendance.view_all",
               "extra.manage", "eotm.manage", "agencies.manage", "settings.manage", "audit.view", "backup.download"}
_OWN_WORK = {"work.log_own", "extra.log_own", "payslips.view_own", "leaderboard.view"}

ROLE_PERMISSIONS = {
    ADMIN: _ADMIN_ONLY | {"drives.manage", "pod.review", "qc.review", "leaderboard.view"},
    TL: _OWN_WORK | {"pod.review"},
    EDITOR: set(_OWN_WORK),
    QC: {"qc.review", "leaderboard.view"},
    DRIVE: {"drives.manage"},
}

# Money, salaries and client data: never granted outside the admin role.
SENSITIVE = {"invoices.manage", "payroll.manage", "attendance.view_all", "agencies.manage", "backup.download",
             "audit.view", "people.manage", "extra.manage"}
assert all(not (perms & SENSITIVE) for role, perms in ROLE_PERMISSIONS.items() if role != ADMIN), \
    "A sensitive permission was given to a non-admin role"
assert set().union(*ROLE_PERMISSIONS.values()) <= set(PERMISSIONS), "Unknown permission in ROLE_PERMISSIONS"


def role_of(user):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    if user.is_superuser:
        return ADMIN
    p = getattr(user, "profile", None)
    return p.role if (p and p.active) else None


def perms_of(user):
    return ROLE_PERMISSIONS.get(role_of(user), set())


def has_perm(user, perm):
    return perm in perms_of(user)


def requires(*perms):
    """View decorator: login + every listed permission. Sets request.role."""
    unknown = set(perms) - set(PERMISSIONS)
    if unknown:
        raise ValueError(f"Unknown permission(s): {unknown}")

    def deco(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            granted = perms_of(request.user)
            if not set(perms) <= granted:
                from .audit import record
                record(request, "access.denied", request.path, f"needs {', '.join(perms)}")
                raise PermissionDenied("You don't have access to this page.")
            request.role = role_of(request.user)
            request.perms = granted
            return view(request, *args, **kwargs)
        wrapper.required_perms = perms
        return wrapper
    return deco


# ---------------------------------------------------------------- object-level scope
def can_review_in_pod(user, project):
    """A TL may review only films whose editor is in the TL's own pod (admins: any)."""
    if role_of(user) == ADMIN:
        return True
    my_pod = getattr(getattr(user, "profile", None), "pod_id", None)
    editor_pod = getattr(getattr(project.editor, "profile", None), "pod_id", None) if project.editor_id else None
    return bool(my_pod) and my_pod == editor_pod
