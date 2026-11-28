"""Camera shake is measured a new way from QC Helper 1.4.0 (wobble around the camera's path, so
gimbal moves and speed ramps no longer count). Limits learnt on the old measure no longer mean
anything: start them again from 1, and say so on the AutoCheck learnt page. A limit a founder
locked is left alone."""
from django.db import migrations

REASON = ("Camera shake is measured a new way (QC Helper 1.4.0): smooth gimbal moves and speed ramps no "
          "longer count as shake. The limit learnt on the old measure was reset to start again.")


def reset(apps, schema_editor):
    Tuning = apps.get_model("ops", "AutoCheckTuning")
    Lesson = apps.get_model("ops", "AutoCheckLesson")
    for row in Tuning.objects.filter(kind="shake", locked=False):
        if abs(row.factor - 1.0) > 0.005 or row.samples:
            if abs(row.factor - 1.0) > 0.005:
                Lesson.objects.create(tuning=row, old_factor=row.factor, new_factor=1.0, reason=REASON)
            row.factor = 1.0
            row.samples = []
            row.save(update_fields=["factor", "samples"])


class Migration(migrations.Migration):
    dependencies = [("ops", "0029_autocheck_learning")]
    operations = [migrations.RunPython(reset, migrations.RunPython.noop)]
