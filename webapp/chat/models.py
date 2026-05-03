from django.db import models
from django.utils import timezone
from pgvector.django import VectorField


class KnowledgeEntry(models.Model):
    CATEGORY_CHOICES = [
        ("general",       "General Information"),
        ("programs",      "Academic Programs"),
        ("admission",     "Admission & Registration"),
        ("fees",          "Fees & Scholarships"),
        ("campus",        "Campus & Facilities"),
        ("research",      "Research"),
        ("student_life",  "Student Life"),
        ("international", "International Students"),
        ("contact",       "Contact & Location"),
        ("courses",       "Course Catalog"),
    ]

    title      = models.CharField(max_length=300)
    category   = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default="general")
    content    = models.TextField()
    keywords   = models.TextField(blank=True)
    source_url = models.URLField(max_length=500, blank=True, unique=True)
    scraped_at = models.DateTimeField(default=timezone.now)

    # Vector embedding — 768 dimensions (nomic-embed-text)
    embedding  = VectorField(dimensions=768, null=True, blank=True)

    class Meta:
        ordering = ["category", "title"]

    def __str__(self):
        return f"[{self.get_category_display()}] {self.title}"


class ChatSession(models.Model):
    session_id    = models.CharField(max_length=100, unique=True)
    created_at    = models.DateTimeField(default=timezone.now)
    last_activity = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Session {self.session_id[:8]}"


class ChatMessage(models.Model):
    ROLE_CHOICES = [("user", "User"), ("assistant", "Assistant")]

    FEEDBACK_CHOICES = [("up", "up"), ("down", "down")]

    session          = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    role             = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content          = models.TextField()
    category         = models.CharField(max_length=50, blank=True, null=True)
    timestamp        = models.DateTimeField(default=timezone.now)
    response_time_ms = models.IntegerField(null=True, blank=True)
    feedback         = models.CharField(max_length=4, choices=FEEDBACK_CHOICES, null=True, blank=True)

    class Meta:
        ordering = ["timestamp"]

    def __str__(self):
        return f"{self.role}: {self.content[:60]}"
