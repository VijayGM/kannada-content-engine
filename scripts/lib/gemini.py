"""
Thin wrapper around the Gemini API free tier (Flash), used for all LLM steps.
"""
import os
import json
import time
import requests

API_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def generate(prompt: str, json_mode: bool = False, temperature: float = 0.9,
             retries: int = 3, backoff: float = 10.0) -> str:
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"

    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(f"{ENDPOINT}?key={API_KEY}", json=body, timeout=120)
            r.raise_for_status()
            data = r.json()
            if "candidates" not in data or not data["candidates"]:
                block_reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
                raise RuntimeError(
                    f"Gemini returned no candidates (likely safety-filtered). "
                    f"blockReason={block_reason}. Full response: {json.dumps(data)[:500]}"
                )
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in (503, 429) and attempt < retries - 1:
                wait = backoff * (attempt + 1)
                print(f"Gemini {status}, retrying in {wait:.0f}s (attempt {attempt + 1}/{retries})")
                time.sleep(wait)
                last_err = e
                continue
            raise
    raise last_err


def generate_json(prompt: str, temperature: float = 0.9) -> dict:
    text = generate(prompt, json_mode=True, temperature=temperature)
    return json.loads(text)
