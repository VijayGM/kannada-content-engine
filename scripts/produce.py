"""
Workflow 3 + 4: Production + Quality Control.
Triggered by .github/workflows/02-check-approval-and-produce.yml once a
story's status is 'approved' in Supabase (flipped manually after the
Telegram review — see lib/telegram.py).

Steps:
  1. Scene breakdown (Gemini) from the approved script
  2. Character reference images (Flux) — once per character, cached
  3. Per-scene images (Kontext) — identity-preserving edits of the reference
  4. Per-scene voice (Sarvam TTS)
  5. Assemble: Ken Burns pan/zoom per image, synced to audio duration,
     concatenated, subtitles burned in (from script text — see subtitle
     timing caveat in README/architecture doc)
  6. Basic QC checks (duration, file exists, non-zero size)
  7. Upload final video to Supabase Storage, update story status

This entire script runs inside a single GitHub Actions job (see
.github/workflows/02-check-approval-and-produce.yml) — no external render
service, FFmpeg runs on the runner itself.
"""
import json
import os
import subprocess
import sys
import tempfile
import uuid

from lib import gemini, supabase_client as db, pollinations, tts, telegram

STORAGE_BUCKET = "content-engine-media"


def find_approved_story():
    rows = db.select("stories", {"status": "eq.approved", "limit": "1"})
    return rows[0] if rows else None

def get_or_create_character(name: str, category: str) -> dict:
    existing = db.select("characters", {"name": f"eq.{name}", "limit": "1"})
    if existing:
        return existing[0]

    identity_descriptor = (
        f"{name}, a character in a Kannada {category} story. "
        f"Flat 2D illustrated style, warm color palette."
    )
    reference_prompt = (
        f"{identity_descriptor} Consistent character design sheet, "
        f"front-facing, neutral pose, plain background."
    )
    seed = abs(hash(name)) % (10**6)
    img_bytes = pollinations.generate_reference(reference_prompt, seed=seed)
    url = db.upload_to_storage(STORAGE_BUCKET, f"characters/{uuid.uuid4()}.png", img_bytes, "image/png")
    return db.insert("characters", {
        "name": name,
        "prompt_template": identity_descriptor,
        "reference_image_url": url,
        "seed": seed,
    })

def scene_breakdown(story: dict) -> list[dict]:
    prompt = f"""Break this Kannada video script into a numbered scene list for image generation.
Each scene should map to roughly 8-15 seconds of narration.

Script (JSON): {json.dumps(story['script'], ensure_ascii=False)}

Return ONLY a JSON array of objects with keys:
"scene_number" (int), "narration_text" (the Kannada text spoken during this scene),
"visual_description" (English, describing the visual: setting, action, mood, lighting),
"characters_present" (array of character names from the script).
"""
    return gemini.generate_json(prompt, temperature=0.4)

def build_scene_assets(story_id: str, category: str, scenes: list[dict]) -> list[dict]:
    built = []
    for sc in scenes:
        scene_row = db.insert("scenes", {
            "story_id": story_id,
            "scene_number": sc["scene_number"],
            "description": sc["visual_description"],
        })

        # Image: reference + Kontext edit per character present, or a plain
        # Flux generation if no named character is in this scene.
        try:
            if sc.get("characters_present"):
                char = get_or_create_character(sc["characters_present"][0], category)
                combined_prompt = f"{char['prompt_template']}. Scene: {sc['visual_description']}"
                if char.get("reference_image_url"):
                    import requests
                    ref_resp = requests.get(char["reference_image_url"], timeout=30)
                    ref_resp.raise_for_status()
                    img_bytes = pollinations.edit_scene(
                        reference_image_bytes=ref_resp.content,
                        scene_prompt=combined_prompt,
                        seed=char["seed"],
                    )
                else:
                    img_bytes = pollinations.generate_reference(combined_prompt, seed=char["seed"])
            else:
                img_bytes = pollinations.generate_reference(sc["visual_description"], seed=uuid.uuid4().int % (10**6))
            img_url = db.upload_to_storage(
                STORAGE_BUCKET, f"scenes/{scene_row['scene_id']}.png", img_bytes, "image/png"
            )
        except Exception as e:  # noqa: BLE001
            db.log_error(story_id, "produce.scene_image", str(e), scene_row["scene_id"])
            raise

        # Voice
        try:
            audio_bytes = tts.synthesize(sc["narration_text"])
            audio_url = db.upload_to_storage(
                STORAGE_BUCKET, f"scenes/{scene_row['scene_id']}.wav", audio_bytes, "audio/wav"
            )
        except Exception as e:  # noqa: BLE001
            db.log_error(story_id, "produce.scene_audio", str(e), scene_row["scene_id"])
            raise

        db.update("scenes", {"scene_id": f"eq.{scene_row['scene_id']}"}, {
            "image_url": img_url, "audio_url": audio_url, "status": "assembled",
        })
        built.append({**scene_row, "image_url": img_url, "audio_url": audio_url,
                       "narration_text": sc["narration_text"]})
    return built


