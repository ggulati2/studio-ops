"""AutoCheck learns from QC, with no buttons and no AI.

Each AutoCheck flag carries a strength ("sev": 1 = just at the limit, 2 = twice as bad). When the
editor sends the next version, the helper reads every Frame.io comment on the version QC just
reviewed and reports it. Matching those against the last check's flags tells us, per flag:

- QC deleted the [Hygiene] comment, or replied "not an issue" next to it  -> it was wrong
- QC wrote their own note about the same thing next to it                  -> it was right
- the flag is gone in the new version (the editor fixed it)                -> it was right
- QC wrote a note about something AutoCheck measures, where it flagged
  nothing                                                                   -> it was missed
  (if it had seen something there below its limit, that strength is a sample too)

QC doesn't have to send a new version for this: once a day a QC person's helper re-reads the
comments on each recently checked version (read_feedback), and AutoCheck learns as soon as QC has
finished with it (they did something, and nothing changed for a day). Either way a check teaches once.

Each kind of flag keeps its recent samples, per agency + film type, per agency and for the whole
studio. Once there are enough (MIN_SAMPLES), the limit is moved, in small steps, to where it
best separates "QC agreed" from "QC removed it". The run uses the most specific limit that has
enough evidence. Founders see every change, can undo it or lock a limit (AutoCheck learnt tab).
"""
import logging
import re

from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)

MIN_SAMPLES = 10          # evidence needed before a limit moves
KEEP = 60                 # samples kept per row (the most recent)
LOW, HIGH = 0.4, 2.5      # a limit never goes below / above these (never switched off)
STEP = 1.25               # most a limit moves in one go (x1.25 or /1.25)
NEAR_S = 3.0              # seconds: a QC note this close to a flag is about the same moment

# what each kind of flag is called, for people
LABELS = {
    "blacks": "Crushed blacks", "highlights": "Blown highlights", "black_frame": "Black frames",
    "flash": "Flash frames", "media_offline": "Media Offline / broken frames", "framing": "Black bars / framing",
    "blur": "Out-of-focus shots", "shake": "Camera shake", "jump_cut": "Jump cuts", "colour_jump": "Colour jumps",
    "audio_peaks": "Audio at 0 dB", "clipping": "Distorted audio", "loudness": "Uneven loudness", "pops": "Audio pops",
    "silence": "Sudden silences", "music_end": "Music cut off at the end",
}
NOTES = {"text": "Titles, names and spelling", "edit": "Edit and story changes", "look": "Colour and look (overall)",
         "audio": "Audio (music choice, mix)", "other": "Other notes"}

# QC's words -> the flag they're about (first match wins; order matters)
KEYWORDS = [
    ("media_offline", r"media offline|offline|red (screen|frame)|missing (clip|media|footage)|corrupt|glitch(ed)? frame"),
    ("flash", r"flash frame|single frame|stray frame|one frame"),
    ("black_frame", r"black (frame|gap|screen)|blank frame"),
    ("framing", r"black bars?|letter ?box|pillar|side bars?|aspect|not (filling|full) (the )?frame|scale|framing|vertical clip"),
    ("blur", r"out of focus|focus|blurr?y|soft shot|\bsoft\b|not sharp"),
    ("shake", r"shak|stabili|jitter|wobbl|jerky"),
    ("jump_cut", r"jump ?cut"),
    ("colour_jump", r"colou?r (match|jump|shift|mismatch)|match (the )?(colou?r|grade|shots?)|grade (jump|mismatch)|exposure jump"),
    ("blacks", r"crush|blacks?\b|too dark|underexpos|lift"),
    ("highlights", r"blown|highlight|overexpos|too bright|burnt|clipp?ed (sky|white)"),
    ("music_end", r"(music|song|track|audio|sound).{0,30}(end|abrupt|cut ?off|stops?|fade ?out)|fade ?out (the )?(music|song|audio)"),
    ("clipping", r"distort|crackl|clipp|too hot|break(s|ing)? up|harsh audio"),
    ("audio_peaks", r"peak|0 ?db|too loud"),
    ("pops", r"\bpop\b|pops|click|crackle"),
    ("silence", r"silen|audio (drop|gap|missing)|no (audio|sound)|sound (drops|missing)"),
    ("loudness", r"uneven|level|volume|loudness|too (soft|low)|quiet"),
]
_KEYWORDS = [(k, re.compile(p, re.I)) for k, p in KEYWORDS]
NEG = re.compile(r"not an? (issue|problem)|no (issue|problem)|ignore|intended|on purpose|by design|that'?s fine|it'?s fine|"
                 r"its fine|looks fine|is fine|ok as is|leave (it|as is)|false (alarm|flag)|wrong flag|not needed|skip this", re.I)
