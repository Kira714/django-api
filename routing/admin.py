from django.contrib import admin

from routing.models import FuelStation


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = ("station_id", "name", "latitude", "longitude", "price_per_gallon")
    search_fields = ("station_id", "name")
    ordering = ("price_per_gallon",)
    list_per_page = 50
