from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from ops import views as v

urlpatterns = [
    path("login/", v.ThrottledLogin.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/", v.PasswordChange.as_view(), name="password_change"),
    path("health/", v.health, name="health"),
    path("cron/daily/", v.cron_daily, name="cron_daily"),
    path("img/<int:pk>/", v.image, name="image"),
    path("", v.home, name="home"),
    # admin (founders)
    path("dashboard/", v.dashboard, name="dashboard"),
    path("projects/", v.projects, name="projects"),
    path("projects/new/", v.project_new, name="project_new"),
    path("projects/<int:pk>/", v.project_edit, name="project_edit"),
    path("projects/<int:pk>/assign/", v.project_assign, name="project_assign"),
    path("planner/", v.planner, name="planner"),
    path("deadlines/", v.deadlines, name="deadlines"),
    path("delivered/", v.delivered, name="delivered"),
    path("reports/", v.reports, name="reports"),
    path("reports/print/", v.report_print, name="report_print"),
    path("invoices/", v.invoices, name="invoices"),
    path("invoices/new/", v.invoice_new, name="invoice_new"),
    path("invoices/<int:pk>/", v.invoice_print, name="invoice_print"),
    path("invoices/<int:pk>/update/", v.invoice_update, name="invoice_update"),
    path("team/", v.team_view, name="team"),
    path("team/new/", v.person_edit, name="person_new"),
    path("team/<int:pk>/", v.person_edit, name="person_edit"),
    path("extra-work/", v.extra_admin, name="extra_admin"),
    path("payroll/", v.payroll, name="payroll"),
    path("payroll/save/", v.payroll_save, name="payroll_save"),
    path("payslip/<int:pk>/", v.payslip_view, name="payslip"),
    path("employee-of-the-month/", v.eotm, name="eotm"),
    path("agencies/", v.agencies, name="agencies"),
    path("agencies/new/", v.agency_edit, name="agency_new"),
    path("agencies/<int:pk>/", v.agency_edit, name="agency_edit"),
    path("settings/", v.settings_view, name="settings"),
    path("settings/backup/", v.backup, name="backup"),
    path("settings/holiday/<int:pk>/delete/", v.holiday_delete, name="holiday_delete"),
    # drives
    path("drives/", v.drives, name="drives"),
    path("drives/<int:pk>/", v.drive_edit, name="drive_edit"),
    path("drives/send-back/", v.drive_returns, name="drive_returns"),
    path("drives/send-back/<int:pk>/status/", v.drive_return_status, name="drive_return_status"),
    # team
    path("pod/", v.pod, name="pod"),
    path("review/<int:pk>/", v.tl_review, name="tl_review"),
    path("today/", v.today, name="today"),
    path("my-month/", v.my_month, name="my_month"),
    path("my-jobs/", v.my_jobs, name="my_jobs"),
    path("my-jobs/<int:pk>/submit/", v.job_submit, name="job_submit"),
    path("my-jobs/<int:pk>/close/", v.close_job, name="close_job"),
    path("my-extra/", v.my_extra, name="my_extra"),
    path("my-extra/<int:pk>/respond/", v.extra_respond, name="extra_respond"),
    path("my-extra/entry/<int:pk>/delete/", v.extra_delete, name="extra_delete"),
    path("my-payslips/", v.my_payslips, name="my_payslips"),
    path("leaderboard/", v.leaderboard, name="leaderboard"),
    path("congrats/", v.congrats, name="congrats"),
    path("qc/", v.qc_queue, name="qc_queue"),
    path("qc/<int:pk>/", v.qc_review, name="qc_review"),
    # raw data back-office for founders (Django admin)
    path("settings/audit/", v.audit_log, name="audit_log"),
    path("settings/access/", v.access_matrix, name="access_matrix"),
    path("backoffice/", admin.site.urls),
]

handler403 = "ops.views.no_access"