# our own comments start [Hygiene] (older ones [AutoCheck]); Frame.io may show the avatar's initials first
OURS = re.compile(r"^\s*(?:[A-Z]{1,3}\s+)?\[(autocheck|hygiene)\]\s*", re.I)

# a flag's strength means something new from this helper version on: older reports don't teach it
REMEASURED = {"shake": "1.4.0"}     # 1.4.0: wobble around the camera's path, not any change of speed


def _older(version, than):
    def parts(v):
        try:
            return [int(x) for x in str(v).split(".")[:3]]
        except ValueError:
            return [0]
    return parts(version or "0") < parts(than)


def _stale(kind, report):
    """True if this report measured `kind` the old way (its strengths can't teach today's limit)."""
    version = (report or {}).get("version")
    return kind in REMEASURED and bool(version) and _older(version, REMEASURED[kind])


def flag_of(text):
    """Which kind of flag a QC note is about, or None."""
    for k, rx in _KEYWORDS:
        if rx.search(text or ""):
            return k
    return None


def note_group(text):
    t = (text or "").lower()
    if re.search(r"font|title|spell|typo|caption|lower ?third|subtitle|text|name|logo|watermark", t):
        return "text"
    if re.search(r"music|song|track|bgm|mix|voice|audio|sound", t):
        return "audio"
    if re.search(r"colou?r|grade|grading|warm|cool|skin|tone|lut|saturat", t):
        return "look"
    if re.search(r"cut|remove|trim|delete|add|insert|shot|replace|move|edit|extend|shorten|keep|transition|slow|fast|speed|story|pace|pacing", t):
        return "edit"
    return "other"


def _norm(text):
    return re.sub(r"\s+", " ", OURS.sub("", text or "")).strip().lower()[:40]


# ---------------------------------------------------------------- limits for a run
def _scopes(project):
    return [(project.agency_id, project.deliverable), (project.agency_id, ""), (None, "")]


def factors_for(project):
    """{flag kind: limit} for a run on this film: the most specific limit with enough evidence
    (or that a founder locked); 1 where nothing has been learnt."""
    from .models import AutoCheckTuning
    rows = {}
    for t in AutoCheckTuning.objects.filter(agency_id__in=[project.agency_id, None]).exclude(kind__startswith="note:"):
        rows[(t.agency_id, t.deliverable, t.kind)] = t
    out = {}
    for kind in LABELS:
        for a, d in _scopes(project):
            t = rows.get((a, d, kind))
            if t and (t.locked or len(t.samples) >= MIN_SAMPLES):
                if abs(t.factor - 1) > 0.005:
                    out[kind] = round(t.factor, 3)
                break
    return out


def previous_run(run):
    """The check on the version QC just reviewed (the one this run compares against)."""
    from .models import AutoCheckRun
    if not run.v1_link:
        return None
    return (AutoCheckRun.objects.filter(project_id=run.project_id, event_id=run.event_id, status=AutoCheckRun.DONE,
                                        v2_link=run.v1_link).exclude(pk=run.pk).order_by("-finished_at", "-pk").first())


def prev_issues(run):
    """The last check's flags on the earlier version, for the helper to look for again."""
    prev = previous_run(run)
    if not prev:
        return []
    return [{"i": i, "kind": x.get("kind"), "t": x.get("t"), "end": x.get("end")}
            for i, x in enumerate((prev.report or {}).get("issues", [])) if x.get("kind") in LABELS]


