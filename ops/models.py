"""Data model for Red Wed Films Studio Ops.

Design notes
- One Django app keeps deployment simple; models are grouped by area below.
- Money is stored as integer rupees/dollars (whole units) — studio invoices
  never use paise, and integers avoid float rounding.
- Business rules (hour splitting, leave deductions, value per editor) live
  in services.py, not in views, so they are testable in one place.
"""
from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

User = settings.AUTH_USER_MODEL


# --------------------------------------------------------------------------
# Studio-wide settings and people
# --------------------------------------------------------------------------
class StudioSettings(models.Model):
    """Single row of studio-wide configuration (always id=1)."""
    brand_name = models.CharField(max_length=80, default="Red Wed Films")
    legal_name = models.CharField(max_length=120, default="Rigger Films LLP")
    address = models.TextField(default="1501, Zenith, Marathon Nexzone,\nPanvel, Raigad, Navi Mumbai,\nMaharashtra, 410206")
    phone = models.CharField(max_length=80, blank=True, default="9892964561 / 9884496942")
    email = models.EmailField(blank=True)
    pan = models.CharField(max_length=20, blank=True)
    bank_account_name = models.CharField(max_length=120, blank=True, default="Rigger Films LLP")
    bank_account_no = models.CharField(max_length=40, blank=True)
    bank_ifsc = models.CharField(max_length=20, blank=True)
    bank_name = models.CharField(max_length=80, blank=True)
    upi_id = models.CharField(max_length=80, blank=True)
    gst_percent = models.PositiveSmallIntegerField(default=0, help_text="0 = no GST line on invoices")
    free_leaves_per_month = models.DecimalField(max_digits=4, decimal_places=1, default=2)
    day_pay_basis = models.CharField(
        max_length=10, default="calendar",
        choices=[("calendar", "Salary ÷ days in month"), ("26", "Salary ÷ 26 working days")])
    shift_start = models.TimeField(default="10:00")
    late_after_minutes = models.PositiveSmallIntegerField(default=15)
    hours_per_day = models.DecimalField(max_digits=4, decimal_places=1, default=8)
    working_weekdays = models.CharField(max_length=20, default="0,1,2,3,4,5",
                                        help_text="Mon=0 … Sun=6, comma separated")
    eotm_voucher_amount = models.PositiveIntegerField(default=1000)
    report_buffer_days = models.PositiveSmallIntegerField(default=3)

    class Meta:
        verbose_name_plural = "Studio settings"

    def __str__(self):
        return "Studio settings"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def weekdays(self):
        return {int(x) for x in self.working_weekdays.split(",") if x.strip().isdigit()}


class Pod(models.Model):
    name = models.CharField(max_length=40, unique=True)
    lead = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="led_pods")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Profile(models.Model):
    ADMIN, TL, EDITOR, QC, DRIVE = "admin", "tl", "editor", "qc", "drive"
    ROLES = [(ADMIN, "Admin (founder)"), (TL, "Team lead"), (EDITOR, "Editor"),
             (QC, "QC"), (DRIVE, "Drive desk")]
    COLORS = ["#7C3AED", "#0F766E", "#B45309", "#1D4ED8", "#BE185D", "#0E7490", "#4D7C0F", "#9333EA"]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=10, choices=ROLES, default=EDITOR)
    pod = models.ForeignKey(Pod, null=True, blank=True, on_delete=models.SET_NULL, related_name="members")
    phone = models.CharField(max_length=30, blank=True)
    monthly_salary = models.PositiveIntegerField(default=0)
    joined_on = models.DateField(null=True, blank=True)
    color = models.CharField(max_length=9, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    @property
    def name(self):
        return self.user.get_full_name() or self.user.username

    @property
    def initial(self):
        return (self.name or "?")[:1].upper()

    def save(self, *args, **kwargs):
        if not self.color:
            self.color = self.COLORS[(self.user_id or 0) % len(self.COLORS)]
        super().save(*args, **kwargs)

    @property
    def does_editing(self):
        return self.role in (self.TL, self.EDITOR)


class Holiday(models.Model):
    FULL, HALF, OPTIONAL = "full", "half", "optional"
    KINDS = [(FULL, "Full day"), (HALF, "Half day"), (OPTIONAL, "Optional")]
    date = models.DateField(unique=True)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=10, choices=KINDS, default=FULL)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"{self.date:%d %b %Y} · {self.name}"


