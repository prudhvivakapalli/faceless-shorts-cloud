"""
Finds and downloads real, reusable media (video first, photo as a fallback)
for each scene. Tries several free, openly-licensed sources in order and
moves to the next one whenever a source comes up empty for a given search
phrase -- no AI involved, this is plain search + download, and every source
used here explicitly licenses its content for reuse (unlike Google Image
Search, Instagram, X, or YouTube, where almost everything you'd find is
someone else's copyrighted work and not safe to republish in your own video).

For each scene, in order:
  1. Pexels video, then Pixabay video -- short stock clips, muted (this
     app's own music track is added later) and trimmed/looped in
     video_builder.py to exactly match that scene's on-screen caption time.
  2. If no video matches: Wikimedia Commons photo, then Openverse, then
     Pexels photo, then Pixabay photo (the original image-only chain).
  3. If nothing matches at all: video_builder.py falls back to a plain dark
     background for that scene.
Video and photo searches both need a free API key for Pexels/Pixabay
(pexels_api_key / pixabay_api_key in config.json) -- skipped silently if not
configured. Wikimedia Commons and Openverse need no key.

Coverage note: even with four sources, niche or politically closed-off
topics (e.g. everyday life inside North Korea) have little to no real
photojournalism or footage under an open license anywhere. When every
source and every fallback phrase comes up empty, this leaves the scene
media blank so you can drop in your own picture/clip by hand rather than
silently using an irrelevant one. It also avoids reusing the same media
across multiple scenes in one video, which otherwise happens easily on
under-covered topics where every fallback search converges on the same
handful of generic results.
"""
import json
import os
import re
import urllib.parse
import urllib.request

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API = "https://api.openverse.org/v1/images/"
PEXELS_PHOTO_API = "https://api.pexels.com/v1/search"
PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"
PIXABAY_PHOTO_API = "https://pixabay.com/api/"
PIXABAY_VIDEO_API = "https://pixabay.com/api/videos/"
USER_AGENT = "faceless-shorts-app/1.0 (personal, non-commercial use)"


def _get_json(url: str, headers: dict = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --- photo sources ----------------------------------------------------------

_POSTER_KEYWORDS = (
    "poster", "movie cover", "film cover", "dvd cover", "album cover",
    "book cover", "cover art", "logo", "wordmark", "title card",
)


def _looks_like_poster_or_cover(text: str) -> bool:
    """Free-text search on Commons/Openverse can just as easily return a
    movie poster, DVD/album cover, or promotional title card as it can a
    real photo -- these usually LOOK like they're "of" the person (their face
    is on it) but are graphic-design artwork, not a photograph, and are
    almost never actually openly licensed no matter what tag got attached to
    the upload. A person_name scene needs an actual photo, so anything whose
    title/description reads like cover art gets skipped rather than risking
    exactly that (this caught a real case: an "Elektra" movie poster getting
    used for a Jennifer Garner scene)."""
    text = (text or "").lower()
    return any(kw in text for kw in _POSTER_KEYWORDS)


def _search_commons(query: str, exclude: set, min_width: int = 700):
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap",
        "gsrnamespace": "6",  # File: namespace
        "gsrlimit": "15",
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|size",
        "iiurlwidth": "1600",
        "format": "json",
    }
    try:
        data = _get_json(COMMONS_API + "?" + urllib.parse.urlencode(params))
    except Exception:
        return None, None

    pages = data.get("query", {}).get("pages", {})
    ordered = sorted(pages.values(), key=lambda p: p.get("index", 0))
    for page in ordered:
        infos = page.get("imageinfo", [])
        if not infos:
            continue
        info = infos[0]
        if info.get("width", 0) < min_width:
            continue
        meta = info.get("extmetadata", {})
        license_short = meta.get("LicenseShortName", {}).get("value", "")
        if not license_short or "©" in license_short:
            continue
        object_name = meta.get("ObjectName", {}).get("value", "")
        if _looks_like_poster_or_cover(page.get("title", "")) or _looks_like_poster_or_cover(object_name):
            continue
        image_url = info.get("thumburl") or info.get("url")
        if not image_url or image_url in exclude:
            continue
        artist = meta.get("Artist", {}).get("value", "")
        artist = re.sub("<[^<]+?>", "", artist).strip() or "Wikimedia Commons contributor"
        credit = f"{artist} / {license_short} / via Wikimedia Commons"
        return image_url, credit
    return None, None


