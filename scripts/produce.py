"""
Workflow 3 + 4: Production + Quality Control.

Triggered by .github/workflows/02-check-approval-and-produce.yml once a
story's status is 'approved' in Supabase.

Steps:
  1. Scene breakdown (Gemini) from the approved script
  2. Character reference images (Pollinations) — once per character, cached
  3. Per-scene images (Pollinations) — identity-preserving edits of the reference
  4. Per-scene voice (Sarvam TTS)
  5. Assemble:
       - Ken Burns pan/zoom per image
       - synced to audio duration
       - Kannada subtitles burned in using UTF-8 ASS + complex shaping
  6. Basic QC checks
  7. Upload final video to Supabase Storage
  8. Update story status

This entire script runs inside a single GitHub Actions job.
FFmpeg runs on the GitHub Actions runner.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid

import requests

from lib import gemini, supabase_client as db, pollinations, tts, telegram
from lib.kannada_text import sanitize_and_validate


STORAGE_BUCKET = "content-engine-media"


# ---------------------------------------------------------------------------
# STORY
# ---------------------------------------------------------------------------

def find_approved_story():
    rows = db.select(
        "stories",
        {
            "status": "eq.approved",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# CHARACTER MANAGEMENT
# ---------------------------------------------------------------------------

def get_or_create_character(
    name: str,
    category: str,
    script_context: str = "",
) -> dict:
    """
    Return an existing character if available.

    IMPORTANT:
    We intentionally do NOT trust reference_image_url here.
    The URL may point to a deleted Supabase object.

    Reference validation/regeneration is handled by
    ensure_character_reference().
    """

    existing = db.select(
        "characters",
        {
            "name": f"eq.{name}",
            "limit": "1",
        },
    )

    if existing:
        return existing[0]

    # Generate detailed character profile via Gemini.
    profile_prompt = f"""Create a detailed visual profile for this character:

Name/Role: {name}
Story category: {category}
Context from script: {
    script_context[:500] if script_context else "No additional context"
}

