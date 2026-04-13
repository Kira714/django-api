from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="FuelStation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("station_id", models.CharField(max_length=100, unique=True)),
                ("name", models.CharField(max_length=255)),
                ("latitude", models.FloatField()),
                ("longitude", models.FloatField()),
                (
                    "price_per_gallon",
                    models.DecimalField(decimal_places=3, max_digits=6),
                ),
            ],
            options={
                "ordering": ["price_per_gallon"],
                "indexes": [
                    models.Index(
                        fields=["latitude", "longitude"],
                        name="routing_fue_latitud_idx",
                    )
                ],
            },
        ),
    ]