def _search_openverse(query: str, exclude: set, min_width: int = 700):
    params = {
        "q": query,
        "page_size": 20,
        "license_type": "commercial,modification",
        "mature": "false",
    }
    try:
        data = _get_json(OPENVERSE_API + "?" + urllib.parse.urlencode(params))
    except Exception:
        return None, None

    for item in data.get("results", []):
        width = item.get("width") or 0
        if width and width < min_width:
            continue
        if _looks_like_poster_or_cover(item.get("title", "")):
            continue
        image_url = item.get("url")
        if not image_url or image_url in exclude:
            continue
        creator = item.get("creator") or "Openverse contributor"
        license_label = f"CC {item.get('license', '').upper()} {item.get('license_version', '')}".strip()
        credit = f"{creator} / {license_label} / via Openverse"
        return image_url, credit
    return None, None


def _search_pexels_photo(query: str, exclude: set, api_key: str, min_width: int = 700):
    if not api_key:
        return None, None
    params = {"query": query, "per_page": 15}
    try:
        data = _get_json(
            PEXELS_PHOTO_API + "?" + urllib.parse.urlencode(params),
            headers={"Authorization": api_key, "User-Agent": USER_AGENT},
        )
    except Exception:
        return None, None

    for photo in data.get("photos", []):
        if photo.get("width", 0) and photo["width"] < min_width:
            continue
        src = photo.get("src", {})
        image_url = src.get("large2x") or src.get("large") or src.get("original")
        if not image_url or image_url in exclude:
            continue
        photographer = photo.get("photographer") or "Pexels contributor"
        credit = f"{photographer} / Pexels License / via Pexels"
        return image_url, credit
    return None, None


def _search_pixabay_photo(query: str, exclude: set, api_key: str, min_width: int = 700):
    if not api_key:
        return None, None
    params = {
        "key": api_key,
        "q": query,
        "image_type": "photo",
        "per_page": 20,
        "safesearch": "true",
    }
    try:
        data = _get_json(PIXABAY_PHOTO_API + "?" + urllib.parse.urlencode(params))
    except Exception:
        return None, None

    for hit in data.get("hits", []):
        if hit.get("imageWidth", 0) and hit["imageWidth"] < min_width:
            continue
        image_url = hit.get("largeImageURL") or hit.get("webformatURL")
        if not image_url or image_url in exclude:
            continue
        user = hit.get("user") or "Pixabay contributor"
        credit = f"{user} / Pixabay Content License / via Pixabay"
        return image_url, credit
    return None, None


# --- video sources -----------------------------------------------------------

def _pick_pexels_video_file(video_files: list):
    """Picks the mp4 closest to full-HD width (1080px) -- close enough to the
    output frame's own width (1080x1920) that scaling up to fill it doesn't
    visibly soften the footage, without downloading a needlessly huge 4K
    source clip that takes longer to fetch for no visible benefit."""
    candidates = [f for f in video_files if f.get("file_type") == "video/mp4"] or list(video_files)
    if not candidates:
        return None
    candidates.sort(key=lambda f: abs((f.get("width") or 0) - 1080))
    return candidates[0]


def _search_pexels_video(query: str, exclude: set, api_key: str, min_width: int = 480):
    if not api_key:
        return None, None
    params = {"query": query, "per_page": 15, "orientation": "portrait"}
    try:
        data = _get_json(
            PEXELS_VIDEO_API + "?" + urllib.parse.urlencode(params),
            headers={"Authorization": api_key, "User-Agent": USER_AGENT},
        )
    except Exception:
        return None, None

    for video in data.get("videos", []):
        file = _pick_pexels_video_file(video.get("video_files", []))
        if not file or (file.get("width", 0) and file["width"] < min_width):
            continue
        video_url = file.get("link")
        if not video_url or video_url in exclude:
            continue
        user = (video.get("user") or {}).get("name") or "Pexels contributor"
        credit = f"{user} / Pexels License / via Pexels"
        return video_url, credit
    return None, None


