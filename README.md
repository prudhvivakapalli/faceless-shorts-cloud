# Faceless Shorts App — cloud runner (GitHub Actions, free)

Same pipeline as the PC version (script → media → captioned video → music →
YouTube upload), rebuilt to run on GitHub's free Actions runners instead of
your own machine. Nothing has to stay on/connected for this to work.

## One-time setup

1. **Create this repo.** Push/upload everything in this folder to a new
   **public** GitHub repository (public = unlimited free Actions minutes;
   nothing sensitive lives in the code, only in secrets below).

2. **Add repo secrets** — Settings → Secrets and variables → Actions → New
   repository secret. Every value below already exists on your PC; nothing
   needs to be typed from scratch:

   | Secret name | Where the value comes from |
   |---|---|
   | `OPENROUTER_API_KEY` | `config.json` → `openrouter_api_key` |
   | `OPENROUTER_MODEL` | `config.json` → `openrouter_model` (optional — defaults to the free model already in use if you skip this) |
   | `PEXELS_API_KEY` | `config.json` → `pexels_api_key` |
   | `PIXABAY_API_KEY` | `config.json` → `pixabay_api_key` |
   | `YOUTUBE_CLIENT_SECRET` | the full contents of `client_secret.json`, pasted as-is |
   | `YOUTUBE_TOKEN` | the full contents of `token.json`, pasted as-is |

3. **Enable Actions** on the repo if it isn't already (Settings → Actions →
   General → Allow all actions).

That's it — no server, no VM, nothing to keep running.

## How a run works

Each run is triggered with one input, `request_json`, the exact same JSON
shape as a `queue/*.json` file on your PC: `{"topic", "upload", "privacy",
"publish_slot", "script"}`. The workflow:
1. Installs ffmpeg, fluidsynth, and the caption font fresh (takes ~30s,
   free every time).
2. Rebuilds `config.json` and your YouTube credentials from the secrets
   above — nothing is stored in the repo itself.
3. Runs `make_short.py` exactly like the watcher does locally.
4. Commits the result JSON to `results/` in this repo, so it can be read
   back the same way results/ has always worked.

Runs are serialized (one at a time), matching how the local watcher
processes its queue.

## What's different from the PC version

- **Music**: your local `music_library/` (the Incompetech pack) isn't
  included here — it's too large for a repo. Every cloud video uses the
  built-in original-music fallback (`lib/music_composer.py`) instead. Drop
  a small curated set of tracks into a `music_library/` folder here later
  if you want sourced music back; nothing else needs to change.
- **Fonts**: uses Ubuntu's built-in DejaVu Sans Bold instead of Arial —
  visually very close, installed automatically by the workflow.
- **Everything else** (script writing, image sourcing, video assembly,
  captions, scheduled publishing) is identical, same code.

## Cost

Genuinely free. A public repo gets unlimited GitHub Actions minutes, and
each video takes roughly 1–3 minutes to build — there's no realistic volume
of Shorts that would ever hit a limit or a bill.