# --------------------------------------------------------------------------
# Clients and drives
# --------------------------------------------------------------------------
class Agency(models.Model):
    name = models.CharField(max_length=120, unique=True)
    city = models.CharField(max_length=80, blank=True)
    contact_person = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    invoice_emails = models.CharField(max_length=250, blank=True, help_text="Comma separated")
    billing_address = models.TextField(blank=True)
    currency = models.CharField(max_length=3, choices=[("INR", "INR ₹"), ("USD", "USD $")], default="INR")
    payment_terms_days = models.PositiveSmallIntegerField(default=15)
    gstin = models.CharField(max_length=20, blank=True)
    pan = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "agencies"

    def __str__(self):
        return self.name

    @property
    def symbol(self):
        return "$" if self.currency == "USD" else "₹"

    @property
    def missing_fields(self):
        return [label for field, label in (("billing_address", "billing address"),
                                           ("invoice_emails", "invoice email"))
                if not getattr(self, field)]


class AgencyRate(models.Model):
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name="rates")
    deliverable = models.CharField(max_length=30)
    amount = models.PositiveIntegerField()

    class Meta:
        unique_together = ("agency", "deliverable")


class StoredImage(models.Model):
    """Small JPEG kept in the database so free hosts need no disk."""
    data = models.BinaryField()
    content_type = models.CharField(max_length=40, default="image/jpeg")
    created = models.DateTimeField(auto_now_add=True)


class HardDrive(models.Model):
    STATUS = [("in_use", "In use"), ("backup_done", "Backup done"), ("ready", "Ready to send back"),
              ("sent", "Sent back"), ("damaged", "Damaged / issue")]
    code = models.CharField("Drive ID", max_length=20, unique=True)
    kind = models.CharField(max_length=12, choices=[("HDD", "HDD"), ("SSD", "SSD"), ("Card", "Memory card")], default="HDD")
    agency = models.ForeignKey(Agency, null=True, blank=True, on_delete=models.SET_NULL, related_name="drives")
    studio_owned = models.BooleanField("Studio's own drive", default=False)
    contents = models.CharField(max_length=200, blank=True)
    brand_model = models.CharField(max_length=80, blank=True)
    serial = models.CharField(max_length=80, blank=True)
    capacity_tb = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    data_tb = models.DecimalField("Data on it (TB)", max_digits=5, decimal_places=1, null=True, blank=True)
    received_on = models.DateField(default=date.today)
    received_via = models.CharField(max_length=20, choices=[("courier", "Courier"), ("hand", "Hand delivery")], default="courier")
    tracking = models.CharField(max_length=80, blank=True)
    location = models.CharField("Kept at", max_length=80, blank=True)
    damaged_on_arrival = models.BooleanField(default=False)
    status = models.CharField(max_length=12, choices=STATUS, default="in_use")
    photos = models.ManyToManyField(StoredImage, blank=True, related_name="drives")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code

    @property
    def open_jobs(self):
        return self.projects.exclude(status=Project.CLOSED)


class DriveReturn(models.Model):
    METHODS = [("courier", "Courier"), ("hand", "Hand delivery"), ("pickup", "Agency picks up")]
    STATUS = [("transit", "In transit"), ("delivered", "Delivered"), ("confirmed", "Agency confirmed")]
    drive = models.ForeignKey(HardDrive, on_delete=models.CASCADE, related_name="returns")
    sent_on = models.DateField(default=date.today)
    sent_to = models.CharField(max_length=120)
    method = models.CharField(max_length=10, choices=METHODS, default="courier")
    courier = models.CharField(max_length=60, blank=True)
    tracking = models.CharField(max_length=80, blank=True)
    collected_by = models.CharField(max_length=80, blank=True)
    condition_ok = models.BooleanField("Same condition as received", default=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="transit")
    photos = models.ManyToManyField(StoredImage, blank=True)
    recorded_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-sent_on"]


