"""
Pollinations.ai helper — free image generation, no API key required for
basic use (register for higher rate limits / no watermark, see README).

Implements the two-stage consistency workflow from the architecture doc §6:
  1. generate_reference() — one canonical image per character (text-to-image, Flux)
  2. edit_scene()         — Kontext image-to-image edit, preserves identity
                             while changing pose/background/action

Known caveat (documented, not hidden): the kontext endpoint has had
intermittent bugs reported. Both functions retry with backoff.
"""
import time
import requests

BASE = "https://image.pollinations.ai"


def _get_with_retry(url: str, params: dict, retries: int = 3, backoff: float = 5.0) -> bytes:
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=120)
            r.raise_for_status()
            if r.headers.get("content-type", "").startswith("image"):
                return r.content
            raise ValueError(f"Unexpected content-type: {r.headers.get('content-type')}")
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"Pollinations request failed after {retries} attempts: {last_err}")


def generate_reference(prompt: str, seed: int, width: int = 1024, height: int = 1024) -> bytes:
    """Text-to-image via Flux. Returns raw image bytes."""
    url = f"{BASE}/prompt/{requests.utils.quote(prompt)}"
    params = {"model": "flux", "seed": seed, "width": width, "height": height, "nologo": "true"}
    return _get_with_retry(url, params)


def edit_scene(reference_image_url: str, scene_prompt: str, seed: int) -> bytes:
    """Image-to-image via Kontext: preserves character identity from
    reference_image_url while applying scene_prompt (new pose/background/action)."""
    full_prompt = f"{scene_prompt}. Keep the same character identity, face, and clothing as the reference image."
    url = f"{BASE}/prompt/{requests.utils.quote(full_prompt)}"
    params = {
        "model": "kontext",
        "image": reference_image_url,
        "seed": seed,
        "nologo": "true",
    }
    return _get_with_retry(url, params)