# ---------------------------------------------------------------- reading QC's reaction
def judge(prev_report, report):
    """Labels from what QC did on the earlier version. Returns (samples, counts, notes):
    samples = [(kind, sev, real?, why)], counts = {kind: {right, wrong, missed, subtle}},
    notes = {group: n} for QC notes AutoCheck has no check for."""
    seen = report.get("seen")
    if not seen:
        return [], {}, {}
    issues = prev_report.get("issues", [])
    posted = [p for p in prev_report.get("posted", []) if p.get("ok")]
    ours = [c for c in seen if c.get("ours") or OURS.match(c.get("text") or "")]
    human = [c for c in seen if c not in ours]
    # which of our flags are still on the earlier version (QC deletes the wrong ones)
    by_issue = {}
    for p in posted:
        i = p.get("issue")
        if i is None:        # older reports: find the flag by its text
            i = next((k for k, x in enumerate(issues) if x.get("text") and _norm(p.get("text")) == _norm(x["text"])), None)
        if i is not None:
            by_issue[i] = p
    def still_there(p):
        return any(abs((c.get("t") or 0) - (p.get("t") or 0)) <= 1.5 and _norm(c.get("text")) == _norm(p.get("text")) for c in ours)
    found = sum(1 for p in by_issue.values() if still_there(p))
    # deletions only mean something if we read the comments properly: some of ours are still there,
    # or there were only one or two flags to begin with
    reliable = bool(by_issue) and (found >= 1 or len(by_issue) <= 2)
    follow = {f.get("i"): f for f in report.get("followup", [])}

    samples, counts = [], {}
    def count(kind, key):
        counts.setdefault(kind, {"right": 0, "wrong": 0, "missed": 0, "subtle": 0})[key] += 1

    used = set()
    for i, x in enumerate(issues):
        kind, sev = x.get("kind"), x.get("sev")
        if kind not in LABELS or sev is None or _stale(kind, prev_report):
            continue
        a, b = (x.get("t") or 0) - NEAR_S, (x.get("end") or x.get("t") or 0) + NEAR_S
        near = [c for c in human if a <= (c.get("t") or 0) <= b]
        verdict = why = None
        if any(NEG.search(c.get("text") or "") for c in near):
            verdict, why = False, "QC said it's not an issue"
        elif any(flag_of(c.get("text")) == kind for c in near):
            verdict, why = True, "QC noted the same thing"      # even if they replaced our comment with their own
        elif i in by_issue and reliable and not still_there(by_issue[i]):
            verdict, why = False, "QC deleted the flag"
        elif (follow.get(i) or {}).get("still") is False:
            verdict, why = True, "the editor fixed it"
        for c in near:
            if flag_of(c.get("text")) == kind or NEG.search(c.get("text") or ""):
                used.add(id(c))
        if verdict is None:
            continue
        samples.append((kind, float(sev), verdict, why))
        count(kind, "right" if verdict else "wrong")

    cands = prev_report.get("cands", {}) or {}
    notes = {}
    for c in human:
        if id(c) in used or NEG.search(c.get("text") or ""):
            continue
        kind = flag_of(c.get("text"))
        t = c.get("t") or 0
        if kind is None:
            g = note_group(c.get("text"))
            notes[g] = notes.get(g, 0) + 1
            continue
        if any(x.get("kind") == kind and (x.get("t") or 0) - NEAR_S <= t <= (x.get("end") or x.get("t") or 0) + NEAR_S
               for x in issues):
            continue        # we flagged it (and the note was counted above)
        if _stale(kind, prev_report):
            continue        # measured the old way: its near misses don't say where today's limit should be
        near = [cv for ct, cv in cands.get(kind, []) if abs(ct - t) <= NEAR_S]
        if near:
            samples.append((kind, float(max(near)), True, "QC found one it saw but kept below its limit"))
            count(kind, "missed")
        else:
            count(kind, "subtle")
    return samples, counts, notes


