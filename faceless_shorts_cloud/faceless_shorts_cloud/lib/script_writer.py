"""
Calls OpenRouter (OpenAI-compatible chat completions API) to write a Short's
script in the "curiosity-gap real-person fact" style. No Claude/Anthropic
involved -- this is the one step in the pipeline that needs an LLM at all,
and it works with OpenRouter's free-tier models.

OpenRouter's free-tier model lineup changes often -- models get renamed or
retired every few weeks. Rather than hardcoding one slug that will eventually
go stale (causing an "HTTP Error 404"), this module treats the configured
model as a first guess: if it 404s, it asks OpenRouter's own /models list for
whichever free models are live *right now* and automatically retries with
one of those, so the app keeps working without a manual fix each time.
"""
import json
import os
import re
import urllib.error
import urllib.request

STYLE_PROFILE_PATH = os.path.join(os.path.dirname(__file__), "..", "style_profile.md")

SYSTEM_PROMPT_TEMPLATE = """You write short, punchy scripts for faceless YouTube \
Shorts. Follow this style profile exactly:

{style_profile}

You must respond with ONLY a JSON object (no markdown fences, no commentary), \
with this exact shape:

{{
  "title": "the hook line (scene 1's caption) itself, trimmed to fit if needed, ending in #Shorts, under 90 characters -- a complete surprising claim OR a named emotional/relational stake (a fear, a secret, a regret, a devotion) with the outcome optionally withheld, whichever the real story supports better -- NEVER a generic 'N facts/reasons about X' label; run the title checklist from the style profile before finalizing",
  "scenes": [
    {{"caption": "on-screen text for this scene, roughly 12-14 words / 2 wrapped lines MAX -- split a longer idea into two scenes instead of writing one long caption (see style profile), and make it follow causally from the previous scene's caption (see 'One throughline' in the style profile) rather than introducing an unrelated new fact", "image_query": "a concrete, literal search phrase for a real photo that would illustrate this scene", "person_name": "the full name of the specific real, identifiable, named individual this scene is about, or null if the scene is generic (an anonymous person, an object, a place, a concept)"}},
    ... EXACTLY 5 to 6 scenes total, never more -- this is a hard runtime
    budget (30-40 seconds total, see style profile), not a suggestion.
    Follow hook -> connected context -> connected consequence -> payoff ->
    CTA, where each scene is a direct causal result of the one before it. If
    the topic has more good material than 5-6 scenes can hold, do NOT add
    scenes -- cut to the single strongest thread and go deep on it instead ...
  ],
  "description": "short 1-2 sentence narrative paragraphs (not the captions restated verbatim), each separated by a blank line, that end on a warm, meaningful, or redemptive takeaway line -- then one direct question to the viewer specific to this story (never generic) -- then a four-line CTA block, one specific action per line, each tied concretely to this story's content, in this shape: '❤️ Like if [specific reaction tied to the story]\\n🔖 Save this for later\\n💬 Tell us [specific thing to answer, tied to the story] in the comments\\n📩 Share this with someone who [specific person tied to the story]' -- never generic wording on any of those four lines (see 'Description' in the style profile)",
  "tags": ["10 to 15 tags as plain strings, no # symbol: the exact subject name, 1-2 common misspelling variants of it, 2-3 format tags (e.g. 'interesting facts', 'true story', 'history facts'), and 2-3 tags naming other real well-known people/topics from the same era or genre as this story (see 'Tags' in the style profile)"]
}}

The last scene's caption should always be a short follow/subscribe call to action.
image_query values should name the real person/subject plus a concrete visual
(e.g. "Dolly Parton red carpet", "Dolly Parton guitar performance") so they work
well as image-search queries against a stock/commons photo library.

For a generic/concept scene (person_name is null), add a short photographic
style descriptor to the end of image_query -- "cinematic", "dramatic
lighting", "moody atmosphere", "close-up", "aerial shot" -- whichever fits the
mood of that line. Stock libraries return noticeably higher-quality, more
professional-looking results for a styled query ("dark cliff edge, moody
atmosphere, cinematic") than a bare one ("dark cliff edge"), since it biases
the search toward professional photography/cinematography rather than plain
snapshots.

person_name matters a lot: generic stock-photo/video libraries (Pexels, Pixabay)
never actually have footage of a specific real, named individual -- only
anonymous models -- so whenever a scene is about a specific real person (a
historical figure, celebrity, athlete, public figure), set person_name to
their exact full name so the app knows to search likeness-safe sources
(Wikimedia Commons, Openverse) instead of generic stock, and to leave the
background plain rather than show a random stranger's face if no real photo
of them is found. Set person_name to null for any scene that is NOT about a
specific identifiable individual (e.g. "a crowd of protesters", "a flooded
street", "a stressed man at a desk")."""


