import json
from datetime import timedelta
from django import template
from django.utils import timezone
from django.db.models import Count
from django.db.models.functions import TruncDate

from chat.models import ChatSession, ChatMessage, KnowledgeEntry

register = template.Library()

@register.inclusion_tag('admin/chat/dashboard_charts.html', takes_context=True)
def render_dashboard(context):
    # 1. Daily Sessions (last 7 days)
    seven_days_ago = timezone.now() - timedelta(days=6)
    sessions_qs = (
        ChatSession.objects
        .filter(created_at__gte=seven_days_ago)
        .annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(count=Count('id'))
        .order_by('date')
    )
    
    # Fill in missing days with 0
    daily_sessions_map = {item['date'].strftime('%Y-%m-%d'): item['count'] for item in sessions_qs if item['date']}
    
    dates = []
    session_counts = []
    for i in range(7):
        d = (timezone.now() - timedelta(days=6-i)).date()
        d_str = d.strftime('%Y-%m-%d')
        dates.append(d_str)
        session_counts.append(daily_sessions_map.get(d_str, 0))

    # 2. Feedback Ratio (Upvotes vs Downvotes)
    feedback_qs = (
        ChatMessage.objects
        .filter(role='assistant', feedback__in=['up', 'down'])
        .values('feedback')
        .annotate(count=Count('id'))
    )
    feedback_counts = {'up': 0, 'down': 0}
    for f in feedback_qs:
        feedback_counts[f['feedback']] = f['count']
    
    # 3. Top Categories
    category_qs = (
        ChatMessage.objects
        .filter(role='user')
        .exclude(category__isnull=True)
        .exclude(category="")
        .values('category')
        .annotate(count=Count('id'))
        .order_by('-count')[:5]
    )
    
    # Map category codes to display names
    cat_dict = dict(KnowledgeEntry.CATEGORY_CHOICES)
    categories = []
    category_counts = []
    for c in category_qs:
        cat_key = c['category']
        display_name = cat_dict.get(cat_key, cat_key)
        categories.append(display_name)
        category_counts.append(c['count'])

    return {
        'dates_json': json.dumps(dates),
        'session_counts_json': json.dumps(session_counts),
        'feedback_up': feedback_counts['up'],
        'feedback_down': feedback_counts['down'],
        'categories_json': json.dumps(categories),
        'category_counts_json': json.dumps(category_counts),
    }