# ---------------------------------------------------------------- moving a limit
def fit(samples, current):
    """The limit that best separates real flags (strength at or above it) from wrong ones (below),
    moved at most one step from the current one. Ties go to the current limit."""
    pts = sorted({round(s, 3) for s, _ in samples})
    grid = {round(LOW + i * 0.05, 3) for i in range(int((HIGH - LOW) / 0.05) + 1)}
    grid |= {round((a + b) / 2, 3) for a, b in zip(pts, pts[1:])}
    grid.add(round(current, 3))

    def errors(f):
        return sum(1 for s, real in samples if (s >= f) != bool(real))
    best = min(sorted(grid), key=lambda f: (errors(f), abs(f - current)))
    new = min(max(best, current / STEP), current * STEP)
    return round(min(max(new, LOW), HIGH), 3)


def _reason(kind, old, new, rows):
    name = LABELS.get(kind, kind).lower()
    recent = rows[-KEEP:]
    if new > old:
        n = sum(1 for s in recent if not s[1] and s[0] < new)
        return f"Flags {name} less often now: QC removed or dismissed {n} recent flag{'s' if n != 1 else ''} that were near the limit."
    n = sum(1 for s in recent if s[1] and s[0] < old)
    return f"Flags {name} more often now: QC found {n} it had seen but kept below its limit."


@transaction.atomic
def learn(run):
    """Learn from QC's reaction to the previous check. Safe to call more than once: each previous
    check teaches once. Returns a short summary (also stored on both reports), or None."""
    from .models import AutoCheckRun
    report = run.report or {}
    prev = previous_run(run)
    if not prev or not report.get("seen"):
        return None
    prev = AutoCheckRun.objects.select_for_update().get(pk=prev.pk)
    if (prev.report or {}).get("feedback"):
        return None
    samples, counts, notes = judge(prev.report or {}, report)
    summary = _teach(run.project, samples, counts, notes, run, from_run=prev.pk)
    prev.report = {**(prev.report or {}), "feedback": summary}
    prev.save(update_fields=["report", "updated"])
    run.report = {**report, "learnt": summary}
    run.save(update_fields=["report", "updated"])
    return summary


def _teach(p, samples, counts, notes, run, from_run):
    """Add what QC's reaction showed to each limit's evidence and move the limits that have enough.
    Returns the summary stored on the reports."""
    from .models import AutoCheckLesson, AutoCheckTuning
    changed = []
    kinds = {k for k, *_ in samples} | set(counts)
    for kind in kinds:
        mine = [(round(s, 3), 1 if real else 0, run.pk) for k, s, real, _ in samples if k == kind]
        c = counts.get(kind, {})
        for a, d in _scopes(p):
            row, _ = AutoCheckTuning.objects.get_or_create(agency_id=a, deliverable=d, kind=kind)
            row.right += c.get("right", 0)
            row.wrong += c.get("wrong", 0)
            row.missed += c.get("missed", 0)
            row.subtle += c.get("subtle", 0)
            row.samples = (list(row.samples) + [list(x) for x in mine])[-KEEP:]
            fields = ["right", "wrong", "missed", "subtle", "samples", "updated"]
            if not row.locked and len(row.samples) >= MIN_SAMPLES:
                new = fit([(s[0], s[1]) for s in row.samples], row.factor)
                if abs(new - row.factor) >= 0.03:
                    AutoCheckLesson.objects.create(tuning=row, old_factor=row.factor, new_factor=new, run=run,
                                                   reason=_reason(kind, row.factor, new, row.samples)[:300])
                    changed.append((kind, a, d, row.factor, new))
                    row.factor = new
                    fields.append("factor")
            row.save(update_fields=fields)
    for g, n in notes.items():
        row, _ = AutoCheckTuning.objects.get_or_create(agency=None, deliverable="", kind="note:" + g)
        row.missed += n
        row.save(update_fields=["missed", "updated"])
    total = {key: sum(c.get(key, 0) for c in counts.values()) for key in ("right", "wrong", "missed", "subtle")}
    return {**total, "notes": sum(notes.values()), "changed": len(changed), "from_run": from_run, "run": run.pk,
            "at": timezone.now().isoformat(timespec="seconds"),
            "why": [{"kind": k, "sev": s, "real": real, "why": w} for k, s, real, w in samples][:60]}