def _load_style_profile():
    with open(STYLE_PROFILE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _extract_json(text):
    """Best-effort extraction in case the model wraps the JSON in prose or
    markdown fences despite instructions."""
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            text = brace_match.group(0)
    return json.loads(text)


def _call_openrouter(model: str, messages: list, api_key: str, referer: str) -> str:
    payload = {"model": model, "messages": messages, "temperature": 0.9}
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": referer,
            "X-Title": "faceless-shorts-app",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def _try_model(model: str, topic: str, system_prompt: str, api_key: str, referer: str) -> dict:
    """Up to 2 attempts against one model: retries once if the reply isn't
    valid JSON in the required shape. Raises the last error if both fail
    (including straight away if the model itself doesn't exist)."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Write a script about: {topic}"},
    ]
    last_error = None
    for _ in range(2):
        try:
            content = _call_openrouter(model, messages, api_key, referer)
            script = _extract_json(content)
            if "title" not in script or "scenes" not in script or not script["scenes"]:
                raise ValueError("Model response missing required fields")
            return script
        except urllib.error.HTTPError as e:
            # A 404 here means the model id itself is invalid/retired --
            # retrying the same model won't help, so bail out immediately.
            raise
        except Exception as e:  # noqa: BLE001 -- bad/partial JSON, network hiccup, etc.
            last_error = e
            messages.append({
                "role": "user",
                "content": "That wasn't valid JSON matching the required shape. "
                            "Reply again with ONLY the JSON object, nothing else.",
            })
    raise last_error


def _get_free_model_candidates(api_key: str, referer: str, exclude=()) -> list:
    """Asks OpenRouter itself which models are free right now, so this app
    keeps working even after a specific free-tier model gets renamed or
    retired. Returns a list of model ids (possibly empty if the lookup
    fails for any reason -- never raises)."""
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}", "HTTP-Referer": referer},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        candidates = []
        for m in data.get("data", []):
            model_id = m.get("id", "")
            pricing = m.get("pricing", {})
            if not model_id.endswith(":free"):
                continue
            if str(pricing.get("prompt", "1")) != "0" or str(pricing.get("completion", "1")) != "0":
                continue
            if model_id in exclude:
                continue
            candidates.append(model_id)
        return candidates
    except Exception:  # noqa: BLE001 -- this is a best-effort fallback lookup
        return []


def generate_script(topic: str, api_key: str, model: str, referer: str = "https://localhost") -> dict:
    """Returns a dict: {title, scenes: [{caption, image_query}, ...], description, tags}.

    Tries `model` first. If OpenRouter says that model doesn't exist (404),
    automatically looks up whichever free models are currently live and
    retries with those before giving up.
    """
    style_profile = _load_style_profile()
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(style_profile=style_profile)

    models_to_try = [model]
    tried = set()
    last_error = None
    fetched_fallbacks = False

    i = 0
    while i < len(models_to_try):
        current = models_to_try[i]
        i += 1
        if current in tried:
            continue
        tried.add(current)
        try:
            script = _try_model(current, topic, system_prompt, api_key, referer)
            if current != model:
                print(f"      (Note: configured model '{model}' wasn't available on "
                      f"OpenRouter -- used '{current}' instead. You can update "
                      f"openrouter_model in config.json to '{current}' to skip this "
                      f"lookup next time.)")
            return script
        except Exception as e:
            last_error = e
            is_not_found = isinstance(e, urllib.error.HTTPError) and e.code == 404
            if is_not_found and not fetched_fallbacks:
                fetched_fallbacks = True
                extra = _get_free_model_candidates(api_key, referer, exclude=tried)
                for m in extra[:5]:
                    if m not in models_to_try:
                        models_to_try.append(m)

    raise RuntimeError(
        "OpenRouter didn't return a usable script from any available free model "
        f"(tried: {', '.join(tried)}). Last error: {last_error}"
    )
