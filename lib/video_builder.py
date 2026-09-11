"""
Assembles the captioned vertical video with ffmpeg only -- no AI, no Canva,
no browser automation. Adapted from the manually-built pipeline: blurred
full-bleed background + contained foreground image + bottom-anchored,
width-safe wrapped captions with fades between scenes.
"""
import os
import shutil

from PIL import Image, ImageFont

from . import proc

W, H = 1080, 1920
FRAME_ASPECT = W / H  # 0.5625 -- images narrower/taller than this fill the
# whole frame height when scaled to fit; images wider than this (closer to
# square, e.g. 4:6 ~= 0.667) leave a blurred strip above and below instead.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
    "C:\\Windows\\Fonts\\arialbd.ttf",  # Windows fallback
]
_SYSTEM_FONT = next((f for f in FONT_CANDIDATES if os.path.exists(f)), FONT_CANDIDATES[0])
FONTSIZE = 52
LINE_SPACING = 4
MAX_TEXT_WIDTH = 880
TOP_SAFE_MARGIN = 150  # keeps top-anchored captions clear of the phone's own
# status bar (clock/battery/signal) and YouTube's back-button/UI strip at the
# very top of the screen -- both sit outside our video but visually overlap
# the top few percent of the frame, so a caption starting right at y=0 reads
# as "cut off" even though our own video file is fine.
# Captions default to vertically centered rather than bottom-anchored.
# YouTube Shorts overlays its own title/description text and the progress
# bar in roughly the bottom 300-350px of the frame (plus like/comment/share
# icons down the right edge), so a bottom-anchored caption box collided with
# that UI. Centering vertically clears both that bottom strip and the small
# top strip (back button, remix icon) regardless of how many lines wrap --
# *when the foreground image fills the full frame height* (true 9:16-ish
# photos). When it doesn't (a squarer/wider source photo, e.g. 4:6), the
# scaled-to-fit image only occupies a band in the middle of the frame with
# blurred background above and below it -- centering the caption there lands
# it right on top of the subject instead of the empty strip, so those scenes
# anchor the caption above the image instead (see _caption_y_expr below).


def _ffmpeg_path(path: str) -> str:
    """Forward-slash form of a path for embedding in an ffmpeg filtergraph
    option value (fontfile=..., textfile=...). Deliberately does NOT make
    the path absolute: ffmpeg's filtergraph parser mis-splits on the ':' in
    a Windows drive letter (C:/...) no matter how it's escaped or quoted, so
    the fix is to avoid drive-letter paths in filter strings entirely and
    use paths relative to this app's own working directory instead."""
    return path.replace("\\", "/")


def _local_font_path() -> str:
    """A copy of the caption font that's safe to reference from an ffmpeg
    filter string. The Windows system font lives at C:\\Windows\\Fonts\\...,
    whose drive-letter colon breaks ffmpeg's filtergraph parser -- so a copy
    is kept in a `fonts/` folder next to this app instead, and referenced by
    a plain path relative to that folder (no drive letter, no colon)."""
    if not (len(_SYSTEM_FONT) > 1 and _SYSTEM_FONT[1] == ":"):
        return _SYSTEM_FONT  # e.g. the Linux candidate -- no colon problem
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_dir = os.path.join(app_root, "fonts")
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, os.path.basename(_SYSTEM_FONT))
    if not os.path.exists(local_path):
        shutil.copyfile(_SYSTEM_FONT, local_path)
    return f"fonts/{os.path.basename(_SYSTEM_FONT)}"  # relative to this app's cwd


FONT = _local_font_path()
_pil_font = ImageFont.truetype(_SYSTEM_FONT, FONTSIZE)
FONT_ARG = _ffmpeg_path(FONT)


def wrap_text(text: str, max_width: int = MAX_TEXT_WIDTH) -> str:
    """Wraps text into lines that fit max_width using real font metrics, so
    captions never run off the sides of the frame."""
    words = text.replace("\n", " ").split()
    lines, current = [], ""
    for word in words:
        trial = (current + " " + word).strip()
        w = _pil_font.getbbox(trial)[2] - _pil_font.getbbox(trial)[0]
        if w <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)


def scene_duration(caption: str) -> float:
    """Rough reading-pace heuristic: longer captions get more time on screen,
    clamped to a sensible Shorts-pacing range. The upper end was raised from
    6.0s to 8.5s (and the per-word rate nudged up) after captions running
    long (3 wrapped lines, ~18 words) were getting clipped to only 6s --
    not enough time to read on-screen text with no voiceover to pace against.
    The style profile now also asks for shorter captions (~12-14 words) by
    default, so this higher cap should mostly act as a safety net rather
    than the common case."""
    words = len(caption.split())
    return max(3.2, min(8.5, 2.6 + 0.32 * words))


