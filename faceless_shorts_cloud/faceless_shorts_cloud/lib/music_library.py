"""
Picks a track from your own local music library (music_library/ folder) and
trims/fades it to fit a video's length -- an alternative to the procedural
MIDI composer in music_composer.py, for when you've dropped in real tracks
(from YouTube's Audio Library, a CC0 pack, or anything you have rights to
use). No AI, no network calls: just ffmpeg.

To add more music later: just drop more .mp3/.wav/.m4a/.flac/.ogg files into
music_library/ -- nothing else to configure. This module picks a random one
each run, avoiding an exact repeat of the last track used when there's more
than one to choose from (tracked in music_library/.last_used).
"""
import json
import os
import random

from . import proc

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".flac", ".ogg")
STATE_FILE = ".last_used.json"


def list_tracks(library_dir: str) -> list:
    """Recursively finds every audio file anywhere under library_dir -- so a
    bulk drop-in (e.g. a whole extracted "catalog/Genre/*.mp3" zip) works
    without flattening it by hand first. Skips macOS zip junk (__MACOSX/ and
    "._"-prefixed resource-fork files, which share the real file's extension
    but aren't playable audio)."""
    if not os.path.isdir(library_dir):
        return []
    tracks = []
    for root, dirs, files in os.walk(library_dir):
        dirs[:] = [d for d in dirs if d != "__MACOSX"]
        for f in files:
            if f.startswith("._"):
                continue
            if f.lower().endswith(AUDIO_EXTENSIONS):
                tracks.append(os.path.join(root, f))
    return sorted(tracks)


def _pick_track(library_dir: str, tracks: list) -> str:
    state_path = os.path.join(library_dir, STATE_FILE)
    last_used = None
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                last_used = json.load(f).get("last_used")
        except Exception:
            pass

    choices = [t for t in tracks if t != last_used] or tracks
    picked = random.choice(choices)

    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"last_used": picked}, f)
    except Exception:
        pass  # non-essential bookkeeping; fine if it fails

    return picked


def prepare_track(total_seconds: float, library_dir: str, out_wav_path: str, fade_out: float = 1.5) -> dict:
    """Picks a track, trims it to total_seconds with a fade-out, normalizes
    loudness, and writes a WAV ready to mux into the video. Raises
    FileNotFoundError if the library is empty -- callers should fall back to
    music_composer.compose_and_render in that case.

    Returns {"path": out_wav_path, "track": the source filename picked}.
    Some free-to-use libraries (e.g. Incompetech/Kevin MacLeod, CC BY) require
    crediting the specific track in the video description -- returning which
    file was picked lets the caller log that so you know which credit line to
    use for a given video, rather than having to guess or open every video."""
    tracks = list_tracks(library_dir)
    if not tracks:
        raise FileNotFoundError(f"No audio files found in {library_dir}")

    track = _pick_track(library_dir, tracks)
    fade_start = max(0.0, total_seconds - fade_out)

    proc.run(
        [
            "ffmpeg", "-y", "-i", track,
            "-t", str(total_seconds),
            "-af", f"afade=t=out:st={fade_start}:d={fade_out},loudnorm=I=-16:TP=-1.5:LRA=11",
            out_wav_path,
        ]
    )
    return {"path": out_wav_path, "track": os.path.basename(track)}
