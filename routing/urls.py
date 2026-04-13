from django.urls import path

from routing.views import plan_route_view


urlpatterns = [
    path("plan/", plan_route_view, name="plan-route"),
]
