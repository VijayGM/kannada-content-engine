"""
Workflow 6: Analytics & Feedback Loop.
Triggered daily by .github/workflows/04-analytics.yml, runs against every
story published in the last 30 days (Instagram Insights are most useful in
the first days post-publish, but keep a longer window for slower-burn posts).
"""
import os
import requests

from lib import supabase_client as db

GRAPH_API = "https://graph.facebook.com/v21.0"
ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN")


def recently_published_stories():
    return db.select("stories", {
        "status": "eq.published",
        "select": "story_id,instagram_media_id,title",
        "order": "publication_date.desc",
        "limit": "60",
    })


def fetch_insights(media_id: str) -> dict:
    r = requests.get(
        f"{GRAPH_API}/{media_id}/insights",
        params={
            # Verify current metric names against Meta's docs before first run —
            # these change with API versions more often than most of this stack.
            "metric": "reach,likes,comments,shares,saved,plays,total_interactions",
            "access_token": ACCESS_TOKEN,
        },
        timeout=30,
    )
    r.raise_for_status()
    out = {}
    for item in r.json().get("data", []):
        out[item["name"]] = item["values"][0]["value"]
    return out


def main():
    for story in recently_published_stories():
        if not story.get("instagram_media_id"):
            continue
        try:
            insights = fetch_insights(story["instagram_media_id"])
        except Exception as e:  # noqa: BLE001
            db.log_error(story["story_id"], "pull_analytics", str(e))
            continue

        snapshot = {
            "story_id": story["story_id"],
            "views": insights.get("plays"),
            "likes": insights.get("likes"),
            "comments": insights.get("comments"),
            "shares": insights.get("shares"),
            "saves": insights.get("saved"),
        }
        db.insert("performance_snapshots", snapshot)
        db.update("stories", {"story_id": f"eq.{story['story_id']}"}, {
            k: v for k, v in snapshot.items() if k != "story_id"
        })
        print(f"Updated performance for {story['title']}: {snapshot}")


if __name__ == "__main__":
    main()