def _search_pixabay_video(query: str, exclude: set, api_key: str, min_width: int = 480):
    if not api_key:
        return None, None
    params = {"key": api_key, "q": query, "per_page": 20, "safesearch": "true"}
    try:
        data = _get_json(PIXABAY_VIDEO_API + "?" + urllib.parse.urlencode(params))
    except Exception:
        return None, None

    for hit in data.get("hits", []):
        videos = hit.get("videos", {})
        # Prefer "large" (Pixabay's highest tier) first now; fall back down
        # through medium/small/tiny only if large isn't available for this clip.
        file = videos.get("large") or videos.get("medium") or videos.get("small") or videos.get("tiny")
        if not file or (file.get("width", 0) and file["width"] < min_width):
            continue
        video_url = file.get("url")
        if not video_url or video_url in exclude:
            continue
        user = hit.get("user") or "Pixabay contributor"
        credit = f"{user} / Pixabay Content License / via Pixabay"
        return video_url, credit
    return None, None


def _search_wikipedia_person_image(person_name: str, exclude: set, min_width: int = 700):
    """Looks up the single Wikipedia article that best matches person_name and
    returns its lead ("page") image, rather than a free-text keyword search.

    This exists because plain keyword search against Commons/Openverse has
    repeatedly returned the wrong real person: another individual who happens
    to share the name (a namesake), or a group/composite photo whose caption
    text merely mentions the person alongside others. Wikipedia's lead image
    is tied to one specific, human-verified article about that exact named
    entity, so it's dramatically less likely to misfire on a namesake or a
    collage. Two steps: (1) find the best-matching article and the filename
    of its page image, (2) look that exact file up on Commons to recover a
    real license + artist credit. If the file isn't on Commons at all (common
    for non-free promotional images used under fair use), this returns
    nothing rather than guessing -- the caller falls back to the older
    free-text search chain."""
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": person_name,
        "gsrnamespace": "0",
        "gsrlimit": "1",
        "prop": "pageimages|pageprops",
        "piprop": "name",
        "format": "json",
    }
    try:
        data = _get_json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params))
    except Exception:
        return None, None

    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return None, None
    page = next(iter(pages.values()))
    if "disambiguation" in page.get("pageprops", {}):
        return None, None  # ambiguous name, no single best article -- don't guess
    filename = page.get("pageimage")
    if not filename:
        return None, None

    params2 = {
        "action": "query",
        "titles": f"File:{filename}",
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|size",
        "iiurlwidth": "1600",
        "format": "json",
    }
    try:
        data2 = _get_json(COMMONS_API + "?" + urllib.parse.urlencode(params2))
    except Exception:
        return None, None

    for p in data2.get("query", {}).get("pages", {}).values():
        infos = p.get("imageinfo", [])
        if not infos:
            continue  # not a Commons-hosted file (e.g. local non-free upload) -- skip
        info = infos[0]
        if info.get("width", 0) < min_width:
            continue
        meta = info.get("extmetadata", {})
        license_short = meta.get("LicenseShortName", {}).get("value", "")
        if not license_short or "©" in license_short:
            continue
        image_url = info.get("thumburl") or info.get("url")
        if not image_url or image_url in exclude:
            continue
        artist = meta.get("Artist", {}).get("value", "")
        artist = re.sub("<[^<]+?>", "", artist).strip() or "Wikimedia Commons contributor"
        credit = f"{artist} / {license_short} / via Wikimedia Commons"
        return image_url, credit
    return None, None