# ---------------------------------------------------------------- the daily feedback read
SETTLE = timezone.timedelta(hours=20)     # QC's comments unchanged this long: they've finished with it
LATEST = timezone.timedelta(days=10)      # learn by then at the latest (if QC did anything)
READ_FOR = timezone.timedelta(days=14)    # stop re-reading a version after this
FIRST_READ = timezone.timedelta(hours=12)  # give QC time to review before the first read
EVERY = timezone.timedelta(hours=20)      # about once a day


def _fingerprint(seen):
    import hashlib
    import json
    key = sorted((round(float(c.get("t") or 0), 1), (c.get("text") or "").strip()) for c in seen)
    return hashlib.sha1(json.dumps(key).encode()).hexdigest()


def qc_touched(report, seen):
    """Has QC done anything on this version yet: a note of their own, or one of our flags gone?"""
    if any(not (c.get("ours") or OURS.match(c.get("text") or "")) for c in seen):
        return True
    ours = {(_norm(c.get("text")), round(float(c.get("t") or 0))) for c in seen if c.get("ours") or OURS.match(c.get("text") or "")}
    posted = [p for p in (report or {}).get("posted", []) if p.get("ok")]
    return any(not any(n == _norm(p.get("text")) and abs(t - float(p.get("t") or 0)) <= 1.5 for n, t in ours) for p in posted)


def needs_read(run, now=None):
    """Should a helper re-read the comments on the version this check posted on?"""
    now = now or timezone.now()
    rep = run.report or {}
    if run.status != run.DONE or not run.finished_at or rep.get("feedback") or rep.get("repost"):
        return False
    if not any(p.get("ok") for p in rep.get("posted", [])):
        return False
    if not (now - READ_FOR <= run.finished_at <= now - FIRST_READ):
        return False
    if run.fb_read_at and run.fb_read_at > now - EVERY:
        return False
    # the next version was already checked against this one: that check learnt (or will learn) from it
    return not type(run).objects.filter(project_id=run.project_id, event_id=run.event_id, v1_link=run.v2_link,
                                        status__in=[run.DONE, run.RUNNING, run.QUEUED]).exclude(pk=run.pk).exists()


@transaction.atomic
def read_feedback(run, seen, now=None):
    """A helper re-read QC's comments on this version. Learn once QC has finished with it: they did
    something, and the comments haven't changed for a day (or 10 days have passed). Returns the
    summary when it learnt, else None. Each version teaches once (also not again at the next version)."""
    from .models import AutoCheckRun
    now = now or timezone.now()
    run = AutoCheckRun.objects.select_for_update().get(pk=run.pk)
    rep = dict(run.report or {})
    run.fb_read_at, run.fb_claim_at, run.fb_claim_by = now, None, None
    if rep.get("feedback"):
        run.save(update_fields=["fb_read_at", "fb_claim_at", "fb_claim_by", "updated"])
        return None
    seen = [{"t": c.get("t"), "text": str(c.get("text") or "")[:300], "ours": bool(c.get("ours"))} for c in seen or []][:2000]
    h, fb = _fingerprint(seen), dict(rep.get("fb") or {})
    if fb.get("hash") != h:
        fb.update({"hash": h, "since": now.isoformat(timespec="seconds")})
    fb.update({"reads": int(fb.get("reads") or 0) + 1, "last": now.isoformat(timespec="seconds"), "comments": len(seen)})
    since = timezone.datetime.fromisoformat(fb["since"])
    touched = qc_touched(rep, seen)
    settled = touched and (now - since >= SETTLE or now - run.finished_at >= LATEST)
    summary = None
    if settled:
        samples, counts, notes = judge(rep, {"seen": seen, "followup": []})
        summary = {**_teach(run.project, samples, counts, notes, run, from_run=run.pk), "daily": True}
        rep["feedback"] = summary
    rep["fb"] = fb
    run.report = rep
    run.save(update_fields=["report", "fb_read_at", "fb_claim_at", "fb_claim_by", "updated"])
    return summary


def read_feedback_safely(run, seen):
    try:
        return read_feedback(run, seen)
    except Exception:          # a feedback read must never break anything else
        log.exception("AutoCheck feedback read failed for run %s", run.pk)
        return None


def learn_safely(run):
    try:
        return learn(run)
    except Exception:          # learning must never stop a report from being saved
        log.exception("AutoCheck learning failed for run %s", run.pk)
        return None
