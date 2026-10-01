from django.db import models


class FuelStation(models.Model):
    """Truck stop with retail fuel price and cached coordinates."""

    opis_id = models.PositiveIntegerField(db_index=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=512)
    city = models.CharField(max_length=128, db_index=True)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    retail_price = models.DecimalField(max_digits=8, decimal_places=4)

    # Populated from City+State lookup (CSV has no lat/lng — only exit-style addresses)
    latitude = models.FloatField(null=True, blank=True, db_index=True)
    longitude = models.FloatField(null=True, blank=True, db_index=True)
    geocode_source = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["state", "city"]),
            models.Index(fields=["latitude", "longitude"]),
            models.Index(fields=["retail_price"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state}) ${self.retail_price}"