def _search_commons_category(person_name: str, exclude: set, min_width: int = 700):
    """Looks inside the Wikimedia Commons category named exactly after this
    person (nearly every notable individual has one, e.g. Category:Jennifer
    Garner) and picks a usable file from it.

    This is a second, independent line of defense alongside
    _search_wikipedia_person_image: that function can come back empty for a
    real, well-photographed person for reasons that have nothing to do with
    whether good photos exist -- e.g. Wikipedia's "page image" heuristic
    failing to recognize the infobox image, or the article title not lining
    up with the search query. A Commons category, by contrast, is a
    human-curated bucket of files about ONE specific entity (as opposed to a
    free-text search matching anyone/anything whose caption happens to
    mention the name), so it's nearly as reliable as the Wikipedia lookup
    and catches real people that lookup misses -- without falling all the
    way back to the free-text chain, which is what actually returned unrelated
    movie stills/poster art for a couple of people in testing."""
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{person_name}",
        "cmtype": "file",
        "cmlimit": "20",
        "format": "json",
    }
    try:
        data = _get_json(COMMONS_API + "?" + urllib.parse.urlencode(params))
    except Exception:
        return None, None
    members = data.get("query", {}).get("categorymembers", [])
    if not members:
        return None, None  # no category by this exact name -- not an error, just nothing to use

    params2 = {
        "action": "query",
        "titles": "|".join(m["title"] for m in members),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|size",
        "iiurlwidth": "1600",
        "format": "json",
    }
    try:
        data2 = _get_json(COMMONS_API + "?" + urllib.parse.urlencode(params2))
    except Exception:
        return None, None

    pages = data2.get("query", {}).get("pages", {})
    ordered = sorted(pages.values(), key=lambda p: p.get("index", 0))
    for page in ordered:
        infos = page.get("imageinfo", [])
        if not infos:
            continue
        info = infos[0]
        if info.get("width", 0) < min_width:
            continue
        meta = info.get("extmetadata", {})
        license_short = meta.get("LicenseShortName", {}).get("value", "")
        if not license_short or "©" in license_short:
            continue
        object_name = meta.get("ObjectName", {}).get("value", "")
        if _looks_like_poster_or_cover(page.get("title", "")) or _looks_like_poster_or_cover(object_name):
            continue
        image_url = info.get("thumburl") or info.get("url")
        if not image_url or image_url in exclude:
            continue
        artist = meta.get("Artist", {}).get("value", "")
        artist = re.sub("<[^<]+?>", "", artist).strip() or "Wikimedia Commons contributor"
        credit = f"{artist} / {license_short} / via Wikimedia Commons"
        return image_url, credit
    return None, None


def _person_candidate_queries(person_name: str, query: str) -> list:
    """Fallback ladder for a scene about a specific real, named individual.

    Unlike _candidate_queries (which strips down to the *tail* of the
    phrase, on the assumption the subject repeats and the visual detail at
    the end is what varies), a person's name is the one thing that must
    never get dropped -- losing it turns the search into a generic query
    that a stock library will happily "match" with a stranger's face. So
    this tries the full query first, then the bare name alone (which
    Wikimedia Commons in particular indexes very well via categories)."""
    candidates = [query]
    if person_name and person_name not in candidates:
        candidates.append(person_name)
    return candidates


def _candidate_queries(query: str) -> list:
    """Builds a ladder of fallback search phrases from one image_query.

    Tail-based fallbacks (keeping the end of the phrase) are tried before
    head-based ones, because the AI's image_query values are shaped like
    "<repeated subject> <specific visual>" (e.g. "North Korean women selling
    vegetables at an open air market") -- the specific visual at the end is
    what actually varies scene to scene, while the leading subject phrase
    repeats across every scene and, on a niche topic, converges every
    fallback onto the same few generic results.
    """
    words = query.split()
    candidates = [query]
    for n in (5, 3):
        if len(words) > n:
            tail = " ".join(words[-n:])
            if tail not in candidates:
                candidates.append(tail)
    for n in (2, 1):
        if len(words) > n:
            head = " ".join(words[:n])
            if head not in candidates:
                candidates.append(head)
    return candidates


