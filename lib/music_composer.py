"""
Fully original, procedurally-composed background music -- no sourced audio,
so zero licensing questions. A short "curious / trivia-reveal" motif on a
sustained instrument (smoother than a plucky one) over a soft pad, rendered
with FluidSynth (open source) and a free General MIDI soundfont. Each call
picks a random key and a small tempo jitter so repeated videos don't all
sound identical.

Requires two things installed locally (see README.md):
  - the `fluidsynth` command-line program
  - a General MIDI .sf2 soundfont file (a free one is linked in the README)
"""
import os
import random

import mido
from mido import Message, MidiFile, MidiTrack, MetaMessage

from . import proc

TICKS_PER_BEAT = 480
VIBRAPHONE = 11
WARM_PAD = 89

# A handful of root notes to pick from so successive videos vary a bit.
ROOT_CHOICES = [57, 60, 62, 64]  # A3, C4, D4, E4


def _build_midi(total_seconds: float, out_path: str, seed: int = None):
    rng = random.Random(seed)
    bpm = rng.randint(88, 100)
    root = rng.choice(ROOT_CHOICES)
    # Dorian mode relative to the chosen root.
    scale = [root + s for s in (0, 2, 3, 5, 7, 9, 10, 12)]

    mid = MidiFile(ticks_per_beat=TICKS_PER_BEAT)

    melody = MidiTrack()
    mid.tracks.append(melody)
    melody.append(MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    melody.append(Message("program_change", program=VIBRAPHONE, channel=0, time=0))

    motif_a = [0, 2, 4, 5, 4, 2, 4, None]
    motif_b = [0, 2, 4, 6, 5, 4, 2, None]
    durations = [300, 300, 300, 300, 300, 300, 600, 300]

    def add_motif(degrees):
        for deg, dur in zip(degrees, durations):
            if deg is None:
                melody.append(Message("note_off", note=0, velocity=0, channel=0, time=dur))
                continue
            note = scale[deg % len(scale)] + 12 * (deg // len(scale))
            melody.append(Message("note_on", note=note, velocity=58, channel=0, time=0))
            melody.append(Message("note_off", note=note, velocity=0, channel=0, time=dur))

    beats_per_second = bpm / 60
    ticks_per_second = TICKS_PER_BEAT * beats_per_second
    total_ticks_needed = int((total_seconds + 2) * ticks_per_second)  # +2s pad, trimmed later

    elapsed, toggle = 0, True
    while elapsed < total_ticks_needed:
        add_motif(motif_a if toggle else motif_b)
        elapsed += sum(durations)
        toggle = not toggle
    melody.append(MetaMessage("end_of_track", time=0))

    pad = MidiTrack()
    mid.tracks.append(pad)
    pad.append(Message("program_change", program=WARM_PAD, channel=1, time=0))
    pad_notes = [root - 12, root - 5]  # root and fifth, an octave down
    pad_note_len = TICKS_PER_BEAT * 4
    elapsed = 0
    while elapsed < total_ticks_needed:
        for n in pad_notes:
            pad.append(Message("note_on", note=n, velocity=34, channel=1, time=0))
        pad.append(Message("note_off", note=pad_notes[0], velocity=0, channel=1, time=pad_note_len))
        pad.append(Message("note_off", note=pad_notes[1], velocity=0, channel=1, time=0))
        elapsed += pad_note_len
    pad.append(MetaMessage("end_of_track", time=0))

    mid.save(out_path)


def compose_and_render(total_seconds: float, work_dir: str, soundfont_path: str,
                        fluidsynth_bin: str = "fluidsynth", seed: int = None) -> str:
    """Returns the path to a rendered, smoothed, loudness-normalized WAV file
    trimmed to total_seconds."""
    midi_path = os.path.join(work_dir, "theme.mid")
    raw_wav = os.path.join(work_dir, "theme_raw.wav")
    final_wav = os.path.join(work_dir, "theme_final.wav")

    _build_midi(total_seconds, midi_path, seed=seed)

    # Newer FluidSynth builds parse strictly: all options must come before
    # the soundfont/MIDI filenames, or it rejects them ("-F is an illegal
    # option at this place").
    proc.run([fluidsynth_bin, "-ni", "-F", raw_wav, "-r", "44100", soundfont_path, midi_path])

    proc.run(
        [
            "ffmpeg", "-y", "-i", raw_wav,
            "-af", f"lowpass=f=9000,aecho=0.8:0.6:35:0.22,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-t", str(total_seconds),
            final_wav,
        ]
    )
    return final_wav
