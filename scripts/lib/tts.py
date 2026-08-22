"""
TTS layer with a provider abstraction (architecture doc §6/§14: swap providers
without rebuilding the pipeline). Sarvam is primary while signup credit lasts;
the fallback function is stubbed for when it runs out (~75-200 days at daily
5-min scripts per the architecture doc's estimate — verify against your own
Sarvam dashboard usage).

Required environment variables:
  TTS_PROVIDER    - "sarvam" (default) or "indic_tts"
  SARVAM_API_KEY  - required if TTS_PROVIDER=sarvam
"""
import os
import requests

PROVIDER = os.environ.get("TTS_PROVIDER", "sarvam")


def synthesize(text: str, speaker: str = "pooja", pace: float = 1.0) -> bytes:
    """Returns raw audio bytes (wav) for the given Kannada text."""
    if PROVIDER == "sarvam":
        return _sarvam_tts(text, speaker, pace)
    elif PROVIDER == "indic_tts":
        return _indic_tts_fallback(text, speaker, pace)
    raise ValueError(f"Unknown TTS_PROVIDER: {PROVIDER}")


def _sarvam_tts(text: str, speaker: str, pace: float) -> bytes:
    api_key = os.environ["SARVAM_API_KEY"]
    # Verify current endpoint/payload shape against Sarvam's docs before first
    # run — this is written from documented behavior as of research time and
    # may drift as Sarvam's API evolves.
    r = requests.post(
        "https://api.sarvam.ai/text-to-speech",
        headers={"api-subscription-key": api_key},
        json={
            "inputs": [text],
            "target_language_code": "kn-IN",
            "speaker": speaker,
            "pace": pace,
            "model": "bulbul:v3",
        },
        timeout=120,
    )
    if not r.ok:
        raise RuntimeError(f"Sarvam TTS {r.status_code} error: {r.text[:500]}")
    data = r.json()
    import base64
    return base64.b64decode(data["audios"][0])


def _indic_tts_fallback(text: str, speaker: str, pace: float) -> bytes:
    """Placeholder for the self-hosted AI4Bharat Indic-TTS fallback.
    Since GitHub Actions runners have no persistent GPU, this should call a
    small CPU-friendly Indic-TTS model bundled/installed in the same job, or
    a free-tier hosted inference endpoint if AI4Bharat offers one at build
    time — confirm current options before you need this fallback, don't wait
    until the Sarvam credit actually runs out.
    """
    raise NotImplementedError(
        "Indic-TTS fallback not yet wired up. Implement before the Sarvam "
        "credit is projected to run out (see architecture doc risk list)."
    )
