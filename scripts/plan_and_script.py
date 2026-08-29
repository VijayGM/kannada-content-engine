"""
Workflow 1 + 2 (combined): Content Planner + Script Generator.
Triggered daily by .github/workflows/01-plan-and-script.yml
"""
import datetime
import json
import sys

from lib import gemini, supabase_client as db, telegram

RETENTION_THRESHOLD = 7.0
MAX_REGENERATIONS = 2

CONTENT_CALENDAR = {
    0: "Family comedy",
    1: "Family",
    2: "Moral stories",
    3: "Relationship stories",
    4: "Workplace comedy",
    5: "Children's stories",
    6: "Emotional stories",
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
- CRITICAL: Every Kannada word must be spelled correctly and be a real, standard word —
  double-check each word before including it. Do NOT invent non-standard word forms or
  merge/duplicate syllables incorrectly (e.g. malformed conjugations with repeated letters
  that aren't real Kannada). If uncertain whether a word or grammatical construction is
  correct, prefer a simpler, more common phrasing you're confident is correct over a more
  elaborate one that risks an error. Correctness matters more than sophistication here.
- Opening hook: 1-3 seconds, immediate curiosity (question, conflict, surprise, or similar).
- Fast pacing, short conversational sentences, natural dialogue, escalating conflict.
- Ending appropriate to the category: punchline, twist, emotional payoff, or moral.

Return ONLY a JSON object with keys:
"opening_hook" (Kannada text), "body_beats" (array of Kannada text segments, each a distinct
story beat/scene), "ending" (Kannada text), "characters" (array of character names/roles
appearing in this script).
"""
    return gemini.generate_json(prompt, temperature=0.75)

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

def check_kannada_correctness(script: dict) -> tuple[bool, list[str]]:
    prompt = f"""You are a native Kannada speaker proofreading a script for spelling and
grammar errors. Read every Kannada word carefully.

Script (JSON): {json.dumps(script, ensure_ascii=False)}

Return ONLY a JSON object: {{"has_errors": <true/false>, "issues": [<array of strings,
each describing one specific misspelled or malformed word/phrase you found, empty array
if none>]}}
"""
    result = gemini.generate_json(prompt, temperature=0.1)
    return not result.get("has_errors", False), result.get("issues", [])

def main():
    category = pick_category()
    print(f"Category for today: {category}")

    avoid = recent_hooks(category)
    concepts = generate_concepts(category, avoid)
    if not concepts:
        db.log_error(None, "plan_and_script.generate_concepts", "Gemini returned no concepts")
        sys.exit(1)

    concept = concepts[0]

    script = None
    score = 0.0
    weakest = ""
    language_issues = []

    for attempt in range(1, MAX_REGENERATIONS + 2):
        script = generate_script(category, concept)
        if not script_uses_kannada_script(script):
            print(f"Attempt {attempt}: rejected — script came back in romanized "
                  f"Latin letters instead of Kannada script. Regenerating.")
            continue

        correct, issues = check_kannada_correctness(script)
        if not correct:
            print(f"Attempt {attempt}: rejected — spelling/grammar issues found: {issues}. Regenerating.")
            language_issues = issues
            continue

        score, weakest = score_retention(script)
        print(f"Attempt {attempt}: retention score {score} (weakest: {weakest})")
        if score >= RETENTION_THRESHOLD:
            break
    else:
        db.log_error(None, "plan_and_script", f"Script QC failed after {MAX_REGENERATIONS + 1} retries — no script saved")
        telegram.notify_error("plan_and_script", f"Script QC failed after {MAX_REGENERATIONS + 1} retries — no script saved")
        return

    story = {
        "story_id": None,
        "category": category,
        "title": concept.get("title", ""),
        "hook": script.get("opening_hook", ""),
        "script": script,
        "retention_score": score,
        "language_issues": language_issues,
        "weakest": weakest,
    }

    print(f"Script passed QC with score {score}")
    telegram.notify_story_for_review(story)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        db.log_error(None, "plan_and_script", str(e))
        telegram.notify_error("plan_and_script", str(e))
        raise
