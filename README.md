# Wild Shorts autopilot

Makes one animal-facts YouTube Short a day and schedules it, on GitHub Actions (free for public repositories).

- `scripts/` hand-written scripts, posted in number order (format in `wild/script.py`)
- `wild/` the pipeline: narration (edge-tts), footage (iNaturalist, Wikimedia Commons, optional Pexels/Pixabay),
  edit and captions (`render.py`), sound (`sound.py`), upload (`youtube.py`)
- `state/posted.json` what has been scheduled (committed by the bot)

Run locally: `python -m wild.pipeline make scripts/001-electric-eel.md` → `out/`.
Check scripts: `python -m wild.pipeline check`. Add a batch: `python tools/add_scripts.py batch.txt`.

All media is CC0 / CC BY / CC BY-SA / Pexels / Pixabay licensed and credited on screen and in every description.
Music by Kevin MacLeod (incompetech.com), CC BY 4.0.
