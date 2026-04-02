from django.urls import path
from chat import views

urlpatterns = [
    path("",                        views.index,            name="index"),
    path("api/chat/",               views.send_message,     name="send_message"),
    path("api/stream/",             views.stream_message,   name="stream_message"),
    path("api/feedback/<int:message_id>/", views.message_feedback, name="message_feedback"),
    path("api/sessions/",           views.list_sessions,    name="list_sessions"),
    path("api/sessions/switch/",    views.switch_session,   name="switch_session"),
    path("api/sessions/new/",       views.new_session,      name="new_session"),
    path("chat/clear/",             views.clear_history,    name="clear_history"),
    path("api/status/",             views.api_status,       name="api_status"),
]
