from django.contrib import admin
from django.db.models import Count, Q
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.html import format_html

from chat.models import KnowledgeEntry, ChatSession, ChatMessage


@admin.register(KnowledgeEntry)
class KnowledgeEntryAdmin(admin.ModelAdmin):
    list_display   = ("title", "category", "source_link", "scraped_at", "content_length")
    list_filter    = ("category",)
    search_fields  = ("title", "keywords", "content")
    readonly_fields = ("scraped_at", "source_url")
    ordering       = ("category", "title")
    list_per_page  = 50

    def source_link(self, obj):
        if obj.source_url:
            return format_html('<a href="{}" target="_blank">🔗 Kaynak</a>', obj.source_url)
        return "-"
    source_link.short_description = "Kaynak URL"

    def content_length(self, obj):
        return f"{len(obj.content):,} karakter"
    content_length.short_description = "İçerik Uzunluğu"


class ChatMessageInline(admin.TabularInline):
    model         = ChatMessage
    extra         = 0
    fields        = ("role", "content", "timestamp", "response_time_ms", "feedback")
    readonly_fields = ("role", "content", "timestamp", "response_time_ms", "feedback")


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display  = ("session_id_short", "created_at", "last_activity", "message_count")
    readonly_fields = ("session_id", "created_at", "last_activity")
    ordering      = ("-last_activity",)
    inlines       = [ChatMessageInline]
    list_per_page = 30

    def session_id_short(self, obj):
        return obj.session_id[:12] + "..."
    session_id_short.short_description = "Session ID"

    def message_count(self, obj):
        return obj.messages.count()
    message_count.short_description = "Mesaj Sayısı"


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display   = ("role", "content_preview", "feedback_badge", "session_short", "timestamp", "response_time_ms")
    list_filter    = ("role", "feedback")
    search_fields  = ("content",)
    readonly_fields = ("session", "role", "timestamp", "response_time_ms", "feedback")
    ordering       = ("-timestamp",)
    list_per_page  = 50

    def get_urls(self):
        return [
            path("feedback-stats/", self.admin_site.admin_view(self.feedback_stats_view), name="chat_feedback_stats"),
        ] + super().get_urls()

    def feedback_stats_view(self, request):
        qs = ChatMessage.objects.filter(role="assistant")
        total    = qs.count()
        up       = qs.filter(feedback="up").count()
        down     = qs.filter(feedback="down").count()
        no_fb    = total - up - down

        downvoted = (
            qs.filter(feedback="down")
            .select_related("session")
            .order_by("-timestamp")[:100]
        )
        pairs = []
        for msg in downvoted:
            q = (
                ChatMessage.objects
                .filter(session=msg.session, role="user", timestamp__lt=msg.timestamp)
                .order_by("-timestamp")
                .first()
            )
            pairs.append({
                "question":  q.content if q else "—",
                "answer":    msg.content,
                "timestamp": msg.timestamp,
                "msg_id":    msg.id,
            })

        context = {
            **self.admin_site.each_context(request),
            "title":    "Feedback İstatistikleri",
            "total":    total,
            "up":       up,
            "down":     down,
            "no_fb":    no_fb,
            "pairs":    pairs,
        }
        return TemplateResponse(request, "admin/chat/feedback_stats.html", context)

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["feedback_stats_url"] = "feedback-stats/"
        return super().changelist_view(request, extra_context=extra_context)

    def content_preview(self, obj):
        return obj.content[:80] + "..." if len(obj.content) > 80 else obj.content
    content_preview.short_description = "İçerik"

    def session_short(self, obj):
        return obj.session.session_id[:8] + "..."
    session_short.short_description = "Session"

    def feedback_badge(self, obj):
        if obj.feedback == "up":
            return format_html('<span style="color:#16a34a;font-size:1.1em">👍</span>')
        if obj.feedback == "down":
            return format_html('<span style="color:#dc2626;font-size:1.1em">👎</span>')
        return format_html('<span style="color:#94a3b8">—</span>')
    feedback_badge.short_description = "Feedback"


# Admin site başlıkları
admin.site.site_header  = "Acıbadem Üniversitesi Chatbot Yönetim Paneli"
admin.site.site_title   = "ACU Chatbot Admin"
admin.site.index_title  = "Hoş Geldiniz"
