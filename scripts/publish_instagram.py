"""
Workflow 5: Publishing.
Triggered by .github/workflows/03-publish.yml once a story's status is
'produced'. Publishes to Instagram via the official Graph API — see
docs/meta-app-review-checklist.md for the one-time setup this depends on.

Path B (confirmed in the architecture doc): full 5-minute videos publish as
regular video posts, not to the Reels tab — the Graph API only gives
Reels-tab placement to clips of 5-90 seconds. This script uses the standard
video container flow accordingly.

Required environment variables:
  IG_BUSINESS_ACCOUNT_ID
  IG_ACCESS_TOKEN     - long-lived token, see README for refresh notes
  GEMINI_API_KEY      - reused here for caption/hashtag generation
"""
import os
import time
import requests

from lib import gemini, supabase_client as db, telegram

GRAPH_API = "https://graph.facebook.com/v21.0"  # bump version periodically; verify against Meta's current docs
IG_ACCOUNT_ID = os.environ.get("IG_BUSINESS_ACCOUNT_ID")
ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN")


def find_produced_story():
    rows = db.select("stories", {"status": "eq.produced", "limit": "1"})
    return rows[0] if rows else None


def generate_caption_and_hashtags(story: dict) -> tuple[str, list[str]]:
    prompt = f"""Write an Instagram caption in Kannada for this story video.
Category: {story['category']}
Title: {story['title']}
Hook: {story['hook']}

Include a natural call-to-action matching the story (question, tag-a-friend,
follow prompt, etc. — pick what fits, don't default to the same CTA every time).
Keep the caption concise. Then suggest 8-12 relevant Kannada/English hashtags
(not hundreds — a controlled, relevant set).

Return ONLY JSON: {{"caption": "...", "hashtags": ["...", "..."]}}
"""
    result = gemini.generate_json(prompt, temperature=0.7)
    return result["caption"], result["hashtags"]


def create_container(video_url: str, caption: str) -> str:
    r = requests.post(
        f"{GRAPH_API}/{IG_ACCOUNT_ID}/media",
        data={
            "video_url": video_url,
            "caption": caption,
            # Standard video post, not media_type=REELS — see docstring:
            # Path B means 5-minute videos don't qualify for Reels-tab placement anyway.
            "media_type": "VIDEO",
            "access_token": ACCESS_TOKEN,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["id"]


def wait_for_container(container_id: str, timeout_s: int = 600, poll_s: int = 15) -> None:
    elapsed = 0
    while elapsed < timeout_s:
        r = requests.get(
            f"{GRAPH_API}/{container_id}",
            params={"fields": "status_code", "access_token": ACCESS_TOKEN},
            timeout=30,
        )
        r.raise_for_status()
        status = r.json().get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Container {container_id} failed processing")
        time.sleep(poll_s)
        elapsed += poll_s
    raise TimeoutError(f"Container {container_id} did not finish within {timeout_s}s")


def publish_container(container_id: str) -> str:
    r = requests.post(
        f"{GRAPH_API}/{IG_ACCOUNT_ID}/media_publish",
        data={"creation_id": container_id, "access_token": ACCESS_TOKEN},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["id"]


def get_permalink(media_id: str) -> str:
    r = requests.get(
        f"{GRAPH_API}/{media_id}",
        params={"fields": "permalink", "access_token": ACCESS_TOKEN},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("permalink", "")


def main():
    if not IG_ACCOUNT_ID or not ACCESS_TOKEN:
        print("IG_BUSINESS_ACCOUNT_ID or IG_ACCESS_TOKEN not set — skipping "
              "publish. This is expected until Meta App Review is complete.")
        return

    story = find_produced_story()

    if not story:
        print("No produced stories waiting to publish. Exiting cleanly.")
        return

    story_id = story["story_id"]
    caption, hashtags = generate_caption_and_hashtags(story)
    full_caption = caption + "\n\n" + " ".join(f"#{h.lstrip('#')}" for h in hashtags)

    container_id = create_container(story["final_video_url"], full_caption)
    db.insert("publications", {"story_id": story_id, "container_id": container_id})

    wait_for_container(container_id)
    media_id = publish_container(container_id)
    permalink = get_permalink(media_id)

    db.update("stories", {"story_id": f"eq.{story_id}"}, {
        "status": "published",
        "instagram_media_id": media_id,
        "instagram_url": permalink,
        "caption": full_caption,
        "hashtags": hashtags,
        "publication_date": "now()",
    })
    db.update("publications", {"container_id": f"eq.{container_id}"}, {
        "instagram_media_id": media_id, "instagram_url": permalink, "status": "published",
    })

    story["instagram_url"] = permalink
    telegram.notify_published(story)
    print(f"Published story {story_id}: {permalink}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        telegram.notify_error("publish_instagram", str(e))
        raise
