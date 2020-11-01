from django.urls import path
from votee import views

urlpatterns = [
    path("e/-/create/", views.ElectionCreate.as_view(), name="election_create"),
    path("e/<slug:election>/", views.ElectionDetail.as_view(), name="election_detail"),
    path(
        "e/<slug:election>/-/admin/",
        views.ElectionAdmin.as_view(),
        name="election_admin",
    ),
    path(
        "e/<slug:election>/<slug:poll>/", views.PollDetail.as_view(), name="poll_detail"
    ),
    path(
        "e/<slug:election>/<slug:poll>/-/admin/",
        views.PollAdmin.as_view(),
        name="poll_admin",
    ),
]