# --------------------------------------------------------------------------
# Projects (one row = one film / deliverable)
# --------------------------------------------------------------------------
class Project(models.Model):
    NOT_STARTED, IN_PROGRESS, TL_REVIEW, QC, REVISIONS, QC_PASSED, CLOSED, ON_HOLD = (
        "not_started", "in_progress", "tl_review", "qc", "revisions", "qc_passed", "closed", "on_hold")
    STATUSES = [(NOT_STARTED, "Not started"), (IN_PROGRESS, "In progress"), (TL_REVIEW, "TL review"),
                (QC, "In QC"), (REVISIONS, "Revisions / QC fixes"), (QC_PASSED, "QC passed"),
                (CLOSED, "Delivered"), (ON_HOLD, "On hold")]
    DELIVERABLES = ["Teaser", "Highlight", "Trailer", "Full Film", "Traditional Film", "Reels",
                    "Same Day Edit", "Event Video", "Ad / Corporate", "Cleanup Shots", "Other"]
    TIERS = [(1, "Tier 1 – Simple"), (2, "Tier 2 – Cinematic"), (3, "Tier 3 – Premium")]
    CLIENT_STAGES = ["Scheduled", "Editing", "Final checks", "Changes in progress", "Delivered"]

    couple = models.CharField("Couple / project", max_length=120)
    deliverable = models.CharField(max_length=30, choices=[(d, d) for d in DELIVERABLES])
    agency = models.ForeignKey(Agency, on_delete=models.PROTECT, related_name="projects")
    tier = models.PositiveSmallIntegerField(choices=TIERS, default=2)
    editor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="projects")
    received_on = models.DateField(default=date.today)
    deadline = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=NOT_STARTED)
    invoice_amount = models.PositiveIntegerField(default=0, help_text="In the agency's currency")
    drive = models.ForeignKey(HardDrive, null=True, blank=True, on_delete=models.SET_NULL, related_name="projects")
    brief = models.TextField(blank=True)
    cut_link = models.URLField("Final film / review link", blank=True)
    project_file_link = models.URLField(blank=True)
    final_mp4_link = models.URLField(blank=True)
    closed_on = models.DateField(null=True, blank=True)
    # Extra (freelance) work done outside office hours by a full-timer
    is_extra = models.BooleanField("Given as extra work", default=False)
    extra_basis = models.CharField(max_length=6, choices=[("fixed", "Fixed per job"), ("hourly", "Per hour")], default="fixed")
    extra_rate = models.PositiveIntegerField("Extra pay (₹ fixed or ₹/hr)", default=0)
    extra_accepted = models.BooleanField(null=True, blank=True)
    # What the client sees in status reports (overrides; blank = derived)
    client_stage = models.CharField(max_length=30, blank=True, choices=[(s, s) for s in CLIENT_STAGES])
    client_date = models.DateField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["deadline", "couple"]

    def __str__(self):
        return f"{self.couple} – {self.deliverable}"

    @property
    def is_open(self):
        return self.status not in (self.CLOSED, self.ON_HOLD)

    @property
    def days_left(self):
        if not self.deadline or not self.is_open:
            return None
        return (self.deadline - timezone.localdate()).days

    @property
    def flag(self):
        d = self.days_left
        if self.status == self.CLOSED:
            if self.closed_on and self.deadline:
                return "Delivered on time" if self.closed_on <= self.deadline else "Delivered late"
            return "Delivered"
        if not self.editor_id and self.is_open:
            return "Needs an editor"
        if d is None:
            return ""
        if d < 0:
            return "Overdue"
        if d <= 3:
            return "Due soon"
        return "On track"


