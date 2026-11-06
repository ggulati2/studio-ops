from .access import ADMIN, DRIVE, EDITOR, QC, TL
from .models import StudioSettings
from .services import celebrated_award, role_of, trophy_counts

NAV = {
    ADMIN: [("Dashboard", "home"), ("Projects", "projects"), ("Planner", "planner"), ("Deadlines", "deadlines"),
            ("Delivered", "delivered"), ("Reports", "reports"), ("Invoices", "invoices"), ("Hard drives", "drives"),
            ("Team & attendance", "team"), ("Extra work", "extra_admin"), ("Payroll", "payroll"),
            ("Employee of the month", "eotm"), ("Agencies", "agencies"), ("Settings", "settings")],
    TL: [("My pod", "pod"), ("Today", "today"), ("My month", "my_month"), ("My jobs", "my_jobs"),
         ("Extra work", "my_extra"), ("Leaderboard", "leaderboard")],
    EDITOR: [("Today", "today"), ("My month", "my_month"), ("My jobs", "my_jobs"),
             ("Extra work", "my_extra"), ("Leaderboard", "leaderboard")],
    QC: [("Waiting for QC", "qc_queue"), ("Leaderboard", "leaderboard")],
    DRIVE: [("Drive register", "drives"), ("Send back", "drive_returns")],
}
ROLE_LABEL = {ADMIN: "Admin · Studio Ops", TL: "Team lead", EDITOR: "Editor", QC: "Quality check", DRIVE: "Drive desk"}


def nav(request):
    if not request.user.is_authenticated:
        return {"studio": StudioSettings.get()}
    r = role_of(request.user)
    award = celebrated_award()
    celebrating = bool(award and award.user_id == request.user.id)
    return {
        "studio": StudioSettings.get(),
        "nav_items": NAV.get(r, []),
        "role": r,
        "role_label": ROLE_LABEL.get(r, ""),
        "my_trophies": trophy_counts().get(request.user.id, 0) if r in (TL, EDITOR, QC) else None,
        "celebrating": celebrating,
        "current_award": award,
    }
