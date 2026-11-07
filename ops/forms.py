from django import forms
from django.contrib.auth import get_user_model

from .models import (Agency, ContractPayment, DayLog, DelayReport, DriveReturn, EmployeeOfMonth, ExtraEntry, HardDrive, Holiday, Pod,
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
        fields = ["couple", "deliverable", "agency", "tier", "editor", "received_on", "deadline", "status",
                  "invoice_amount", "drive", "brief", "cut_link", "is_extra", "extra_basis", "extra_rate", "contractor_fee"]
        widgets = {"received_on": DATE, "deadline": DATE, "brief": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["editor"].choices = editor_choices()
        self.fields["agency"].queryset = Agency.objects.filter(active=True)
        self.fields["drive"].queryset = HardDrive.objects.exclude(status="sent")
        if self.instance.pk and self.instance.editor_id:
            self.initial["editor"] = self.instance.editor_id

    def clean_editor(self):
        v = self.cleaned_data.get("editor")
        return User.objects.get(pk=v) if v else None


class NewProjectForm(StyledMixin, forms.Form):
    """Creates one job per ticked deliverable for the same couple."""
    couple = forms.CharField(label="Couple / project name", max_length=120)
    agency = forms.ModelChoiceField(queryset=Agency.objects.filter(active=True))
    deliverables = forms.MultipleChoiceField(choices=[(d, d) for d in Project.DELIVERABLES],
                                             widget=forms.CheckboxSelectMultiple)
    tier = forms.TypedChoiceField(choices=Project.TIERS, coerce=int, initial=2)
    deadline = forms.DateField(widget=DATE, required=False)
    invoice_amount = forms.IntegerField(min_value=0, required=False, help_text="Per deliverable. Blank = agency's usual rate.")
    drive = forms.ModelChoiceField(queryset=HardDrive.objects.exclude(status="sent"), required=False)
    editor = forms.TypedChoiceField(required=False, coerce=int, empty_value=None,
                                    help_text="Leave unassigned to put it in the 'Needs an editor' queue")
    is_extra = forms.BooleanField(required=False, label="Give as extra work (outside working hours, paid extra)")
    extra_basis = forms.ChoiceField(choices=[("fixed", "Fixed per job"), ("hourly", "Per hour")], required=False)
    extra_rate = forms.IntegerField(min_value=0, required=False, label="Extra pay (₹)")
    brief = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["editor"].choices = editor_choices()
        for f in self.fields.values():
            if isinstance(f.widget, forms.CheckboxSelectMultiple):
                f.widget.attrs["class"] = "pills"


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["day_type"].widget.attrs["class"] = "pills"
        self.fields["notes"].label = "Notes for my TL"


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


TaskFormSet = forms.formset_factory(TaskRowForm, extra=0, max_num=6)


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
        fields = ["name", "city", "contact_person", "phone", "invoice_emails", "billing_address", "currency",
                  "payment_terms_days", "gstin", "pan", "notes", "active"]
        widgets = {"billing_address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["rates"] = "\n".join(f"{r.deliverable} = {r.amount}" for r in self.instance.rates.all())

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
    username = forms.CharField(label="Login ID", max_length=60,
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

    def clean_username(self):
        import re
        u = self.cleaned_data["username"].strip()
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
