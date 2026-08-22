"""
Pollinations.ai helper.

Flux (text-to-image, character reference sheets) stays on the original free,
unlimited, no-key endpoint. Kontext (image-to-image, per-scene consistency
edits) requires a free registered API key from enter.pollinations.ai (secret
key, sk_...) and uses a different endpoint/shape: POST with the image
uploaded as multipart bytes, not passed as a URL.

Required environment variable (only for edit_scene / Kontext):
  POLLINATIONS_API_KEY
"""
import os
import time
import requests

IMAGE_BASE = "https://image.pollinations.ai"
GEN_BASE = "https://gen.pollinations.ai"


def _get_with_retry(url: str, params: dict, retries: int = 3, backoff: float = 5.0) -> bytes:
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=120)
            r.raise_for_status()
            if r.headers.get("content-type", "").startswith("image"):
                return r.content
            raise ValueError(f"Unexpected content-type: {r.headers.get('content-type')}, body: {r.text[:300]}")
        except requests.exceptions.HTTPError as e:
            body = e.response.text[:500] if e.response is not None else ""
            last_err = f"{e} | response body: {body}"
            time.sleep(backoff * (attempt + 1))
        except Exception as e:
            last_err = e
            time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"Pollinations (Flux) request failed after {retries} attempts: {last_err}")


def generate_reference(prompt: str, seed: int, width: int = 1024, height: int = 1024) -> bytes:
    url = f"{IMAGE_BASE}/prompt/{requests.utils.quote(prompt)}"
    params = {"model": "flux", "seed": seed, "width": width, "height": height, "nologo": "true"}
    return _get_with_retry(url, params)


def edit_scene(reference_image_bytes: bytes, scene_prompt: str, seed: int,
                retries: int = 3, backoff: float = 5.0) -> bytes:
    api_key = os.environ.get("POLLINATIONS_API_KEY")
    if not api_key:
        raise RuntimeError(
            "POLLINATIONS_API_KEY not set. Register a free secret key at "
            "https://enter.pollinations.ai"
        )

    full_prompt = f"{scene_prompt}. Keep the same character identity, face, and clothing as the reference image."
    url = f"{GEN_BASE}/v1/images/edits"
    headers = {"Authorization": f"Bearer {api_key}"}
    data = {"prompt": full_prompt, "model": "kontext", "seed": str(seed)}
    files = {"image": ("reference.png", reference_image_bytes, "image/png")}

    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, data=data, files=files, timeout=120)
            r.raise_for_status()
            if r.headers.get("content-type", "").startswith("image"):
                return r.content
            raise ValueError(f"Unexpected content-type: {r.headers.get('content-type')}, body: {r.text[:300]}")
        except requests.exceptions.HTTPError as e:
            body = e.response.text[:500] if e.response is not None else ""
            last_err = f"{e} | response body: {body}"
            time.sleep(backoff * (attempt + 1))
        except Exception as e:
            last_err = e
            time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"Pollinations (Kontext) request failed after {retries} attempts: {last_err}")