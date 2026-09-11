#!/usr/bin/env python3
"""
make_short.py -- end-to-end faceless YouTube Shorts pipeline, running
entirely on your own machine after the one-time setup in README.md.

The only step that calls an external AI is script writing, and that goes to
OpenRouter (your own API key, works with free-tier models) -- not Claude.
Everything else (image sourcing, video assembly, music, upload) is plain
local code with zero token cost.

Usage:
    python make_short.py --topic "a surprising true story about someone" --privacy private

This module is also imported by watcher.py (see README.md's "Run this from
your phone" section) -- run_pipeline() below is the shared core so both the
interactive command line and the background watcher behave identically.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from lib import script_writer, image_sourcer, video_builder, music_composer, music_library, uploader, proc, schedule


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        # A plain exception, not SystemExit -- this needs to be catchable by
        # watcher.py's normal `except Exception` handling so a bad config
        # fails just that one request instead of silently killing the whole
        # background watcher (SystemExit is deliberately NOT an Exception
        # subclass in Python, which caused exactly that bug here before).
        raise FileNotFoundError(
            f"{path} not found. Copy config.example.json to config.json and fill in "
            "your OpenRouter API key and soundfont path first."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_pipeline(topic: str, config: dict, upload: bool = False, privacy: str = "private",
                  work_dir: str = None, log=print, script: dict = None,
                  publish_slot: str = None) -> dict:
    """Runs the full script -> media -> video -> music -> (optional) upload
    pipeline for one topic. `log` is called with each progress line (defaults
    to print; the watcher passes something that captures to a string instead).

    `script`, if given, is a ready-made {"title", "scenes": [{"caption",
    "image_query"}, ...], "tags", "description"} dict -- this skips the
    OpenRouter script-writing call entirely and uses these exact facts/
    wording instead (used when the topic's content was supplied directly
    rather than left for the AI to invent from a one-line topic).

    `publish_slot`, if given, is a daily IST time like "19:00" -- the video
    still builds and uploads right away, but is scheduled (via YouTube's own
    publishAt) to go public at the next occurrence of that time instead of
    immediately, regardless of `privacy`. See lib/schedule.py.

    Returns {"final_video": path, "title": ..., "youtube_url": str or None,
    "publish_at": str or None}.
    Raises on any failure -- callers decide how to report that (the CLI
    entry point below prints it; the watcher writes it to a result file).
    """
    slug = "".join(c if c.isalnum() else "_" for c in topic.lower())[:40].strip("_")
    work_dir = work_dir or os.path.join("output", slug or "short")
    os.makedirs(work_dir, exist_ok=True)

    if script is not None:
        log(f"[1/4] Using the pre-written script for: {topic!r} (skipping AI script-writing) ...")
    else:
        log(f"[1/4] Writing script for: {topic!r} via {config['openrouter_model']} ...")
        script = script_writer.generate_script(
            topic=topic,
            api_key=config["openrouter_api_key"],
            model=config["openrouter_model"],
        )
    log(f"      Title: {script['title']}")
    for i, scene in enumerate(script["scenes"], 1):
        log(f"      Scene {i}: {scene['caption']!r}  (image: {scene['image_query']!r})")

    with open(os.path.join(work_dir, "script.json"), "w", encoding="utf-8") as f:
        json.dump(script, f, indent=2)

    log("\n[2/4] Sourcing media (Pexels/Pixabay video, then Commons/Openverse/Pexels/Pixabay photos) ...")
    images_dir = os.path.join(work_dir, "images")
    api_keys = {
        "pexels": config.get("pexels_api_key", ""),
        "pixabay": config.get("pixabay_api_key", ""),
    }
    media_results = image_sourcer.fetch_scene_media(script["scenes"], images_dir, api_keys)
    for i, media in enumerate(media_results, 1):
        if media["path"]:
            log(f"      Scene {i}: found {media['type']} -- credit: {media['credit']}")
        else:
            log(f"      Scene {i}: NO video or image found -- will use a plain background. "
                f"Consider dropping your own photo/clip at {images_dir}/scene_{i:02d}.jpg (or .mp4) "
                f"and rerunning with --work-dir {work_dir} to reuse this script.")

    with open(os.path.join(work_dir, "image_credits.json"), "w", encoding="utf-8") as f:
        json.dump(media_results, f, indent=2)

    log("\n[3/4] Building video + music ...")
    silent_path = video_builder.build_video(script["scenes"], media_results, work_dir)
    total_seconds = sum(video_builder.scene_duration(s["caption"]) for s in script["scenes"])

    library_dir = config.get("music_library_dir", "music_library")
    music_wav_path = os.path.join(work_dir, "theme_final.wav")
    music_credit = None
    try:
        picked = music_library.prepare_track(total_seconds, library_dir, music_wav_path)
        music_wav = picked["path"]
        # Some libraries (e.g. Incompetech/Kevin MacLeod, CC BY) require a
        # credit line per track in the video description. If an
        # ATTRIBUTION.json ({"filename.mp3": "credit line", ...}) exists next
        # to the tracks, look up the exact line for whichever track got
        # picked so it's impossible to lose track of which credit goes where.
        attribution_path = os.path.join(library_dir, "ATTRIBUTION.json")
        if os.path.exists(attribution_path):
            with open(attribution_path, "r", encoding="utf-8") as f:
                attribution_map = json.load(f)
            music_credit = attribution_map.get(picked["track"])
        if music_credit:
            log(f"      Using '{picked['track']}' from your music library. "
                f"Credit needed in the video description: {music_credit}")
        else:
            log(f"      Using '{picked['track']}' from your music library ({library_dir}).")
    except FileNotFoundError:
        log(f"      No tracks found in {library_dir}/ -- generating original music instead. "
            f"(Drop .mp3/.wav files in there any time to use your own music going forward.)")
        music_wav = music_composer.compose_and_render(
            total_seconds=total_seconds,
            work_dir=work_dir,
            soundfont_path=config["soundfont_path"],
            fluidsynth_bin=config.get("fluidsynth_bin", "fluidsynth"),
        )

    final_path = os.path.join(work_dir, "final.mp4")
    proc.run(
        [
            "ffmpeg", "-y", "-i", silent_path, "-i", music_wav,
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", final_path,
        ]
    )
    log(f"      Built: {final_path}")

    result = {"final_video": final_path, "title": script["title"], "youtube_url": None,
               "music_credit": music_credit, "publish_at": None}

    if upload:
        publish_at = schedule.next_ist_slot(publish_slot) if publish_slot else None
        if publish_at:
            log(f"\n[4/4] Uploading to YouTube (scheduled public at {publish_at} UTC, "
                f"slot {publish_slot} IST) ...")
        else:
            log("\n[4/4] Uploading to YouTube ...")
        tags = script.get("tags", [])
        description = script.get("description", "")
        if music_credit:
            # CC BY tracks (e.g. Kevin MacLeod/incompetech) require a credit
            # line in the description -- append it automatically so a real
            # music-library track never gets uploaded without it.
            description = f"{description}\n\n🎵 Music: {music_credit}"
        video_id = uploader.upload_video(
            client_secret_path=config["client_secret_path"],
            token_path=config["token_path"],
            file_path=final_path,
            title=script["title"],
            description=description,
            tags=tags,
            privacy=privacy,
            publish_at=publish_at,
        )
        result["youtube_url"] = f"https://youtube.com/watch?v={video_id}"
        result["publish_at"] = publish_at
    else:
        log(f"\n[4/4] Skipped upload (pass --upload to publish). File is ready at {final_path}")

    return result


def _run_one_request(request_file: str, result_file: str, config_path: str):
    """Runs exactly one request end-to-end in THIS process and writes the
    full result JSON to result_file -- used by watcher.py, which spawns a
    brand-new `python make_short.py ...` process per request instead of
    importing run_pipeline() once and reusing that same long-lived process
    forever. That matters because Python only reads a .py file the first
    time it's imported: a long-lived watcher process keeps using whatever
    version of image_sourcer.py/script_writer.py/etc. was on disk when IT
    started, so a bug fix pushed to those files would otherwise sit inert
    until someone physically restarted the watcher. Running each request as
    its own fresh process means every single video always uses whatever
    code is on disk right now -- a fix takes effect on the very next queued
    video, no restart, ever."""
    with open(request_file, "r", encoding="utf-8") as f:
        req = json.load(f)

    topic = req.get("topic", "")
    upload = bool(req.get("upload", False))
    privacy = req.get("privacy", "private")
    script = req.get("script")
    publish_slot = req.get("publish_slot")  # e.g. "19:00" (IST) -- see lib/schedule.py

    log_lines = []

    def log(line):
        print(line, flush=True)
        log_lines.append(str(line))

    result = {"topic": topic, "upload_requested": upload, "privacy": privacy, "publish_slot": publish_slot}
    try:
        config = load_config(config_path)
        pipeline_result = run_pipeline(topic, config, upload=upload, privacy=privacy, log=log, script=script,
                                        publish_slot=publish_slot)
        result["status"] = "success"
        result["final_video"] = pipeline_result["final_video"]
        result["title"] = pipeline_result["title"]
        result["youtube_url"] = pipeline_result["youtube_url"]
        result["music_credit"] = pipeline_result.get("music_credit")
        result["publish_at"] = pipeline_result.get("publish_at")
    except Exception:
        import traceback
        result["status"] = "error"
        result["error"] = traceback.format_exc()
    result["log"] = "\n".join(log_lines)

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--topic", help="What the short should be about")
    parser.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--work-dir", default=None, help="Where to build files (default: ./output/<slug>)")
    parser.add_argument("--upload", action="store_true", help="Upload to YouTube when done (otherwise just builds the file)")
    parser.add_argument("--request-file", default=None,
                         help="(used by watcher.py) Run exactly one request from this JSON file "
                              "{topic, upload, privacy, script} in this fresh process, instead of --topic.")
    parser.add_argument("--result-file", default=None,
                         help="(used by watcher.py) Where to write the full result JSON when --request-file is used.")
    args = parser.parse_args()

    if args.request_file:
        _run_one_request(args.request_file, args.result_file, args.config)
        return

    if not args.topic:
        parser.error("--topic is required (or use --request-file)")

    config = load_config(args.config)
    run_pipeline(args.topic, config, upload=args.upload, privacy=args.privacy, work_dir=args.work_dir)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, FileNotFoundError) as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
