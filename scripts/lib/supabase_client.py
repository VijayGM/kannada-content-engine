"""
Minimal Supabase REST helper.
Every pipeline script talks to Supabase over its PostgREST API rather than
a DB driver, so this file has zero external dependencies beyond `requests`
(already available on GitHub-hosted runners, or installed via requirements.txt).

Required environment variables (set as GitHub Actions Secrets, never committed):
  SUPABASE_URL  - e.g. https://xxxx.supabase.co
  SUPABASE_KEY  - service_role key (server-side only, never exposed to a client)
"""
import os
import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def select(table: str, params: dict | None = None):
    """GET rows. params uses PostgREST filter syntax, e.g. {'status': 'eq.approved'}."""
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=_HEADERS, params=params or {})
    r.raise_for_status()
    return r.json()


def insert(table: str, row: dict):
    """POST a single row, returns the inserted row (Prefer: return=representation)."""
    headers = {**_HEADERS, "Prefer": "return=representation"}
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=headers, json=row)
    r.raise_for_status()
    return r.json()[0]


def update(table: str, match: dict, patch: dict):
    """PATCH rows matching `match` (e.g. {'story_id': 'eq.<uuid>'}) with `patch` fields."""
    headers = {**_HEADERS, "Prefer": "return=representation"}
    r = requests.patch(f"{SUPABASE_URL}/rest/v1/{table}", headers=headers, params=match, json=patch)
    r.raise_for_status()
    return r.json()


def log_error(story_id: str | None, workflow_step: str, message: str, scene_id: str | None = None):
    """Write to errors_and_retries instead of only relying on GitHub Actions' own log output —
    see the architecture doc's 'ephemeral compute debugging' risk note."""
    insert("errors_and_retries", {
        "story_id": story_id,
        "scene_id": scene_id,
        "workflow_step": workflow_step,
        "error_message": str(message)[:2000],
    })


def log_cost(story_id: str | None, provider: str, operation: str, units: float, cost_inr: float, succeeded: bool = True):
    insert("api_costs", {
        "story_id": story_id,
        "provider": provider,
        "operation": operation,
        "units": units,
        "cost_inr": cost_inr,
        "succeeded": succeeded,
    })


def upload_to_storage(bucket: str, path: str, file_bytes: bytes, content_type: str) -> str:
    """Upload bytes to Supabase Storage, return the public URL.
    Bucket must already exist and be set to public (Storage tab in Supabase dashboard)."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    r = requests.post(f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}", headers=headers, data=file_bytes)
    r.raise_for_status()
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"
