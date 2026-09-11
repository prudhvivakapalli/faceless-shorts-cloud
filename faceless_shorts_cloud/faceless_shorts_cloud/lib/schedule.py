"""
schedule.py -- turns a daily IST time slot (like "19:00") into the exact
UTC timestamp YouTube's publishAt field needs, so a video that finishes
building at any random moment still lands on the correct scheduled slot.

Why this exists: the pipeline builds a video whenever the watcher gets to
it (could be 6am, could be 11pm -- media sourcing and rendering take a
variable amount of time). But we don't want it going PUBLIC the moment
it's built; we want it to go public at a specific time of day that matches
when the channel's actual audience is on YouTube (see YouTube Studio ->
Audience -> "When your viewers are on YouTube"). YouTube's own scheduling
feature handles that split cleanly: upload the video as private with a
publishAt timestamp, and YouTube itself flips it to public at that exact
moment, regardless of when the upload actually finished.

next_ist_slot("19:00") always returns the NEXT time 7:00 PM IST occurs --
today if it hasn't happened yet, otherwise tomorrow. That means the same
slot name can be reused forever across every request without ever having
to think about dates.
"""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def next_ist_slot(hh_mm: str) -> str:
    """hh_mm like "19:00" (24-hour, IST). Returns an RFC3339 UTC timestamp
    string in the exact format YouTube's status.publishAt expects, e.g.
    "2026-09-10T13:30:00Z" -- for the next occurrence of that IST clock
    time (today if still in the future, otherwise tomorrow)."""
    hour, minute = (int(x) for x in hh_mm.split(":"))
    now_ist = datetime.now(IST)
    candidate = now_ist.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now_ist:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