class TaskStage(models.TextChoices):
    SYNC = "Ingest & Sync"
    SELECTS = "Selects"
    ASSEMBLY = "Assembly"
    ROUGH = "Rough Cut"
    EDIT = "Edit"
    COLOR = "Color"
    SOUND = "Sound"
    CLIENT = "Client Changes"
    REVISIONS = "Revisions"
    QCFIX = "QC Fixes"
    EXPORT = "Export & Upload"
    OTHER = "Other"


# --------------------------------------------------------------------------
# Daily logging, attendance, planning
# --------------------------------------------------------------------------
class DayLog(models.Model):
    OFFICE, WFH, HALF, LEAVE, HOLIDAY, OFF, DOWNTIME = "office", "wfh", "half", "leave", "holiday", "off", "downtime"
    DAY_TYPES = [(OFFICE, "Office"), (WFH, "WFH"), (HALF, "Half day"), (LEAVE, "Leave"),
                 (HOLIDAY, "Holiday"), (OFF, "Weekly off"), (DOWNTIME, "Downtime")]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="daylogs")
    date = models.DateField()
    day_type = models.CharField(max_length=10, choices=DAY_TYPES, default=OFFICE)
    time_in = models.TimeField(null=True, blank=True)
    time_out = models.TimeField(null=True, blank=True)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        unique_together = ("user", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"{self.user} {self.date}"

    @property
    def hours(self):
        if not (self.time_in and self.time_out):
            return 0.0
        start = datetime.combine(self.date, self.time_in)
        end = datetime.combine(self.date, self.time_out)
        if end <= start:  # worked past midnight
            end += timedelta(days=1)
        return round((end - start).total_seconds() / 3600, 2)

    @property
    def is_late(self):
        if not self.time_in or self.day_type not in (self.OFFICE, self.HALF):
            return False
        s = StudioSettings.get()
        limit = (datetime.combine(self.date, s.shift_start) + timedelta(minutes=s.late_after_minutes)).time()
        return self.time_in > limit


class TaskEntry(models.Model):
    """A job worked on during a logged day. Hours optional: blanks share
    whatever is left of the day's in/out hours (see services.split_hours)."""
    daylog = models.ForeignKey(DayLog, on_delete=models.CASCADE, related_name="tasks")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="task_entries")
    stage = models.CharField(max_length=20, choices=TaskStage.choices, default=TaskStage.EDIT)
    hours = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)


class ExtraEntry(models.Model):
    """Hours a full-timer logged on extra (freelance) work outside office hours."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="extra_entries")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="extra_entries")
    date = models.DateField(default=date.today)
    start = models.TimeField()
    end = models.TimeField()
    stage = models.CharField(max_length=20, choices=TaskStage.choices, default=TaskStage.EDIT)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-date", "-start"]

    @property
    def hours(self):
        a = datetime.combine(self.date, self.start)
        b = datetime.combine(self.date, self.end)
        if b <= a:
            b += timedelta(days=1)
        return round((b - a).total_seconds() / 3600, 2)

    @property
    def pay(self):
        p = self.project
        return int(round(self.hours * p.extra_rate)) if p.extra_basis == "hourly" else 0

    @property
    def overlaps_office(self):
        log = DayLog.objects.filter(user=self.user, date=self.date).first()
        if not log or not (log.time_in and log.time_out):
            return False
        return self.start < log.time_out and self.end > log.time_in and self.end > self.start


class PlanEntry(models.Model):
    """One cell of the planner: what someone is planned to do on a day."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="plan_entries")
    date = models.DateField()
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.CASCADE)
    label = models.CharField(max_length=80, blank=True, help_text="Free text when not a project, e.g. Back-up")

    class Meta:
        unique_together = ("user", "date")

    @property
    def text(self):
        return str(self.project) if self.project_id else self.label


