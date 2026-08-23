"""
Workflow 1 + 2 (combined): Content Planner + Script Generator.
Triggered daily by .github/workflows/01-plan-and-script.yml

Steps:
  1. Pick today's category (calendar default, overridable by performance data later)
  2. Pull recent stories in that category to avoid duplicate hooks/plots
  3. Generate 3 concepts via Gemini, pick one
  4. Generate the full Kannada script (hook / body / ending)
  5. Score it for retention; regenerate once if below threshold
  6. Save to Supabase with status 'pending_review'
  7. Notify via Telegram for Human Approval Mode

This is intentionally a straight-line script, not an n8n workflow file —
see README.md for why (n8n is used for visual editing, but each scheduled
run executes as a plain script inside the GitHub Actions container; the
n8n workflow JSON that mirrors this logic lives in n8n/workflows/ for when
you're ready to wire it up in the n8n editor).
"""
import datetime
import json
import sys

from lib import gemini, supabase_client as db, telegram

RETENTION_THRESHOLD = 7.0  # out of 10 — configurable
MAX_REGENERATIONS = 2

# Default weekly calendar from the brief §15 — override by editing this dict,
# no code changes needed elsewhere.
CONTENT_CALENDAR = {
    0: "Family comedy",       # Monday
    1: "Family",               # Tuesday
    2: "Moral stories",        # Wednesday
    3: "Relationship stories", # Thursday
    4: "Workplace comedy",     # Friday
    5: "Children's stories",   # Saturday
    6: "Emotional stories",    # Sunday
}


def pick_category() -> str:
    weekday = datetime.datetime.utcnow().weekday()
    return CONTENT_CALENDAR[weekday]


def recent_hooks(category: str, limit: int = 15) -> list[str]:
    rows = db.select("stories", {
        "category": f"eq.{category}",
        "select": "hook",
        "order": "creation_date.desc",
        "limit": str(limit),
    })
    return [r["hook"] for r in rows if r.get("hook")]


def generate_concepts(category: str, avoid_hooks: list[str]) -> list[dict]:
    prompt = f"""You are a Kannada short-video story writer for Instagram.
Category: {category}
Generate 3 distinct story concepts for a spoken-Kannada narrated video, up to 5 minutes long.

Avoid these already-used hooks (do not repeat the pattern or premise):
{json.dumps(avoid_hooks, ensure_ascii=False)}

Return ONLY a JSON array of 3 objects, each with keys:
"title" (short, English is fine for internal tracking), "hook" (the opening line, in Kannada), "premise" (2-3 sentence summary, in English, for internal review).
"""
    result = gemini.generate_json(prompt)
    return result if isinstance(result, list) else result.get("concepts", [])


def generate_script(category: str, concept: dict) -> dict:
    prompt = f"""You are a Kannada short-video scriptwriter. Write a complete spoken-Kannada
video script for Instagram, up to 5 minutes when read aloud at a natural conversational pace.

Category: {category}
Title: {concept['title']}
Premise: {concept['premise']}

Requirements:
- Natural, conversational Kannada — including natural Kannada-English code-mixing where
  that's how people actually speak in this context (e.g. workplace, youth dialogue).
  Do NOT produce literal/textbook Kannada that a native speaker would find stilted.
- CRITICAL: Write all Kannada words in actual Kannada script (ಕನ್ನಡ ಲಿಪಿ / Unicode Kannada
  characters), NEVER in romanized/Latin-letter transliteration ("Kanglish", e.g. writing
  "hogutini" instead of "ಹೋಗುತ್ತೀನಿ"). Code-mixed English words (like "boss", "office",
  "deadline") may appear in Latin letters as loanwords within an otherwise Kannada-script
  sentence — that's correct and intended. But Kannada words themselves must always be in
  Kannada script. This matters because the downstream text-to-speech engine only correctly
  pronounces actual Kannada script; romanized Kannada text produces broken or wrong audio.
- Opening hook: 1-3 seconds, immediate curiosity (question, conflict, surprise, or similar).
- Fast pacing, short conversational sentences, natural dialogue, escalating conflict.
- Ending appropriate to the category: punchline, twist, emotional payoff, or moral.

Return ONLY a JSON object with keys:
"opening_hook" (Kannada text), "body_beats" (array of Kannada text segments, each a distinct
story beat/scene), "ending" (Kannada text), "characters" (array of character names/roles
appearing in this script).
"""
    return gemini.generate_json(prompt, temperature=0.95)

def has_kannada_script(text: str, min_ratio: float = 0.3) -> bool:
    if not text:
        return True
    kannada_chars = sum(1 for c in text if "\u0c80" <= c <= "\u0cff")
    alpha_chars = sum(1 for c in text if c.isalpha())
    if alpha_chars == 0:
        return True
    return (kannada_chars / alpha_chars) >= min_ratio


def script_uses_kannada_script(script: dict) -> bool:
    parts = [script.get("opening_hook", ""), script.get("ending", "")]
    parts.extend(script.get("body_beats", []))
    return all(has_kannada_script(p) for p in parts)

def score_retention(script: dict) -> float:
    prompt = f"""Score this Kannada video script's Instagram retention potential from 0-10.
Consider: hook strength (first 1-3 seconds), pacing, curiosity gap, emotional progression,
payoff strength, replay/shareability potential.

Script (JSON): {json.dumps(script, ensure_ascii=False)}

Return ONLY a JSON object: {{"score": <number 0-10>, "weakest_element": "<string>"}}
"""
    result = gemini.generate_json(prompt, temperature=0.3)
    return float(result.get("score", 0)), result.get("weakest_element", "")


def main():
    category = pick_category()
    print(f"Category for today: {category}")

    avoid = recent_hooks(category)
    concepts = generate_concepts(category, avoid)
    if not concepts:
        db.log_error(None, "plan_and_script.generate_concepts", "Gemini returned no concepts")
        sys.exit(1)

    # MVP duplicate check: simple keyword-overlap heuristic, not embeddings —
    # good enough at low volume, flagged in the architecture doc as a
    # future upgrade once you have enough stories for embeddings to be worth it.
    concept = concepts[0]

    script = None
    score = 0.0
    weakest = ""
    for attempt in range(1, MAX_REGENERATIONS + 2):
        script = generate_script(category, concept)
        if not script_uses_kannada_script(script):
            print(f"Attempt {attempt}: rejected — script came back in romanized "
                  f"Latin letters instead of Kannada script. Regenerating.")
            continue
        score, weakest = score_retention(script)
        print(f"Attempt {attempt}: retention score {score} (weakest: {weakest})")
        if score >= RETENTION_THRESHOLD:
            break

    story = db.insert("stories", {
        "category": category,
        "title": concept["title"],
        "hook": script.get("opening_hook"),
        "script": script,
        "retention_score": score,
        "status": "pending_review",
    })

    print(f"Saved story {story['story_id']} with status pending_review")
    telegram.notify_story_for_review(story)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 — top-level catch so failures are visible, not silent
        db.log_error(None, "plan_and_script", str(e))
        telegram.notify_error("plan_and_script", str(e))
        raise
