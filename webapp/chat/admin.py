from django.contrib import admin
from django.utils.html import format_html
from chat.models import KnowledgeEntry, ChatSession, ChatMessage


@admin.register(KnowledgeEntry)
class KnowledgeEntryAdmin(admin.ModelAdmin):
    list_display  = ("title", "category", "source_link", "scraped_at", "content_length")
    list_filter   = ("category",)
    search_fields = ("title", "keywords", "content")
    readonly_fields = ("scraped_at", "source_url")
    ordering      = ("category", "title")
    list_per_page = 50

    def source_link(self, obj):
        if obj.source_url:
            return format_html('<a href="{}" target="_blank">🔗 Kaynak</a>', obj.source_url)
        return "-"
    source_link.short_description = "Kaynak URL"

    def content_length(self, obj):
        return f"{len(obj.content):,} karakter"
    content_length.short_description = "İçerik Uzunluğu"


class ChatMessageInline(admin.TabularInline):
    model  = ChatMessage
    extra  = 0
    fields = ("role", "content", "timestamp", "response_time_ms")
    readonly_fields = ("role", "content", "timestamp", "response_time_ms")


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
    list_display  = ("role", "content_preview", "session_short", "timestamp", "response_time_ms")
    list_filter   = ("role",)
    search_fields = ("content",)
    readonly_fields = ("session", "role", "timestamp", "response_time_ms")
    ordering      = ("-timestamp",)
    list_per_page = 50

    def content_preview(self, obj):
        return obj.content[:80] + "..." if len(obj.content) > 80 else obj.content
    content_preview.short_description = "İçerik"

    def session_short(self, obj):
        return obj.session.session_id[:8] + "..."
    session_short.short_description = "Session"


# Admin site başlıkları
admin.site.site_header  = "Acıbadem Üniversitesi Chatbot Yönetim Paneli"
admin.site.site_title   = "ACU Chatbot Admin"
admin.site.index_title  = "Hoş Geldiniz"