def download(url: str, dest: str):
    import requests
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    with open(dest, "wb") as f:
        f.write(r.content)


def get_audio_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def write_srt(text: str, duration: float, path: str, max_words: int = 6):
    words = text.split()
    chunks = [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)] or [text]
    per_chunk = duration / len(chunks)

    def fmt(t):
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        ms = int((s % 1) * 1000)
        return f"{int(h):02}:{int(m):02}:{int(s):02},{ms:03}"

    with open(path, "w", encoding="utf-8") as f:
        for i, chunk in enumerate(chunks):
            start, end = i * per_chunk, (i + 1) * per_chunk
            f.write(f"{i+1}\n{fmt(start)} --> {fmt(end)}\n{chunk}\n\n")


def assemble_video(scenes: list[dict], workdir: str) -> str:
    font_name = os.environ.get("SUBTITLE_FONT", "Noto Sans Kannada")

    clip_paths = []
    for i, sc in enumerate(scenes):
        img_path = os.path.join(workdir, f"img_{i}.png")
        audio_path = os.path.join(workdir, f"audio_{i}.wav")
        srt_path = os.path.join(workdir, f"sub_{i}.srt")
        clip_path = os.path.join(workdir, f"clip_{i}.mp4")
        download(sc["image_url"], img_path)
        download(sc["audio_url"], audio_path)

        duration = get_audio_duration(audio_path)
        write_srt(sc["narration_text"], duration, srt_path)
        srt_filter_path = srt_path.replace("\\", "/").replace(":", "\\:")

        subprocess.run([
            "ffmpeg", "-y", "-loop", "1", "-i", img_path, "-i", audio_path,
            "-filter_complex",
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,zoompan=z='min(zoom+0.0015,1.3)':d={int(duration*25)}:s=1080x1920:fps=25,"
f"subtitles='{srt_filter_path}':force_style="
f"'FontName={font_name},FontSize=16,PrimaryColour=&HFFFFFF&,"
f"OutlineColour=&H000000&,BorderStyle=1,Outline=2,Alignment=2,"
f"MarginV=100,MarginL=60,MarginR=60'[v]",
            "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-c:a", "aac",
            "-t", str(duration), "-shortest", clip_path,
        ], check=True, capture_output=True)
        clip_paths.append(clip_path)

    # Concatenate all clips
    concat_list = os.path.join(workdir, "concat.txt")
    with open(concat_list, "w") as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")

    final_path = os.path.join(workdir, "final.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
        "-c:v", "libx264", "-c:a", "aac", final_path,
    ], check=True, capture_output=True)

    return final_path


def qc_check(video_path: str) -> tuple[bool, str]:
    if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
        return False, "Final video file missing or empty"
    duration = get_audio_duration(video_path)  # ffprobe works on video too
    if duration < 5 or duration > 330:  # allow slight overrun past 5 min
        return False, f"Duration out of expected range: {duration:.1f}s"
    return True, "ok"

def main():
    story = find_approved_story()
    if not story:
        print("No approved stories waiting for production. Exiting cleanly.")
        return

    story_id = story["story_id"]
    db.update("stories", {"story_id": f"eq.{story_id}"}, {"status": "in_production"})

    try:
        scenes = scene_breakdown(story)
    except RuntimeError as e:
        if "PROHIBITED_CONTENT" in str(e) or "safety-filtered" in str(e):
            db.log_error(story_id, "produce.scene_breakdown", str(e))
            db.update("stories", {"story_id": f"eq.{story_id}"}, {"status": "blocked_by_safety_filter"})
            telegram.notify_error(
                "produce.scene_breakdown",
                "This story was blocked by Gemini's safety filter (likely a false "
                "positive on emotional content) and won't be retried automatically. "
                "Review it in Supabase — you can rewrite and re-approve it, or let "
                "tomorrow's fresh story take its place.",
                story_id,
            )
            print(f"Story {story_id} blocked by safety filter, marked and skipped.")
            return
        raise

    built = build_scene_assets(story_id, story["category"], scenes)

    with tempfile.TemporaryDirectory() as workdir:
        final_path = assemble_video(built, workdir)
        passed, reason = qc_check(final_path)

        if not passed:
            db.log_error(story_id, "produce.qc", reason)
            db.update("stories", {"story_id": f"eq.{story_id}"}, {"status": "qc_failed"})
            telegram.notify_error("produce.qc", reason, story_id)
            sys.exit(1)

        with open(final_path, "rb") as f:
            video_url = db.upload_to_storage(
                STORAGE_BUCKET, f"final/{story_id}.mp4", f.read(), "video/mp4"
            )

    db.update("stories", {"story_id": f"eq.{story_id}"}, {
        "status": "produced", "final_video_url": video_url,
    })
    print(f"Story {story_id} produced successfully: {video_url}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        telegram.notify_error("produce", str(e))
        raise
