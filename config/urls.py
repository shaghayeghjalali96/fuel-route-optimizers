from django.urls import path

from fuelroute.views import RouteFuelView, health, map_page

urlpatterns = [
    path("", map_page, name="map"),
    path("health/", health, name="health"),
    path("api/route/", RouteFuelView.as_view(), name="route-fuel"),
]
