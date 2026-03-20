from django.urls import path
from chat import views

urlpatterns = [
    path("",              views.index,         name="index"),
    path("api/chat/",     views.send_message,  name="send_message"),
    path("chat/clear/",   views.clear_history, name="clear_history"),
    path("api/status/",   views.api_status,    name="api_status"),
]
