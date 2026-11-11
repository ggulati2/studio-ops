from decimal import Decimal

from django.db import migrations


def fix(apps, schema_editor):
    """0016 multiplied the old 'Data on it (TB)' by 1000. Some drives had their data typed in
    GB already (e.g. 106 for 106 GB), which became 106000 GB. A value that is more than the
    drive can hold (or over 30 TB when the size isn't known) was one of those: divide it back."""
    HardDrive = apps.get_model("ops", "HardDrive")
    for d in HardDrive.objects.exclude(data_gb__isnull=True):
        limit = d.capacity_tb * 1000 if d.capacity_tb else Decimal(30000)
        if d.data_gb > limit:
            d.data_gb = (d.data_gb / 1000).quantize(Decimal("0.1"))
            d.save(update_fields=["data_gb"])


class Migration(migrations.Migration):
    dependencies = [("ops", "0016_drive_data_gb")]
    operations = [migrations.RunPython(fix, migrations.RunPython.noop)]
