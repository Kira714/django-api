from django.db import models


class FuelStation(models.Model):
    """A fueling station loaded from stations.json; prices synced from EIA API."""

    station_id = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    latitude = models.FloatField()
    longitude = models.FloatField()
    state = models.CharField(max_length=2, blank=True, default="")
    price_per_gallon = models.DecimalField(max_digits=6, decimal_places=3)

    class Meta:
        indexes = [
            # Speed up the bounding-box filter used during route planning.
            models.Index(fields=["latitude", "longitude"]),
        ]
        ordering = ["price_per_gallon"]

    def __str__(self) -> str:
        return f"{self.name} (${self.price_per_gallon}/gal)"