# --------------------------------------------------------------------------
# QC
# --------------------------------------------------------------------------
QC_CHECKLIST = [
    "Audio levels consistent, no clipping",
    "Music / song as approved, no cut-offs",
    "Colour matches across cameras, skin tones natural",
    "Names, dates and titles spelled correctly",
    "No flash frames, black frames or jump errors",
    "Agency logo / end card as per brief",
    "Duration and aspect ratio match the brief",
    "Export settings correct (resolution, codec, bitrate)",
    "Previous QC comments fixed",
    "Agency-specific notes followed",
]


class Review(models.Model):
    TL, QC = "tl", "qc"
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="reviews")
    kind = models.CharField(max_length=2, choices=[(TL, "TL review"), (QC, "QC")])
    passed = models.BooleanField()
    issues = models.JSONField(default=list, blank=True, help_text="Checklist items marked as issues")
    comments = models.TextField(blank=True, help_text="One per line, start with a timecode e.g. 00:12")
    reviewer = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------
class Invoice(models.Model):
    STATUS = [("draft", "Draft"), ("sent", "Sent"), ("part", "Part-paid"), ("paid", "Paid"), ("overdue", "Overdue")]
    number = models.CharField(max_length=30, unique=True)
    agency = models.ForeignKey(Agency, on_delete=models.PROTECT, related_name="invoices")
    issued_on = models.DateField(default=date.today)
    due_on = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="INR")
    gst_percent = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=8, choices=STATUS, default="draft")
    received = models.PositiveIntegerField(default=0)
    received_on = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["-issued_on", "-id"]

    def __str__(self):
        return self.number

    @property
    def symbol(self):
        return "$" if self.currency == "USD" else "₹"

    @property
    def subtotal(self):
        return sum(l.amount for l in self.lines.all())

    @property
    def gst(self):
        return round(self.subtotal * self.gst_percent / 100)

    @property
    def total(self):
        return self.subtotal + self.gst

    @property
    def balance(self):
        return max(self.total - self.received, 0)

    @property
    def is_overdue(self):
        return self.status in ("sent", "part") and self.due_on and self.due_on < timezone.localdate()


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    project = models.OneToOneField(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoice_line")
    couple = models.CharField(max_length=120)
    film = models.CharField(max_length=60)
    amount = models.PositiveIntegerField()

    class Meta:
        ordering = ["couple", "id"]


class PayrollMonth(models.Model):
    month = models.DateField(unique=True, help_text="First day of the month")
    finalized = models.BooleanField(default=False)
    finalized_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-month"]


class Payslip(models.Model):
    payroll = models.ForeignKey(PayrollMonth, on_delete=models.CASCADE, related_name="slips")
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    salary = models.PositiveIntegerField()
    leaves_taken = models.DecimalField(max_digits=4, decimal_places=1)
    unpaid_leaves = models.DecimalField(max_digits=4, decimal_places=1)
    leave_deduction = models.PositiveIntegerField()
    extra_pay = models.PositiveIntegerField()
    other = models.IntegerField(default=0)
    other_note = models.CharField(max_length=100, blank=True)
    net = models.IntegerField()

    class Meta:
        unique_together = ("payroll", "user")


# --------------------------------------------------------------------------
# Recognition
# --------------------------------------------------------------------------
class EmployeeOfMonth(models.Model):
    month = models.DateField(unique=True, help_text="First day of the month being celebrated")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="eotm_wins")
    reason = models.TextField()
    voucher_amount = models.PositiveIntegerField(default=1000)
    handed_over_on = models.DateField(null=True, blank=True)
    photo = models.ForeignKey(StoredImage, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-month"]

    def __str__(self):
        return f"{self.month:%B %Y} – {self.user}"

    def clean(self):
        if self.month and self.month.day != 1:
            raise ValidationError("Month must be the first day of the month.")


class Congrats(models.Model):
    award = models.ForeignKey(EmployeeOfMonth, on_delete=models.CASCADE, related_name="congrats")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.CharField(max_length=200, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("award", "user")