Return JSON with keys:
"age_range" (e.g., "40s", "young adult"),
"physical_features" (hair, build, distinctive features),
"clothing" (typical outfit for this character),
"personality_traits" (2-3 traits that affect visual expression),
"visual_style" (art style description for consistency)
"""

    profile = gemini.generate_json(
        profile_prompt,
        temperature=0.5,
    )

    identity_descriptor = (
        f"{name}, "
        f"{profile.get('age_range', 'adult')}, "
        f"{profile.get('physical_features', '')}, "
        f"wearing {profile.get('clothing', 'simple clothing')}. "
        f"Style: {profile.get('visual_style', 'Flat 2D illustrated, warm colors')}. "
        f"Personality: {profile.get('personality_traits', '')}."
    )

    reference_prompt = (
        f"{identity_descriptor} "
        "Character design sheet, front-facing view, "
        "neutral expression, full body visible, plain background, "
        "consistent proportions."
    )

    seed = int(
        hashlib.md5(name.encode("utf-8")).hexdigest(),
        16,
    ) % (10**6)

    img_bytes = pollinations.generate_reference(
        reference_prompt,
        seed=seed,
    )

    path = f"characters/{uuid.uuid4()}.png"

    url = db.upload_to_storage(
        STORAGE_BUCKET,
        path,
        img_bytes,
        "image/png",
    )

    return db.insert(
        "characters",
        {
            "name": name,
            "prompt_template": identity_descriptor,
            "reference_image_url": url,
            "seed": seed,
            "visual_profile": profile,
        },
    )


def _save_character_reference(char: dict, img_bytes: bytes) -> str:
    """
    Upload a regenerated character reference and update the existing
    character row.

    We update the existing record instead of creating a duplicate
    character.
    """

    new_path = f"characters/{uuid.uuid4()}.png"

    new_url = db.upload_to_storage(
        STORAGE_BUCKET,
        new_path,
        img_bytes,
        "image/png",
    )

    character_id = char.get("character_id")

    if character_id:
        db.update(
            "characters",
            {
                "character_id": f"eq.{character_id}",
            },
            {
                "reference_image_url": new_url,
            },
        )
    else:
        # Fallback for an unexpected DB response that does not contain
        # character_id.
        db.update(
            "characters",
            {
                "name": f"eq.{char['name']}",
            },
            {
                "reference_image_url": new_url,
            },
        )

    # Keep the in-memory object synchronized too.
    char["reference_image_url"] = new_url

    print(
        f"CHARACTER REFERENCE SAVED: {char['name']} -> {new_url}"
    )

    return new_url


def _generate_character_reference(char: dict) -> bytes:
    """
    Generate a fresh reference image for an existing character.
    """

    reference_prompt = (
        f"{char['prompt_template']} "
        "Character design sheet, front-facing view, "
        "neutral expression, full body visible, plain background, "
        "consistent proportions."
    )

    seed = char.get("seed")

    if seed is None:
        seed = int(
            hashlib.md5(
                char["name"].encode("utf-8")
            ).hexdigest(),
            16,
        ) % (10**6)

        char["seed"] = seed

    print(
        f"REGENERATING CHARACTER REFERENCE: "
        f"{char['name']} | seed={seed}"
    )

    return pollinations.generate_reference(
        reference_prompt,
        seed=seed,
    )


def ensure_character_reference(char: dict) -> bytes:
    """
    Download and validate a character reference image.

    If the Supabase object is missing (for example NoSuchKey / 404),
    automatically regenerate it with Pollinations, upload the new
    reference, update the existing characters row, and return the
    regenerated bytes.

    This prevents stale reference_image_url values from breaking
    production.
    """

    ref_url = char.get("reference_image_url")

    if not ref_url:
        print(
            f"CHARACTER REFERENCE URL MISSING: {char['name']}"
        )

        img_bytes = _generate_character_reference(char)
        _save_character_reference(char, img_bytes)

        return img_bytes

    print(
        f"CHECKING CHARACTER REFERENCE: "
        f"{char['name']}"
    )
    print(
        f"CHARACTER REFERENCE URL: {ref_url}"
    )

    try:
        response = requests.get(
            ref_url,
            timeout=60,
        )
    except requests.RequestException as exc:
        print(
            f"CHARACTER REFERENCE DOWNLOAD ERROR: "
            f"{char['name']}: {exc}"
        )
        raise

    if response.ok and response.content:
        print(
            f"CHARACTER REFERENCE OK: "
            f"{char['name']} "
            f"({len(response.content)} bytes)"
        )

        return response.content

    response_body = response.text[:1000]

    print(
        f"CHARACTER REFERENCE MISSING/INVALID: "
        f"{char['name']}"
    )
    print(
        f"HTTP STATUS: {response.status_code}"
    )
    print(
        f"RESPONSE BODY: {response_body}"
    )

    # The important Supabase failure we want to self-heal:
    #
    # HTTP 400
    # {"statusCode":"404","error":"not_found",
    #  "message":"Object not found","code":"NoSuchKey"}
    #
    # Also handle ordinary HTTP 404.
    is_missing_object = (
        response.status_code == 404
        or "NoSuchKey" in response_body
        or '"not_found"' in response_body
        or '"statusCode":"404"' in response_body
    )

    if not is_missing_object:
        raise RuntimeError(
            f"Character reference download failed for "
            f"{char['name']}: HTTP {response.status_code} "
            f"{response_body}"
        )

    print(
        f"CHARACTER REFERENCE MISSING FROM STORAGE. "
        f"REGENERATING: {char['name']}"
    )

    img_bytes = _generate_character_reference(char)

    _save_character_reference(
        char,
        img_bytes,
    )

    return img_bytes


# ---------------------------------------------------------------------------
# SCENE BREAKDOWN
# ---------------------------------------------------------------------------

def scene_breakdown(story: dict) -> list[dict]:
    script = story["script"]

    narration_segments = []

    if script.get("opening_hook"):
        narration_segments.append(
            sanitize_and_validate(
                script["opening_hook"]
            )
        )

    narration_segments.extend(
        sanitize_and_validate(body)
        for body in script.get("body_beats", [])
    )

    if script.get("ending"):
        narration_segments.append(
            sanitize_and_validate(
                script["ending"]
            )
        )

    prompt = f"""You are given a Kannada video script broken into narration segments.

