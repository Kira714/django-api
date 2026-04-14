from django.urls import path

from routing.views import plan_route_eia_view, plan_route_view


urlpatterns = [
    path("plan/", plan_route_view, name="plan-route"),
    path("plan/eia/", plan_route_eia_view, name="plan-route-eia"),
]
