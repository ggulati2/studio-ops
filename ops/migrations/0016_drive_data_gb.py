from decimal import Decimal

from django.db import migrations, models


def tb_to_gb(apps, schema_editor):
    HardDrive = apps.get_model("ops", "HardDrive")
    for d in HardDrive.objects.exclude(data_tb__isnull=True):
        d.data_gb = (d.data_tb * Decimal(1000)).quantize(Decimal("0.1"))
        d.save(update_fields=["data_gb"])


def gb_to_tb(apps, schema_editor):
    HardDrive = apps.get_model("ops", "HardDrive")
    for d in HardDrive.objects.exclude(data_gb__isnull=True):
        d.data_tb = min((d.data_gb / Decimal(1000)).quantize(Decimal("0.1")), Decimal("9999.9"))
        d.save(update_fields=["data_tb"])


class Migration(migrations.Migration):
    """'Data on it (TB)' becomes 'Data Available (GB)'; existing values are converted (1 TB = 1000 GB)."""

    dependencies = [("ops", "0015_drive_folders")]

    operations = [
        migrations.AddField(
            model_name="harddrive", name="data_gb",
            field=models.DecimalField(blank=True, decimal_places=1, max_digits=8, null=True, verbose_name="Data Available (GB)"),
        ),
        migrations.RunPython(tb_to_gb, gb_to_tb),
        migrations.RemoveField(model_name="harddrive", name="data_tb"),
        migrations.AlterField(
            model_name="harddrive", name="capacity_tb",
            field=models.DecimalField(blank=True, decimal_places=1, max_digits=5, null=True, verbose_name="Capacity (TB)"),
        ),
    ]
