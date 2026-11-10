from django import forms
from django.utils import timezone
from django.contrib.auth import get_user_model

from .models import (Agency, ClientRevision, LeaveRequest, ContractPayment, DayLog, DelayReport, DriveReturn, EmployeeOfMonth, ExtraEntry, HardDrive, Holiday, Pod,
                     Profile, Project, StudioSettings, TaskStage)
from .services import is_company_drive_link, team

User = get_user_model()
DATE = forms.DateInput(attrs={"type": "date"})
TIME = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


class StyledMixin:
    """Adds the app's input class and makes required fields obvious."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            if isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault("class", "check")
            else:
                f.widget.attrs.setdefault("class", "input")


# ---------------------------------------------------------------- safe photo uploads
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTOS = 6
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF", "MPO"}


def check_image(f):
    """Reject anything that isn't a real, reasonably sized photo (checked by decoding it, not by its name)."""
    from PIL import Image
    if f.size > MAX_PHOTO_BYTES:
        raise forms.ValidationError(f"{f.name} is over 10 MB.")
    try:
        f.seek(0)
        with Image.open(f) as img:
            fmt = img.format
            if img.width * img.height > 40_000_000:
                raise forms.ValidationError(f"{f.name} is too large in pixels.")
            img.verify()
    except forms.ValidationError:
        raise
    except Exception:
        raise forms.ValidationError(f"{f.name} isn't a photo we can read (JPEG, PNG, WebP or HEIC).")
    finally:
        f.seek(0)
    if fmt not in ALLOWED_FORMATS:
        raise forms.ValidationError(f"{f.name}: {fmt} files aren't accepted. Use JPEG, PNG, WebP or HEIC.")
    return f


class MultiPhotoInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class PhotosField(forms.FileField):
    """Several photos in one go (phone camera or gallery)."""
    def __init__(self, *args, max_files=MAX_PHOTOS, **kwargs):
        self.max_files = max_files
        kwargs.setdefault("widget", MultiPhotoInput(attrs={"accept": "image/*", "multiple": True}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        if not files:
            if self.required:
                raise forms.ValidationError("Add at least one photo.")
            return []
        if len(files) > self.max_files:
            raise forms.ValidationError(f"Up to {self.max_files} photos at a time.")
        return [check_image(super(PhotosField, self).clean(f, initial)) for f in files]


def editor_choices():
    return [("", "— Unassigned —")] + [(u.id, f"{u.profile.name} · {u.profile.pod or u.profile.get_role_display()}") for u in team()]


class ProjectForm(StyledMixin, forms.ModelForm):
    editor = forms.TypedChoiceField(required=False, coerce=int, empty_value=None)

    class Meta:
        model = Project
        fields = ["couple", "deliverable", "agency", "editor", "received_on", "deadline", "agency_deadline", "status",
                  "invoice_amount", "brief", "cut_link", "cloud_link", "is_extra", "extra_basis", "extra_rate", "contractor_fee",
                  "first_delivered_on", "timing_override", "timing_note"]
        widgets = {"received_on": DATE, "deadline": DATE, "agency_deadline": DATE, "first_delivered_on": DATE,
                   "brief": forms.Textarea(attrs={"rows": 3})}
        labels = {"first_delivered_on": "Sent to agency on"}
        help_texts = {"first_delivered_on": "The day it was really sent (first delivery). On time = on or before the agency's deadline."}

    source = forms.ChoiceField(label="Footage on", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["editor"].choices = editor_choices()
        self.fields["agency"].queryset = Agency.objects.filter(active=True)
        self.fields["source"].choices = source_choices()
        if self.instance.pk:
            self.initial["source"] = source_value(self.instance)
            if self.instance.drive_id and str(self.instance.drive_id) not in dict(self.fields["source"].choices):
                self.fields["source"].choices += [(str(self.instance.drive_id), str(self.instance.drive))]
        # keep the order: source right after the agency's deadline
        order = list(self.fields)
        order.remove("source")
        order.insert(order.index("agency_deadline") + 1, "source")
        self.order_fields(order)
        if self.instance.pk and self.instance.editor_id:
            self.initial["editor"] = self.instance.editor_id

    def clean(self):
        data = super().clean()
        ed, ad = data.get("deadline"), data.get("agency_deadline")
        if ed and ad and ed > ad:
            self.add_error("deadline", f"The editor's deadline ({ed:%-d %b}) is after the agency's ({ad:%-d %b}).")
        sent = data.get("first_delivered_on")
        if sent and sent > timezone.localdate():
            self.add_error("first_delivered_on", "The sent date can't be in the future.")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        apply_source(obj, self.cleaned_data.get("source", ""), self.cleaned_data.get("cloud_link", ""))
        if commit:
            obj.save()
        return obj

    def clean_editor(self):
        v = self.cleaned_data.get("editor")
        return User.objects.get(pk=v) if v else None


CLOUD = "cloud"


def source_choices():
    """Footage source: a hard drive, or Cloud / online."""
    return ([("", "— Not received yet —"), (CLOUD, "☁ Cloud / online (link or transfer)")]
            + [(str(d.pk), str(d)) for d in HardDrive.objects.exclude(status="sent").order_by("code")])


def apply_source(project, value, link=""):
    project.drive_id = int(value) if value and value.isdigit() else None
    project.received_online = value == CLOUD
    project.cloud_link = (link or "") if value == CLOUD else ""


def source_value(project):
    return str(project.drive_id) if project.drive_id else (CLOUD if project.received_online else "")


def film_key(deliverable):
    return Project.DELIVERABLES.index(deliverable)


class NewProjectForm(StyledMixin, forms.Form):
    """Creates one job per ticked film for the same couple. Each film can have its own
    editor's deadline, agency's deadline and invoice amount (blank = the defaults)."""
    couple = forms.CharField(label="Couple / project name", max_length=120)
    agency = forms.ModelChoiceField(queryset=Agency.objects.filter(active=True))
    deliverables = forms.MultipleChoiceField(label="Films", choices=[(d, d) for d in Project.DELIVERABLES],
                                             widget=forms.CheckboxSelectMultiple)
    editor = forms.TypedChoiceField(required=False, coerce=int, empty_value=None,
                                    help_text="Leave unassigned to put it in the 'Needs an editor' queue")
    deadline = forms.DateField(label="Editor's deadline", widget=DATE, required=False,
                               help_text="For all films, unless you set one per film below. Editors see only this date.")
    agency_deadline = forms.DateField(label="Agency's deadline", widget=DATE, required=False,
                                      help_text="Promised to the agency. Founders only, never shown to editors.")
    invoice_amount = forms.IntegerField(min_value=0, required=False, label="Invoice amount per film",
                                        help_text="Blank = the agency's usual rate for each film.")
    source = forms.ChoiceField(label="Footage on", required=False)
    cloud_link = forms.URLField(label="Download link (cloud)", required=False,
                                help_text="WeTransfer / Google Drive / Dropbox link, if the footage came online")
    is_extra = forms.BooleanField(required=False, label="Give as extra work (outside working hours, paid extra)")
    extra_basis = forms.ChoiceField(choices=[("fixed", "Fixed per job"), ("hourly", "Per hour")], required=False)
    extra_rate = forms.IntegerField(min_value=0, required=False, label="Extra pay (₹)")
    brief = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["editor"].choices = editor_choices()
        self.fields["source"].choices = source_choices()
        for f in self.fields.values():
            if isinstance(f.widget, forms.CheckboxSelectMultiple):
                f.widget.attrs["class"] = "pills"
        # Per-film overrides (shown in a table under the films you tick)
        for d in Project.DELIVERABLES:
            k = film_key(d)
            self.fields[f"f{k}_deadline"] = forms.DateField(required=False, widget=DATE, label=f"{d}: editor's deadline")
            self.fields[f"f{k}_agency_deadline"] = forms.DateField(required=False, widget=DATE, label=f"{d}: agency's deadline")
            self.fields[f"f{k}_amount"] = forms.IntegerField(required=False, min_value=0, label=f"{d}: invoice amount",
                                                             widget=forms.NumberInput(attrs={"inputmode": "numeric"}))
            for n in ("deadline", "agency_deadline", "amount"):
                self.fields[f"f{k}_{n}"].widget.attrs["class"] = "input"

    MAIN = ["couple", "agency", "deliverables", "editor", "deadline", "agency_deadline", "invoice_amount",
            "source", "cloud_link", "is_extra", "extra_basis", "extra_rate", "brief"]

    def main_fields(self):
        return [self[n] for n in self.MAIN]

    def film_rows(self):
        return [{"name": d, "key": film_key(d), "deadline": self[f"f{film_key(d)}_deadline"],
                 "agency_deadline": self[f"f{film_key(d)}_agency_deadline"], "amount": self[f"f{film_key(d)}_amount"]}
                for d in Project.DELIVERABLES]

    def films(self):
        """[(deliverable, editor_deadline, agency_deadline, amount_or_None)] for ticked films."""
        c = self.cleaned_data
        out = []
        for d in c.get("deliverables", []):
            k = film_key(d)
            amount = c.get(f"f{k}_amount")
            out.append((d, c.get(f"f{k}_deadline") or c.get("deadline"),
                        c.get(f"f{k}_agency_deadline") or c.get("agency_deadline"),
                        amount if amount is not None else c.get("invoice_amount")))
        return out

    def clean(self):
        data = super().clean()
        if data.get("source") == CLOUD and not data.get("cloud_link"):
            pass  # a link is helpful but optional (it may have come by WhatsApp)
        if "deliverables" in data:
            for d, ed, ad, _ in self.films():
                if ed and ad and ed > ad:
                    k = film_key(d)
                    field = f"f{k}_deadline" if self.cleaned_data.get(f"f{k}_deadline") else "deadline"
                    self.add_error(field, f"{d}: the editor's deadline ({ed:%-d %b}) is after the agency's ({ad:%-d %b}).")
        return data


class AssignForm(forms.Form):
    editor = forms.TypedChoiceField(coerce=int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["editor"].choices = editor_choices()[1:]


class DayForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = DayLog
        fields = ["day_type", "time_in", "time_out", "notes"]
        widgets = {"time_in": TIME, "time_out": TIME, "day_type": forms.RadioSelect}

    def __init__(self, *args, future=False, **kwargs):
        self.future = future
        super().__init__(*args, **kwargs)
        self.fields["day_type"].widget.attrs["class"] = "pills"
        self.fields["notes"].label = "Notes for my TL"
        # Already-approved leave keeps its type; anything else in the future needs approval first
        self.approved_type = self.instance.day_type if (self.instance.pk and self.instance.day_type in (DayLog.LEAVE, DayLog.HALF)) else None

    def clean_day_type(self):
        t = self.cleaned_data["day_type"]
        if self.future and t in (DayLog.LEAVE, DayLog.HALF) and t != self.approved_type:
            raise forms.ValidationError("Leave on a future day needs the founders' approval. Use My leave → Apply for leave.")
        return t


class TaskRowForm(forms.Form):
    project = forms.ModelChoiceField(queryset=Project.objects.none(), required=False,
                                     widget=forms.Select(attrs={"class": "input"}))
    stage = forms.ChoiceField(choices=[("", "— Stage —")] + list(TaskStage.choices), required=False,
                              widget=forms.Select(attrs={"class": "input"}))
    hours = forms.DecimalField(required=False, min_value=0, max_value=16, decimal_places=2,
                               widget=forms.NumberInput(attrs={"class": "input", "step": "0.25", "placeholder": "auto"}))

    def __init__(self, *args, projects=None, **kwargs):
        super().__init__(*args, **kwargs)
        if projects is not None:
            self.fields["project"].queryset = projects
        self.fields["project"].empty_label = "— Choose a job —"

    def clean(self):
        c = super().clean()
        if c.get("project") and not c.get("stage"):
            self.add_error("stage", "Pick a stage")
        return c


TaskFormSet = forms.formset_factory(TaskRowForm, extra=0, max_num=12)


class CloseJobForm(StyledMixin, forms.Form):
    project_file_link = forms.URLField(label="Project file link (Google Drive)")
    final_mp4_link = forms.URLField(label="Final film link (MP4 on Google Drive)")
    confirm = forms.BooleanField(label="I checked both links open, and the project file includes all footage, music and graphics used.")

    def clean(self):
        c = super().clean()
        for f in ("project_file_link", "final_mp4_link"):
            if c.get(f) and not is_company_drive_link(c[f]):
                self.add_error(f, "This must be a Google Drive link on the company Drive.")
        return c


class ReviewForm(forms.Form):
    comments = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4, "class": "input",
                               "placeholder": "One per line, e.g. 00:12 Groom's name spelled wrong"}))
    cut_link = forms.URLField(required=False, widget=forms.URLInput(attrs={"class": "input"}), label="Film link")


class ExtraEntryForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = ExtraEntry
        fields = ["project", "stage", "date", "start", "end", "note"]
        widgets = {"date": DATE, "start": TIME, "end": TIME}
        labels = {"project": "Extra job", "start": "From", "end": "To"}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.filter(editor=user, is_extra=True).exclude(extra_accepted=False)


class AgencyForm(StyledMixin, forms.ModelForm):
    rates = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}),
                            help_text="Usual rates, one per line: Trailer = 18000")

    class Meta:
        model = Agency
        fields = ["name", "code", "city", "contact_person", "phone", "invoice_emails", "billing_address", "currency",
                  "payment_terms_days", "gstin", "pan", "notes", "active"]
        widgets = {"billing_address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["rates"] = "\n".join(f"{r.deliverable} = {r.amount}" for r in self.instance.rates.all())

    def clean_code(self):
        import re
        c = (self.cleaned_data.get("code") or "").strip().upper()
        if not c:
            return None
        if not re.fullmatch(r"[A-Z]{1,3}", c):
            raise forms.ValidationError("Use 1 to 3 letters (A–Z), e.g. BRC.")
        if Agency.objects.filter(code__iexact=c).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(f"{c} is already used by another agency.")
        return c

    def clean_rates(self):
        out = {}
        for line in self.cleaned_data.get("rates", "").splitlines():
            if not line.strip():
                continue
            if "=" not in line:
                raise forms.ValidationError(f"Use 'Deliverable = amount' (problem: {line!r})")
            k, v = line.split("=", 1)
            try:
                out[k.strip()] = int(v.strip().replace(",", ""))
            except ValueError:
                raise forms.ValidationError(f"Amount must be a number (problem: {line!r})")
        return out


class DriveForm(StyledMixin, forms.ModelForm):
    photos_upload = PhotosField(required=False, label="Photos (label, serial sticker, any damage) — up to 6")

    class Meta:
        model = HardDrive
        fields = ["code", "kind", "agency", "studio_owned", "contents", "brand_model", "serial", "capacity_tb",
                  "data_tb", "received_on", "received_via", "tracking", "location", "damaged_on_arrival", "status", "notes"]
        widgets = {"received_on": DATE, "notes": forms.Textarea(attrs={"rows": 2})}


class DriveReturnForm(StyledMixin, forms.ModelForm):
    photos_upload = PhotosField(required=False, label="Photos when sending (drive, parcel, receipt) — up to 6")

    class Meta:
        model = DriveReturn
        fields = ["sent_on", "sent_to", "method", "courier", "tracking", "collected_by", "condition_ok", "notes", "status"]
        widgets = {"sent_on": DATE, "notes": forms.Textarea(attrs={"rows": 2})}


class HolidayForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ["date", "name", "kind"]
        widgets = {"date": DATE}


class StudioSettingsForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = StudioSettings
        fields = "__all__"
        widgets = {"shift_start": TIME, "address": forms.Textarea(attrs={"rows": 3})}


class PersonForm(StyledMixin, forms.Form):
    first_name = forms.CharField(label="Name", max_length=60)
    last_name = forms.CharField(label="Surname", max_length=60, required=False)
    username = forms.CharField(label="Login ID", max_length=60, required=False,
                               help_text="Filled in as Name_Surname while you type. You can change it. Letters, numbers and _ . - only; "
                                         "capitals don't matter when they log in.")
    email = forms.EmailField(required=False)
    role = forms.ChoiceField(choices=Profile.ROLES)
    confirm_founder = forms.BooleanField(
        required=False,
        label="Yes, give this person full founder access: invoices, salaries, client details, everyone's data, "
              "adding and removing people, and the back office.")
    pod = forms.ModelChoiceField(queryset=Pod.objects.all(), required=False)
    employment = forms.ChoiceField(choices=Profile.EMPLOYMENT, initial=Profile.FULL_TIME,
                                   widget=forms.RadioSelect(attrs={"class": "pills"}),
                                   label="Employment", required=False)

    def clean_employment(self):
        return self.cleaned_data.get("employment") or Profile.FULL_TIME
    monthly_salary = forms.IntegerField(min_value=0, initial=0, label="Monthly salary / stipend (₹)",
                                        help_text="Full-time only. Goes through monthly payroll.")
    contract_basis = forms.ChoiceField(choices=[("", "— How are they paid? —")] + Profile.BASIS, required=False,
                                       label="Contract pay basis")
    contract_rate = forms.IntegerField(min_value=0, initial=0, required=False, label="Contract rate (₹)",
                                       help_text="₹ per hour / per day / per month. Per-film fees are set on each film.")
    joined_on = forms.DateField(widget=DATE, required=False)
    active = forms.BooleanField(required=False, initial=True)
    password = forms.CharField(
        required=False, strip=False, label="Password",
        widget=forms.TextInput(attrs={"autocomplete": "off", "autocapitalize": "none", "spellcheck": "false",
                                      "class": "input mono", "data-password": "1"}),
        help_text="Shown as you type so you can share it. They'll be asked to choose their own at first login. "
                  "Leave blank to keep the current one.")

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        super().__init__(*args, **kwargs)
        if instance:
            p = instance.profile
            self.initial.update(first_name=instance.first_name, last_name=instance.last_name, username=instance.username,
                                email=instance.email, employment=p.employment, contract_basis=p.contract_basis,
                                contract_rate=p.contract_rate,
                                role=p.role, pod=p.pod, monthly_salary=p.monthly_salary, joined_on=p.joined_on, active=p.active)

    @property
    def is_contract(self):
        return (self.data.get("employment") if self.is_bound else self.initial.get("employment")) == Profile.CONTRACT

    def clean_username(self):
        import re
        u = self.cleaned_data["username"].strip()
        if not u and self.is_contract:
            # Contract editors never sign in; they still need an internal ID
            base = re.sub(r"[^A-Za-z0-9]+", "_", f"{self.data.get('first_name', '')} {self.data.get('last_name', '')}".strip()).strip("_") or "contract"
            u, n = base, 2
            while User.objects.filter(username__iexact=u).exclude(pk=getattr(self.instance, "pk", None)).exists():
                u, n = f"{base}_{n}", n + 1
            return u
        if not u:
            raise forms.ValidationError("Enter a login ID.")
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", u):
            raise forms.ValidationError("Use letters, numbers and _ . - only (no spaces).")
        qs = User.objects.filter(username__iexact=u)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("That login ID is taken. Try adding a number or a middle initial.")
        return u

    def clean_password(self):
        from django.contrib.auth.password_validation import validate_password
        pw = self.cleaned_data.get("password")
        if self.is_contract:
            return ""          # contract editors don't get a login
        if not self.instance and not pw:
            raise forms.ValidationError("New people need a first password.")
        if pw:
            validate_password(pw, self.instance)
        return pw

    def clean(self):
        data = super().clean()
        inst = self.instance
        if data.get("employment") == Profile.CONTRACT:
            if not data.get("contract_basis"):
                self.add_error("contract_basis", "Choose how this contract person is paid.")
            elif data["contract_basis"] != "project" and not data.get("contract_rate"):
                self.add_error("contract_rate", "Enter their rate.")
        becoming_founder = data.get("role") == Profile.ADMIN and not (inst and inst.profile.role == Profile.ADMIN)
        if becoming_founder and not data.get("confirm_founder"):
            self.add_error("confirm_founder", "Tick this to confirm, or pick another role. Founder access can see all money and salaries.")
        if inst and inst.profile.role == Profile.ADMIN:
            losing = data.get("role") != Profile.ADMIN or not data.get("active")
            if losing:
                others = Profile.objects.filter(role=Profile.ADMIN, active=True, user__is_active=True).exclude(user=inst)
                if not others.exists():
                    raise forms.ValidationError("This is the last active founder login. Add another founder before changing it.")
                if getattr(self, "actor", None) and self.actor.pk == inst.pk:
                    raise forms.ValidationError("You can't remove your own founder access. Ask the other founder.")
        return data


class EOTMForm(StyledMixin, forms.ModelForm):
    month = forms.DateField(widget=forms.DateInput(attrs={"type": "month"}, format="%Y-%m"),
                            input_formats=["%Y-%m", "%Y-%m-%d"])
    photo_upload = forms.FileField(required=False, label="Photo (optional)", validators=[check_image],
                                   widget=forms.ClearableFileInput(attrs={"accept": "image/*"}))

    class Meta:
        model = EmployeeOfMonth
        fields = ["month", "user", "reason", "voucher_amount", "handed_over_on"]
        widgets = {"reason": forms.Textarea(attrs={"rows": 3}), "handed_over_on": DATE}
        labels = {"user": "Winner", "reason": "Why they won (the whole team sees this)",
                  "voucher_amount": "Amazon voucher card (₹)", "handed_over_on": "Card handed over on"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = User.objects.filter(profile__active=True).exclude(profile__role="admin").order_by("first_name")
        self.fields["user"].label_from_instance = lambda u: u.profile.name

    def clean_month(self):
        return self.cleaned_data["month"].replace(day=1)

    def validate_unique(self):
        """Choosing a winner for a month that already has one replaces it (handled in the view)."""


class DelayForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = DelayReport
        fields = ["proposed_date", "reason", "note"]
        widgets = {"proposed_date": DATE, "note": forms.Textarea(attrs={"rows": 3, "placeholder": "e.g. Client sent new songs on Monday; need 2 more days for the re-cut."})}

    def __init__(self, *args, project=None, **kwargs):
        self.project = project
        super().__init__(*args, **kwargs)
        self.fields["note"].required = True

    def clean_proposed_date(self):
        d = self.cleaned_data["proposed_date"]
        from django.utils import timezone
        if d < timezone.localdate():
            raise forms.ValidationError("Pick today or a later date.")
        if self.project and self.project.deadline and d <= self.project.deadline:
            raise forms.ValidationError(f"Pick a date after the current deadline ({self.project.deadline:%-d %b}).")
        return d


class ContractPaymentForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = ContractPayment
        fields = ["paid_on", "amount", "method", "reference", "note"]
        widgets = {"paid_on": DATE}

    def clean_amount(self):
        a = self.cleaned_data["amount"]
        if not a:
            raise forms.ValidationError("Enter the amount paid.")
        return a


class ClientRevisionForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = ClientRevision
        fields = ["requested_on", "changes", "due_date", "editor"]
        widgets = {"requested_on": DATE, "due_date": DATE,
                   "changes": forms.Textarea(attrs={"rows": 4, "placeholder": "e.g. Change the song at 02:10; add the haldi shots; spell the groom's name 'Kartik'."})}

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["editor"].queryset = User.objects.filter(pk__in=[u.pk for u in team()])
        self.fields["editor"].label_from_instance = lambda u: u.profile.name
        self.fields["editor"].label = "Editor for the revision"
        if project and not self.is_bound:
            self.initial.setdefault("editor", project.editor_id)

    def clean_due_date(self):
        from django.utils import timezone
        d = self.cleaned_data["due_date"]
        if d < timezone.localdate():
            raise forms.ValidationError("Pick today or a later date.")
        return d


class LeaveRequestForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ["start", "end", "half_day", "reason"]
        widgets = {"start": DATE, "end": DATE}
        labels = {"reason": "Reason (founders see this)"}

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["end"].required = False
        self.fields["end"].help_text = "Leave blank for a single day."

    def clean(self):
        from datetime import timedelta
        from django.utils import timezone
        data = super().clean()
        s, e = data.get("start"), data.get("end") or data.get("start")
        if not s:
            return data
        data["end"] = e
        today = timezone.localdate()
        if s <= today:
            self.add_error("start", "Advance leave starts tomorrow or later. For today, mark it on your Today page.")
        if e < s:
            self.add_error("end", "The last day can't be before the first.")
        elif (e - s).days > 30:
            self.add_error("end", "Up to 31 days in one request. Split longer leave into two.")
        if data.get("half_day") and e != s:
            self.add_error("half_day", "A half day must be a single date.")
        if self.user and not self.errors:
            clash = LeaveRequest.objects.filter(user=self.user, status__in=(LeaveRequest.PENDING, LeaveRequest.APPROVED),
                                                start__lte=e, end__gte=s)
            if clash.exists():
                c = clash.first()
                raise forms.ValidationError(f"You already asked for {c.start:%-d %b}–{c.end:%-d %b} ({c.get_status_display().lower()}).")
        return data


class ContractWorkForm(StyledMixin, forms.Form):
    """Founders log a contract editor's day (contract editors have no login)."""
    date = forms.DateField(widget=DATE)
    day_type = forms.ChoiceField(choices=[(DayLog.OFFICE, "Full day"), (DayLog.HALF, "Half day"), (DayLog.WFH, "Remote")],
                                 initial=DayLog.OFFICE, label="Day")
    time_in = forms.TimeField(widget=TIME, required=False, label="Start")
    time_out = forms.TimeField(widget=TIME, required=False, label="End")
    project = forms.ModelChoiceField(queryset=Project.objects.none(), required=False, label="Film (optional)")
    notes = forms.CharField(max_length=200, required=False)

    def __init__(self, *args, user=None, hourly=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.hourly = hourly
        if user is not None:
            self.fields["project"].queryset = Project.objects.filter(editor=user).exclude(status=Project.CLOSED).select_related("agency")
        if hourly:
            self.fields["time_in"].required = self.fields["time_out"].required = True
            self.fields["time_in"].help_text = "Hours are paid from start to end, minus the studio break on days over 5 h."

    def clean(self):
        data = super().clean()
        if self.hourly and data.get("time_in") and data.get("time_out") and data["time_in"] == data["time_out"]:
            self.add_error("time_out", "End must differ from start.")
        return data


class QuickDayForm(DayForm):
    """Filling a missed day from My month: the same fields as Today, with the essentials
    made mandatory so a back-filled day is complete (times for a working day)."""
    NEEDS_TIMES = (DayLog.OFFICE, DayLog.WFH, DayLog.HALF, DayLog.DOWNTIME)
    NEEDS_JOBS = (DayLog.OFFICE, DayLog.WFH, DayLog.HALF)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Holidays and weekly offs are set by the studio; a missed day is work, leave or downtime
        self.fields["day_type"].choices = [c for c in DayLog.DAY_TYPES if c[0] not in (DayLog.HOLIDAY, DayLog.OFF)]
        self.fields["time_in"].label, self.fields["time_out"].label = "In", "Out"

    def clean(self):
        data = super().clean()
        t = data.get("day_type")
        if t in self.NEEDS_TIMES:
            for f in ("time_in", "time_out"):
                if not data.get(f):
                    self.add_error(f, "Required for a working day.")
            if data.get("time_in") and data.get("time_in") == data.get("time_out"):
                self.add_error("time_out", "Out can't be the same as In.")
        else:
            data["time_in"] = data["time_out"] = None
        return data