For each segment, provide ONLY a visual description (in English)
and list which characters appear.

Narration segments (JSON array):
{json.dumps(narration_segments, ensure_ascii=False)}

Characters in this story:
{json.dumps(script.get('characters', []), ensure_ascii=False)}

Return ONLY a JSON array of objects with keys:

"scene_number" (int, starting from 1),
"visual_description" (English, describing the visual: setting, action, mood, lighting),
"characters_present" (array of character names from the story that appear in this scene).

IMPORTANT:
Do NOT modify or rewrite the narration text.
It will be preserved separately.
"""

    visual_data = gemini.generate_json(
        prompt,
        temperature=0.4,
    )

    scenes = []

    for i, segment in enumerate(narration_segments):

        visual = (
            visual_data[i]
            if isinstance(visual_data, list)
            and i < len(visual_data)
            else {}
        )

        scenes.append(
            {
                "scene_number": i + 1,
                "narration_text": segment,
                "visual_description": visual.get(
                    "visual_description",
                    f"Scene {i + 1}",
                ),
                "characters_present": visual.get(
                    "characters_present",
                    [],
                ),
            }
        )

    return scenes


# ---------------------------------------------------------------------------
# SCENE ASSET BUILDING
# ---------------------------------------------------------------------------

def build_scene_assets(
    story_id: str,
    category: str,
    scenes: list[dict],
    story: dict,
) -> list[dict]:

    built = []

    # Cache character reference bytes during this production run.
    #
    # This prevents downloading the same reference image repeatedly when
    # the same character appears in multiple scenes.
    character_reference_cache = {}

    for sc in scenes:

        scene_row = db.insert(
            "scenes",
            {
                "story_id": story_id,
                "scene_number": sc["scene_number"],
                "description": sc["visual_description"],
            },
        )

        # ---------------------------------------------------------------
        # IMAGE
        # ---------------------------------------------------------------

        try:

            if sc.get("characters_present"):

                character_name = sc["characters_present"][0]

                char = get_or_create_character(
                    character_name,
                    category,
                    script_context=json.dumps(
                        story.get("script", {}),
                        ensure_ascii=False,
                    ),
                )

                combined_prompt = (
                    f"{char['prompt_template']}. "
                    f"Scene: {sc['visual_description']}"
                )

                # Use a stable cache key.
                character_key = (
                    char.get("character_id")
                    or char.get("name")
                )

                if character_key not in character_reference_cache:

                    character_reference_cache[
                        character_key
                    ] = ensure_character_reference(char)

                reference_image_bytes = (
                    character_reference_cache[
                        character_key
                    ]
                )

                # Try identity-preserving image edit first.
                try:

                    img_bytes = pollinations.edit_scene(
                        reference_image_bytes=reference_image_bytes,
                        scene_prompt=combined_prompt,
                        seed=char.get("seed"),
                    )

                except RuntimeError as exc:

                    error_text = str(exc)

                    if (
                        "402" in error_text
                        or "PAYMENT_REQUIRED" in error_text
                    ):

                        print(
                            "Kontext unavailable "
                            "(no credits). "
                            "Falling back to Flux generation."
                        )

                        print(
                            f"Pollinations error: {error_text}"
                        )

                        img_bytes = (
                            pollinations.generate_reference(
                                combined_prompt,
                                seed=char.get("seed"),
                            )
                        )

                    else:
                        raise

            else:

                # No named character in this scene.
                scene_seed = (
                    uuid.uuid4().int % (10**6)
                )

                img_bytes = (
                    pollinations.generate_reference(
                        sc["visual_description"],
                        seed=scene_seed,
                    )
                )

            img_url = db.upload_to_storage(
                STORAGE_BUCKET,
                f"scenes/{scene_row['scene_id']}.png",
                img_bytes,
                "image/png",
            )

        except Exception as exc:  # noqa: BLE001

            db.log_error(
                story_id,
                "produce.scene_image",
                str(exc),
                scene_row["scene_id"],
            )

            raise

        # ---------------------------------------------------------------
        # VOICE
        # ---------------------------------------------------------------

        try:

            clean_narration = sanitize_and_validate(
                sc["narration_text"]
            )

            audio_bytes = tts.synthesize(
                clean_narration
            )

            audio_url = db.upload_to_storage(
                STORAGE_BUCKET,
                f"scenes/{scene_row['scene_id']}.wav",
                audio_bytes,
                "audio/wav",
            )

        except Exception as exc:  # noqa: BLE001

            db.log_error(
                story_id,
                "produce.scene_audio",
                str(exc),
                scene_row["scene_id"],
            )

            raise

        # ---------------------------------------------------------------
        # UPDATE SCENE
        # ---------------------------------------------------------------

        db.update(
            "scenes",
            {
                "scene_id": f"eq.{scene_row['scene_id']}",
            },
            {
                "image_url": img_url,
                "audio_url": audio_url,
                "status": "assembled",
            },
        )

        built.append(
            {
                **scene_row,
                "image_url": img_url,
                "audio_url": audio_url,
                "narration_text": sc["narration_text"],
            }
        )

    return built


# ---------------------------------------------------------------------------
# DOWNLOAD / MEDIA HELPERS
# ---------------------------------------------------------------------------

def download(url: str, dest: str):
    response = requests.get(
        url,
        timeout=120,
    )

    response.raise_for_status()

    with open(dest, "wb") as f:
        f.write(response.content)


def get_audio_duration(path: str) -> float:

    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    return float(
        out.stdout.strip()
    )


# ---------------------------------------------------------------------------
# KANNADA SUBTITLES
# ---------------------------------------------------------------------------

def _subtitle_chunks(
    text: str,
    max_words: int = 6,
) -> list[str]:
    """
    Split ONLY on whitespace.

    IMPORTANT:
    Never transliterate, normalize, decompose, or otherwise modify
    Kannada Unicode characters.
    """

    text = sanitize_and_validate(text)

    words = text.split()

    return [
        " ".join(
            words[i:i + max_words]
        )
        for i in range(
            0,
            len(words),
            max_words,
        )
    ] or [text]


def _ass_time(seconds: float) -> str:

    total_cs = max(
        0,
        int(round(seconds * 100)),
    )

    h, rem = divmod(
        total_cs,
        360000,
    )

    m, rem = divmod(
        rem,
        6000,
    )

    s, cs = divmod(
        rem,
        100,
    )

    return (
        f"{h}:"
        f"{m:02}:"
        f"{s:02}."
        f"{cs:02}"
    )


def write_ass(
    text: str,
    duration: float,
    path: str,
    max_words: int = 6,
    font_name: str = "Noto Sans Kannada",
):
    """
    Write UTF-8 ASS subtitles for libass/OpenType complex-script shaping.

    The narration text is never transliterated, decomposed, or otherwise
    rewritten.

    Only whitespace-based chunking is performed for subtitle timing.
    """

    chunks = _subtitle_chunks(
        text,
        max_words=max_words,
    )

    per_chunk = (
        duration / len(chunks)
    )

    safe_font = (
        font_name
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
    )

    # 54 was increased from the original tiny subtitle size.
    # Keep this value unless you want to change caption size again.
    subtitle_font_size = 54

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Kannada,{safe_font},{subtitle_font_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,60,60,100,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    with open(
        path,
        "w",
        encoding="utf-8",
        newline="\n",
    ) as f:

        f.write(header)

        for i, chunk in enumerate(chunks):

            start = (
                i * per_chunk
            )

            end = (
                (i + 1) * per_chunk
            )

            # ASS uses { } for override tags.
            # Escape literal braces so narration is preserved safely.
            ass_text = (
                chunk
                .replace("{", "\\{")
                .replace("}", "\\}")
            )

            f.write(
                "Dialogue: 0,"
                f"{_ass_time(start)},"
                f"{_ass_time(end)},"
                "Kannada,,0,0,0,,"
                f"{ass_text}\n"
            )

    # ---------------------------------------------------------------
    # UTF-8 ROUND-TRIP VALIDATION
    # ---------------------------------------------------------------
    #
    # This catches accidental encoding/transcoding changes before
    # FFmpeg receives the subtitle file.
    #

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as f:

        rendered = []

        for line in f:

            if line.startswith(
                "Dialogue:"
            ):

                rendered.append(
                    line
                    .rstrip("\n")
                    .split(",", 9)[-1]
                    .replace("\\{", "{")
                    .replace("\\}", "}")
                )

    if rendered != chunks:

        raise ValueError(
            "Subtitle UTF-8 round-trip validation failed: "
            "narration text changed before FFmpeg rendering"
        )


# ---------------------------------------------------------------------------
# VIDEO ASSEMBLY
# ---------------------------------------------------------------------------

def assemble_video(
    scenes: list[dict],
    workdir: str,
) -> str:

    font_name = os.environ.get(
        "SUBTITLE_FONT",
        "Noto Sans Kannada",
    )

    clip_paths = []

    for i, sc in enumerate(scenes):

        img_path = os.path.join(
            workdir,
            f"img_{i}.png",
        )

        audio_path = os.path.join(
            workdir,
            f"audio_{i}.wav",
        )

        ass_path = os.path.join(
            workdir,
            f"sub_{i}.ass",
        )

        clip_path = os.path.join(
            workdir,
            f"clip_{i}.mp4",
        )

        # ---------------------------------------------------------------
        # DOWNLOAD ASSETS
        # ---------------------------------------------------------------

        download(
            sc["image_url"],
            img_path,
        )

        download(
            sc["audio_url"],
            audio_path,
        )

        duration = get_audio_duration(
            audio_path
        )

        # ---------------------------------------------------------------
        # WRITE KANNADA ASS SUBTITLES
        # ---------------------------------------------------------------

        write_ass(
            sc["narration_text"],
            duration,
            ass_path,
            max_words=6,
            font_name=font_name,
        )

        # ---------------------------------------------------------------
        # FFmpeg PATH ESCAPING
        # ---------------------------------------------------------------

        ass_filter_path = (
            ass_path
            .replace("\\", "/")
            .replace(":", "\\:")
            .replace("'", "\\'")
        )

        fonts_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(__file__)
            ),
            "fonts",
        )

        fonts_dir = (
            os.path.abspath(fonts_dir)
            .replace("\\", "/")
            .replace(":", "\\:")
            .replace("'", "\\'")
        )

        # ---------------------------------------------------------------
        # CREATE CLIP
        # ---------------------------------------------------------------

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                img_path,
                "-i",
                audio_path,

                "-filter_complex",

                (
                    "[0:v]"
                    "scale=1080:1920:"
                    "force_original_aspect_ratio=increase,"
                    "crop=1080:1920,"
                    f"zoompan="
                    f"z='min(zoom+0.0015,1.3)':"
                    f"d={int(duration * 25)}:"
                    "s=1080x1920:"
                    "fps=25,"
                    f"ass='{ass_filter_path}':"
                    f"fontsdir='{fonts_dir}':"
                    "shaping=complex"
                    "[v]"
                ),

                "-map",
                "[v]",

                "-map",
                "1:a",

                "-c:v",
                "libx264",

                "-c:a",
                "aac",

                "-t",
                str(duration),

                "-shortest",

                clip_path,
            ],
            check=True,
            capture_output=True,
        )

        clip_paths.append(
            clip_path
        )

    # -------------------------------------------------------------------
    # CONCATENATE CLIPS
    # -------------------------------------------------------------------

    concat_list = os.path.join(
        workdir,
        "concat.txt",
    )

    with open(
        concat_list,
        "w",
        encoding="utf-8",
    ) as f:

        for path in clip_paths:
            f.write(
                f"file '{path}'\n"
            )

    final_path = os.path.join(
        workdir,
        "final.mp4",
    )

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_list,
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            final_path,
        ],
        check=True,
        capture_output=True,
    )

    return final_path


# ---------------------------------------------------------------------------
# QUALITY CONTROL
# ---------------------------------------------------------------------------

def qc_check(
    video_path: str,
) -> tuple[bool, str]:

    if (
        not os.path.exists(video_path)
        or os.path.getsize(video_path) == 0
    ):
        return (
            False,
            "Final video file missing or empty",
        )

    duration = get_audio_duration(
        video_path
    )

    if duration < 5 or duration > 330:

        return (
            False,
            f"Duration out of expected range: "
            f"{duration:.1f}s",
        )

    return (
        True,
        "ok",
    )


# ---------------------------------------------------------------------------
# MAIN PRODUCTION WORKFLOW
# ---------------------------------------------------------------------------

def main():

    story = find_approved_story()

    if not story:

        print(
            "No approved stories waiting for production. "
            "Exiting cleanly."
        )

        return

    story_id = story["story_id"]

    db.update(
        "stories",
        {
            "story_id": f"eq.{story_id}",
        },
        {
            "status": "in_production",
        },
    )

    # -------------------------------------------------------------------
    # SCENE BREAKDOWN
    # -------------------------------------------------------------------

    try:

        scenes = scene_breakdown(
            story
        )

    except RuntimeError as exc:

        if (
            "PROHIBITED_CONTENT" in str(exc)
            or "safety-filtered" in str(exc)
        ):

            db.log_error(
                story_id,
                "produce.scene_breakdown",
                str(exc),
            )

            db.update(
                "stories",
                {
                    "story_id": f"eq.{story_id}",
                },
                {
                    "status": "blocked_by_safety_filter",
                },
            )

            telegram.notify_error(
                "produce.scene_breakdown",
                (
                    "This story was blocked by Gemini's "
                    "safety filter (likely a false positive "
                    "on emotional content) and won't be "
                    "retried automatically. Review it in "
                    "Supabase — you can rewrite and "
                    "re-approve it, or let tomorrow's "
                    "fresh story take its place."
                ),
                story_id,
            )

            print(
                f"Story {story_id} blocked by safety filter, "
                "marked and skipped."
            )

            return

        raise

    # -------------------------------------------------------------------
    # BUILD SCENE ASSETS
    # -------------------------------------------------------------------

    built = build_scene_assets(
        story_id,
        story["category"],
        scenes,
        story,
    )

    # -------------------------------------------------------------------
    # ASSEMBLE + QC + UPLOAD
    # -------------------------------------------------------------------

    with tempfile.TemporaryDirectory() as workdir:

        final_path = assemble_video(
            built,
            workdir,
        )

        passed, reason = qc_check(
            final_path
        )

        if not passed:

            db.log_error(
                story_id,
                "produce.qc",
                reason,
            )

            db.update(
                "stories",
                {
                    "story_id": f"eq.{story_id}",
                },
                {
                    "status": "qc_failed",
                },
            )

            telegram.notify_error(
                "produce.qc",
                reason,
                story_id,
            )

            sys.exit(1)

        with open(
            final_path,
            "rb",
        ) as f:

            video_url = db.upload_to_storage(
                STORAGE_BUCKET,
                f"final/{story_id}.mp4",
                f.read(),
                "video/mp4",
            )

    # -------------------------------------------------------------------
    # FINAL STORY UPDATE
    # -------------------------------------------------------------------

    db.update(
        "stories",
        {
            "story_id": f"eq.{story_id}",
        },
        {
            "status": "produced",
            "final_video_url": video_url,
        },
    )

    print(
        f"Story {story_id} produced successfully: "
        f"{video_url}"
    )


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    try:

        main()

    except Exception as exc:  # noqa: BLE001

        telegram.notify_error(
            "produce",
            str(exc),
        )

        raise