def _drawtext_filter(base_label: str, cap_path_arg: str, duration: float, y_expr: str = "(h-text_h)/2") -> str:
    """The caption overlay + fade chain shared by all three clip types
    (video background, still-image background, plain-color background).
    y_expr is an ffmpeg drawtext expression for the caption's vertical
    position -- vertically centered by default, but callers can pass a
    fixed pixel value instead (see _caption_y_expr) to anchor it above an
    image that doesn't fill the full frame height."""
    return (
        f"[{base_label}]drawtext=fontfile='{FONT_ARG}':textfile='{cap_path_arg}':"
        # expansion=none turns off drawtext's own "%{...}" template syntax --
        # without it, a literal '%' in a caption (e.g. "10%") is parsed as
        # the start of an expansion sequence and ffmpeg fails with
        # "Stray % near ...". Captions are plain generated text, never meant
        # to use ffmpeg's expansion features, so this is always safe.
        f"expansion=none:"
        f"fontcolor=white:fontsize={FONTSIZE}:line_spacing={LINE_SPACING}:"
        f"box=1:boxcolor=black@0.55:boxborderw=24:"
        f"x=(w-text_w)/2:y={y_expr}:"
        f"enable='between(t,0.25,{duration - 0.15})'[texted];"
        f"[texted]fade=t=in:st=0:d=0.25:alpha=0,fade=t=out:st={duration - 0.3}:d=0.3:alpha=0[vout]"
    )


def _caption_y_expr(media_path: str, caption: str) -> str:
    """Decides where the caption sits for an image scene. If the image is
    tall/narrow enough to fill the full frame height once scaled to fit
    (aspect ratio <= 9:16-ish), it's centered as before. If the image is
    wider/squarer than that (e.g. 4:6), scaling it to fit leaves a blurred
    background strip above and below it -- in that case the caption is
    anchored inside that top strip instead of dead-center on the subject."""
    try:
        with Image.open(media_path) as im:
            iw, ih = im.size
        img_aspect = iw / ih
        if img_aspect <= FRAME_ASPECT:
            return "(h-text_h)/2"  # fills the full height -- center is fine

        fg_h = W / img_aspect  # scaled-to-fit height when width-constrained
        fg_top = (H - fg_h) / 2  # size of the blurred strip above the image

        wrapped_lines = wrap_text(caption).count("\n") + 1
        text_block_h = wrapped_lines * (FONTSIZE + LINE_SPACING) + 48  # + box padding

        # Never go above TOP_SAFE_MARGIN, even if that means overlapping the
        # top of the image itself -- overlapping the picture a little still
        # reads fine, while overlapping the phone's status bar/YouTube's own
        # UI reads as visibly broken (the top line looks cut in half).
        top_y = max(TOP_SAFE_MARGIN, fg_top - text_block_h - 20)
        return str(int(top_y))
    except Exception:
        return "(h-text_h)/2"  # if anything goes wrong, fall back to the old behavior


def _build_clip(media_path, media_type, caption, duration, out_path, cap_path):
    with open(cap_path, "w", encoding="utf-8") as f:
        f.write(wrap_text(caption))
    cap_path_arg = _ffmpeg_path(cap_path)

    if media_type == "video" and media_path and os.path.exists(media_path):
        # Fill the vertical frame by cropping (standard for repurposing stock
        # b-roll into Shorts/TikTok format); loop covers the rare clip
        # shorter than this scene's caption duration, -t trims the rest.
        filter_complex = (
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},setsar=1[base];" + _drawtext_filter("base", cap_path_arg, duration)
        )
        cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", media_path,
            "-t", str(duration),
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-r", "30", "-pix_fmt", "yuv420p",
            out_path,
        ]
    elif media_type == "image" and media_path and os.path.exists(media_path):
        y_expr = _caption_y_expr(media_path, caption)
        filter_complex = (
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},boxblur=25:2,eq=brightness=-0.08[bg];"
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base];" + _drawtext_filter("base", cap_path_arg, duration, y_expr)
        )
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", media_path,
            "-t", str(duration),
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-r", "30", "-pix_fmt", "yuv420p",
            out_path,
        ]
    else:
        # No video or image was found for this scene -- fall back to a
        # plain dark background rather than guessing with an irrelevant one.
        filter_complex = _drawtext_filter("0:v", cap_path_arg, duration)
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x1a1a1a:s={W}x{H}:d={duration}",
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-r", "30", "-pix_fmt", "yuv420p",
            out_path,
        ]
    proc.run(cmd)


def build_video(scenes: list, media_results: list, out_dir: str, out_name: str = "short_draft_silent.mp4") -> str:
    """scenes: [{"caption": ...}, ...]
    media_results: parallel list of {"type": "video"|"image"|None, "path": str or None, "credit": ...}
    Returns the path to the assembled silent video.
    """
    cap_dir = os.path.join(out_dir, "captions")
    os.makedirs(cap_dir, exist_ok=True)

    clip_paths = []
    for i, (scene, media) in enumerate(zip(scenes, media_results)):
        duration = scene_duration(scene["caption"])
        out_path = os.path.join(out_dir, f"clip_{i+1:02d}.mp4")
        cap_path = os.path.join(cap_dir, f"clip_{i+1:02d}.txt")
        _build_clip(media.get("path"), media.get("type"), scene["caption"], duration, out_path, cap_path)
        clip_paths.append(out_path)

    concat_list = os.path.join(out_dir, "concat_list.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in clip_paths:
            # ffmpeg's concat demuxer resolves relative paths against the
            # *list file's own* folder, not this app's working directory --
            # since concat_list.txt and every clip live in the same out_dir,
            # the bare filename is all that's needed (and avoids doubling
            # out_dir onto itself, and the drive-letter colon problem too).
            f.write(f"file '{os.path.basename(p)}'\n")

    final_out = os.path.join(out_dir, out_name)
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        final_out,
    ]
    proc.run(cmd)
    return final_out