def fetch_media_for_scene(query: str, out_dir: str, index: int, exclude: set, api_keys: dict = None,
                           person_name: str = None) -> dict:
    """Tries a ladder of search phrases. For each phrase, tries video sources
    before photo sources, and skips any media already used elsewhere in this
    video. Returns {"type": "video"|"image"|None, "path": str|None, "credit": str|None}.

    person_name changes the whole strategy: Pexels/Pixabay (video AND photo)
    are stock libraries of anonymous models -- they never actually have a
    specific real, named individual, so "matching" a query like "Napoleon
    Bonaparte portrait painting" just hands back an unrelated stranger's
    face/footage. When person_name is set, those sources are skipped
    entirely; only Wikimedia Commons and Openverse are tried (real chances
    of an actual likeness), using a name-preserving fallback ladder. If
    neither finds a real photo of that person, this returns a blank result
    (type None) on purpose -- video_builder.py then falls back to a plain
    background -- rather than ever showing the wrong person's face."""
    api_keys = api_keys or {}

    if person_name:
        # Two independent, high-precision sources are tried before ever
        # falling back to free-text search: the person's own Wikipedia
        # article image, then their Wikimedia Commons category. Either one
        # being tied to one verified entity is what makes them safe -- free
        # text search can (and, in testing, did) return a namesake, a
        # group/collage photo, or even unrelated movie stills/poster art
        # just because the caption text happened to match.
        for source in (
            lambda: _search_wikipedia_person_image(person_name, exclude),
            lambda: _search_commons_category(person_name, exclude),
        ):
            media_url, credit = source()
            if not media_url:
                continue
            out_path = os.path.join(out_dir, f"scene_{index:02d}.jpg")
            try:
                req = urllib.request.Request(media_url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    body = resp.read()
                with open(out_path, "wb") as f:
                    f.write(body)
                exclude.add(media_url)
                return {"type": "image", "path": out_path, "credit": credit}
            except Exception:
                continue  # try the next high-precision source, then the free-text chain

        candidates = _person_candidate_queries(person_name, query)
        sources = [
            lambda q: _search_commons(q, exclude),
            lambda q: _search_openverse(q, exclude),
        ]
        for candidate in candidates:
            for search in sources:
                media_url, credit = search(candidate)
                if not media_url:
                    continue
                out_path = os.path.join(out_dir, f"scene_{index:02d}.jpg")
                try:
                    req = urllib.request.Request(media_url, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        body = resp.read()
                    with open(out_path, "wb") as f:
                        f.write(body)
                except Exception:
                    continue
                exclude.add(media_url)
                return {"type": "image", "path": out_path, "credit": credit}
        return {"type": None, "path": None, "credit": None}

    # Generic (non-person) scenes draw from stock libraries with abundant
    # high-resolution supply, so these ask for noticeably higher minimums
    # than the defaults (1080 photos/720 video vs. 700/480) -- the final
    # frame is 1080x1920, so anything much smaller than that has to be
    # upscaled to fill it and comes out visibly soft. Person-photo lookups
    # (Wikipedia/Commons category, further down) keep the lower default
    # since real archival photos of historical figures are already a much
    # more constrained supply and shouldn't be filtered out over resolution.
    video_sources = [
        lambda q: _search_pexels_video(q, exclude, api_keys.get("pexels"), min_width=720),
        lambda q: _search_pixabay_video(q, exclude, api_keys.get("pixabay"), min_width=720),
    ]
    image_sources = [
        lambda q: _search_commons(q, exclude, min_width=1080),
        lambda q: _search_openverse(q, exclude, min_width=1080),
        lambda q: _search_pexels_photo(q, exclude, api_keys.get("pexels"), min_width=1080),
        lambda q: _search_pixabay_photo(q, exclude, api_keys.get("pixabay"), min_width=1080),
    ]

    for candidate in _candidate_queries(query):
        for kind, sources, ext in (("video", video_sources, ".mp4"), ("image", image_sources, ".jpg")):
            for search in sources:
                media_url, credit = search(candidate)
                if not media_url:
                    continue
                out_path = os.path.join(out_dir, f"scene_{index:02d}{ext}")
                try:
                    req = urllib.request.Request(media_url, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        body = resp.read()
                    with open(out_path, "wb") as f:
                        f.write(body)
                except Exception:
                    continue  # download failed -- try the next source/candidate
                exclude.add(media_url)
                return {"type": kind, "path": out_path, "credit": credit}
    return {"type": None, "path": None, "credit": None}


def fetch_scene_media(scenes: list, out_dir: str, api_keys: dict = None) -> list:
    """scenes: [{"caption": ..., "image_query": ..., "person_name": str|None}, ...]
    api_keys: {"pexels": "...", "pixabay": "..."} -- either may be empty/missing
    to skip that source (video AND photo searches on that provider).
    Returns a parallel list of dicts:
    {"type": "video"|"image"|None, "path": str|None, "credit": str|None}
    """
    os.makedirs(out_dir, exist_ok=True)
    results = []
    used = set()  # media URLs already picked for an earlier scene this run
    for i, scene in enumerate(scenes):
        results.append(fetch_media_for_scene(
            scene["image_query"], out_dir, i + 1, used, api_keys,
            person_name=scene.get("person_name"),
        ))
    return results
