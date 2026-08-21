"""
Telegram bot helper.
"""
import os
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_message(text: str):
    r = requests.post(f"{API}/sendMessage", json={"chat_id": CHAT_ID, "text": text[:4000]})
    r.raise_for_status()
    return r.json()


def notify_story_for_review(story: dict):
    script = story.get("script") or {}
    body_preview = ""
    if isinstance(script, dict):
        body_preview = (script.get("opening") or "")[:300]
    text = (
        f"New story ready for review\n\n"
        f"Category: {story.get('category')}\n"
        f"Title: {story.get('title')}\n"
        f"Hook: {story.get('hook')}\n"
        f"Retention score: {story.get('retention_score')}\n\n"
        f"Opening (preview):\n{body_preview}\n\n"
        f"story_id: {story.get('story_id')}\n\n"
        f"To approve: set this story's status to approved in Supabase.\n"
        f"To reject: set it to rejected."
    )
    return send_message(text)


def notify_error(workflow_step: str, message: str, story_id: str | None = None):
    text = f"Pipeline error\nStep: {workflow_step}\nStory: {story_id}\n\n{str(message)[:1500]}"
    return send_message(text)


def notify_published(story: dict):
    text = f"Published\n\nTitle: {story.get('title')}\nURL: {story.get('instagram_url')}"
    return send_message(text)