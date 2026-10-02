"""Pollinations.ai media helpers.

Current API: https://gen.pollinations.ai
Generation requires a Pollinations API key (normally an ``sk_`` secret key for
server-side GitHub Actions). Billing/auth failures are fail-fast; transient
429/5xx failures may be retried.
"""
import os
import time
import requests

GEN_BASE = os.environ.get("POLLINATIONS_BASE_URL", "https://gen.pollinations.ai").rstrip("/")


class PollinationsBillingError(RuntimeError):
    """The Pollinations account/key cannot currently pay for the request."""


class PollinationsAuthError(RuntimeError):
    """The Pollinations API key is missing or invalid."""


def _headers() -> dict:
    api_key = os.environ.get("POLLINATIONS_API_KEY")
    if not api_key:
        raise PollinationsAuthError(
            "POLLINATIONS_API_KEY is not set. Create a Pollinations secret key "
            "at enter.pollinations.ai and add it to GitHub Actions secrets."
        )
    return {"Authorization": f"Bearer {api_key}"}


def _raise_http(r: requests.Response, operation: str) -> None:
    status = r.status_code
    body = r.text[:500] if r.text else ""
    if status == 402:
        raise PollinationsBillingError(
            f"Pollinations billing/payment required during {operation} (HTTP 402). "
            "Add/restore Pollen balance for the API key; retrying will not fix a 402. "
            f"Response: {body}"
        )
    if status in (401, 403):
        raise PollinationsAuthError(
            f"Pollinations authentication/permission failed during {operation} "
            f"(HTTP {status}). Check POLLINATIONS_API_KEY. Response: {body}"
        )
    r.raise_for_status()


def _get_media(url: str, params: dict, expected: str, operation: str,
               retries: int = 3, backoff: float = 5.0) -> bytes:
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=_headers(), timeout=300)
            if r.status_code in (402, 401, 403):
                _raise_http(r, operation)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                body = r.text[:300]
                last_err = f"HTTP {r.status_code}: {body}"
                if attempt < retries - 1:
                    time.sleep(backoff * (attempt + 1))
                    continue
                _raise_http(r, operation)
            _raise_http(r, operation)
            content_type = r.headers.get("content-type", "").lower()
            if content_type.startswith(expected):
                return r.content
            # Some gateways omit a useful content-type. Accept a non-empty body
            # only when the expected media signature is present.
            if expected == "video" and r.content[:4] == b"\x00\x00\x00\x18":
                return r.content
            raise RuntimeError(
                f"Pollinations returned unexpected content-type for {operation}: "
                f"{content_type}; body={r.text[:300]}"
            )
        except (PollinationsBillingError, PollinationsAuthError):
            raise
        except requests.RequestException as e:
            last_err = str(e)
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
            else:
                break
    raise RuntimeError(f"Pollinations {operation} failed after {retries} attempts: {last_err}")


DEFAULT_NEGATIVE_PROMPT = (
    "blurry, distorted, disfigured, deformed, extra limbs, extra fingers, "
    "missing fingers, bad anatomy, bad proportions, mutated hands, watermark, "
    "signature, text, low quality, low resolution, jpeg artifacts, grainy, "
    "duplicate, cropped, out of frame"
)


def generate_reference(prompt: str, seed: int, width: int = 1024, height: int = 1024,
                       negative_prompt: str = DEFAULT_NEGATIVE_PROMPT) -> bytes:
    """Generate a character/reference image using the current Pollinations image API."""
    quality_boost = "highly detailed, clean linework, professional character illustration, sharp focus"
    full_prompt = f"{prompt}. {quality_boost}"
    url = f"{GEN_BASE}/image/{requests.utils.quote(full_prompt, safe='')}"
    params = {
        "model": os.environ.get("POLLINATIONS_IMAGE_MODEL", "flux"),
        "seed": seed, "width": width, "height": height,
        "nologo": "true", "negative_prompt": negative_prompt,
    }
    return _get_media(url, params, "image", "image generation")


def edit_scene(reference_image_bytes: bytes, scene_prompt: str, seed: int,
               retries: int = 3, backoff: float = 5.0) -> bytes:
    """Edit a reference image while preserving identity."""
    full_prompt = f"{scene_prompt}. Keep the same character identity, face, and clothing as the reference image."
    url = f"{GEN_BASE}/v1/images/edits"
    data = {"prompt": full_prompt, "model": os.environ.get("POLLINATIONS_EDIT_MODEL", "black-forest-labs/flux.1-kontext-pro"), "seed": str(seed)}
    files = {"image": ("reference.png", reference_image_bytes, "image/png")}

    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=_headers(), data=data, files=files, timeout=300)
            if r.status_code in (402, 401, 403):
                _raise_http(r, "image edit")
            if r.status_code == 429 or 500 <= r.status_code < 600:
                last_err = f"HTTP {r.status_code}: {r.text[:300]}"
                if attempt < retries - 1:
                    time.sleep(backoff * (attempt + 1))
                    continue
            _raise_http(r, "image edit")
            content_type = r.headers.get("content-type", "").lower()
            if content_type.startswith("image"):
                return r.content
            raise RuntimeError(f"Unexpected content-type from image edit: {content_type}; body={r.text[:300]}")
        except (PollinationsBillingError, PollinationsAuthError):
            raise
        except requests.RequestException as e:
            last_err = str(e)
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"Pollinations image edit failed after {retries} attempts: {last_err}")


def generate_video(prompt: str, duration: int = 4, model: str | None = None,
                   start_image_url: str | None = None,
                   reference_image_urls: list[str] | None = None,
                   retries: int = 2, backoff: float = 5.0) -> bytes:
    """Generate a short cinematic MP4 from text and optional image references."""
    url = f"{GEN_BASE}/video/{requests.utils.quote(prompt, safe='')}"
    params = {
        "model": model or os.environ.get("POLLINATIONS_VIDEO_MODEL", "google/veo-3.1-fast"),
        "duration": int(duration),
    }
    if start_image_url:
        params["image"] = start_image_url
    if reference_image_urls:
        params["reference_images"] = reference_image_urls
    return _get_media(url, params, "video", "video generation", retries=retries, backoff=backoff)
