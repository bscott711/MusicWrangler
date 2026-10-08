#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Makes clean edits of tracks with explicit lyrics by removing only the singer's
voice under each explicit word, so the music keeps playing underneath.

How it works:
1. Explicit words are found in the track's lyrics: the synced .ttml sidecar
   gamdl saves next to each download, or else the embedded lyrics tag.
2. Apple's .ttml is usually timed per line, not per word, so Whisper word
   timestamps pin each word down within its line.
3. Demucs separates the vocals around each word, and the vocal stem is
   subtracted from the original only inside the word's window (with short
   ramps). Every other sample stays identical to the original.
4. The result is re-encoded with cover art and tags carried over, the lyrics
   tag and .ttml censored, and Apple's explicit-content rating removed.

Needs the optional dependencies: uv sync --extra censor
"""

import argparse
import importlib.util
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

SAMPLE_RATE = 44100                # htdemucs works at 44.1 kHz, as does Apple Music audio
WHISPER_RATE = 16000
WHISPER_MODEL = "small.en"
CONTEXT = 5.0                      # seconds of audio either side of a lyric line for Whisper/Demucs
LINE_SLOP = 0.5                    # Apple's line timings can be a few hundred ms off
PAD_BEFORE, PAD_AFTER = 0.08, 0.09 # margin around Whisper's word boundaries
RAMP = 0.025                       # fade in/out of the vocal removal, to avoid clicks
CLEAN_SUFFIX = " (Clean Edit)"

# Words that make a lyric explicit, with their common variants (fucking,
# fuckin', shitty, bitches...). Milder words can be added with --extra-words.
EXPLICIT_PATTERNS = [
    r"(?:mother)?fuck\w*",
    r"\w*shit\w*",
    r"bitch\w*",
    r"cunts?",
    r"cock(?:s|sucker|suckers)?",
    r"dick(?:s|head|heads)?",
    r"puss(?:y|ies)",
    r"asshole\w*",
    r"bastards?",
    r"whores?",
    r"sluts?",
    r"nigg(?:a|as|az|er|ers)",
    r"fag(?:s|got|gots)?",
    r"goddamn\w*",
]


class CensorError(Exception):
    pass


def explicit_regex(extra_words: list[str] = ()) -> re.Pattern:
    alternatives = EXPLICIT_PATTERNS + [re.escape(w.strip()) for w in extra_words if w.strip()]
    return re.compile(rf"(?<!\w)(?:{'|'.join(alternatives)})(?!\w)", re.IGNORECASE)


def censor_text(text: str, rx: re.Pattern) -> str:
    """Keeps each explicit word's first letter: "fucking" -> "f******"."""
    return rx.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), text)


def missing_dependencies() -> list[str]:
    return [m for m in ("faster_whisper", "demucs", "numpy") if importlib.util.find_spec(m) is None]


def _ts(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:05.2f}"


# --- Reading lyrics and tags ---

def _ttml_seconds(value: str) -> float:
    """Parses TTML clock values like "2:34.682", "1:02:03.5" or "12.5s"."""
    seconds = 0.0
    for part in value.rstrip("s").split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def read_ttml_lines(path: Path) -> list[tuple[float, float, str]]:
    """Returns (begin, end, text) for each timed lyric line in a .ttml file."""
    lines = []
    for p in ElementTree.parse(path).getroot().iter():
        if p.tag.endswith("}p") and p.get("begin") and p.get("end"):
            text = "".join(p.itertext()).strip()
            lines.append((_ttml_seconds(p.get("begin")), _ttml_seconds(p.get("end")), text))
    return lines


@dataclass
class Probe:
    tags: dict[str, str]           # keys lower-cased
    has_cover: bool
    duration: float


def probe_file(path: Path) -> Probe:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags:stream=codec_type",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    info = json.loads(out)
    tags = {k.lower(): v for k, v in info["format"].get("tags", {}).items()}
    has_cover = any(s.get("codec_type") == "video" for s in info.get("streams", []))
    return Probe(tags, has_cover, float(info["format"]["duration"]))


@dataclass
class ExplicitScan:
    lines: list[tuple[float, float, int]] | None  # (begin, end, word count) per explicit .ttml line; None without a timed .ttml
    tag_words: int                                # explicit words in the embedded lyrics tag
    rated_explicit: bool                          # Apple's content rating says explicit

    @property
    def found(self) -> bool:
        return bool(self.lines) if self.lines is not None else self.tag_words > 0


def scan(path: Path, probe: Probe, rx: re.Pattern) -> ExplicitScan:
    ttml = path.with_suffix(".ttml")
    timed = read_ttml_lines(ttml) if ttml.exists() else []
    lines = [(b, e, len(rx.findall(text))) for b, e, text in timed if rx.search(text)] if timed else None
    tag_words = sum(len(rx.findall(v)) for k, v in probe.tags.items() if k.startswith("lyrics"))
    return ExplicitScan(lines, tag_words, probe.tags.get("rating") in ("1", "4"))


# --- Audio processing ---

def decode(path: Path, rate: int, channels: int):
    import numpy as np
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
         "-f", "f32le", "-ac", str(channels), "-ar", str(rate), "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, channels)


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


class Models:
    """Loads Whisper and Demucs on first use and reuses them across tracks."""

    def __init__(self):
        self._whisper = None
        self._demucs = None

    @property
    def whisper(self):
        if self._whisper is None:
            from faster_whisper import WhisperModel
            self._whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        return self._whisper

    @property
    def demucs(self):
        if self._demucs is None:
            from demucs.pretrained import get_model
            self._demucs = get_model("htdemucs")
            self._demucs.eval()
        return self._demucs


def locate_words(path: Path, found: ExplicitScan, duration: float, rx: re.Pattern,
                 models: Models) -> tuple[list[tuple[float, float]], list[str]]:
    """Returns the time windows to clear of vocals, plus notes on any fallbacks."""
    if found.lines is not None:
        regions = [(b - LINE_SLOP, e + LINE_SLOP) for b, e, _ in found.lines]
    else:
        regions = [(0.0, duration)]

    # Audio is passed to Whisper as an array: its own file decoding (PyAV)
    # breaks with newer PyAV releases.
    speech = decode(path, WHISPER_RATE, 1)[:, 0]
    heard = []
    for start, end in _merge([(max(0.0, s - CONTEXT), min(duration, e + CONTEXT)) for s, e in regions]):
        clip = speech[int(start * WHISPER_RATE):int(end * WHISPER_RATE)]
        segments, _ = models.whisper.transcribe(
            clip, language="en", word_timestamps=True, condition_on_previous_text=False,
        )
        for segment in segments:
            heard += [(start + w.start, start + w.end) for w in segment.words if rx.search(w.word)]

    if found.lines is None:
        if len(heard) < found.tag_words:
            raise CensorError(
                f"no timed lyrics, and Whisper located only {len(heard)} of {found.tag_words} "
                "explicit words; not writing a clean edit"
            )
        return _merge([(s - PAD_BEFORE, e + PAD_AFTER) for s, e in heard]), []

    windows, notes = [], []
    for begin, end, count in found.lines:
        in_line = [(s, e) for s, e in heard if begin - LINE_SLOP <= (s + e) / 2 <= end + LINE_SLOP]
        if len(in_line) >= count:
            windows += [(s - PAD_BEFORE, e + PAD_AFTER) for s, e in in_line]
        else:
            # Whisper missed it (common in loud mixes): clear the whole line instead.
            windows.append((begin - LINE_SLOP, end + LINE_SLOP))
            notes.append(f"couldn't pin down the word at {_ts(begin)}; removed vocals for the whole line")
    return _merge(windows), notes


def remove_vocals(audio, windows: list[tuple[float, float]], models: Models):
    """Subtracts the Demucs vocal stem inside each window; other samples are untouched."""
    import numpy as np
    import torch
    from demucs.apply import apply_model

    model = models.demucs
    vocals_index = model.sources.index("vocals")
    duration = len(audio) / SAMPLE_RATE
    out = audio.copy()
    for start, end in _merge([(max(0.0, s - CONTEXT), min(duration, e + CONTEXT)) for s, e in windows]):
        i0, i1 = int(start * SAMPLE_RATE), int(end * SAMPLE_RATE)
        segment = torch.from_numpy(audio[i0:i1].T.copy())
        ref = segment.mean(0)
        with torch.no_grad():
            sources = apply_model(
                model, ((segment - ref.mean()) / ref.std())[None],
                device="cpu", shifts=2, split=True, overlap=0.25, progress=False,
            )[0]
        vocals = (sources[vocals_index] * ref.std() + ref.mean()).numpy().T

        t = (i0 + np.arange(i1 - i0)) / SAMPLE_RATE
        gain = np.zeros(i1 - i0)
        for s, e in windows:
            gain = np.maximum(gain, np.clip(np.minimum((t - s + RAMP) / RAMP, (e + RAMP - t) / RAMP), 0, 1))
        out[i0:i1] -= (gain[:, None] * vocals).astype(np.float32)
    return out


def encode(audio, src: Path, dst: Path, probe: Probe, rx: re.Pattern, lossless: bool) -> None:
    """Writes `audio` to `dst`, carrying over cover art and tags from `src`."""
    import numpy as np

    codec = {
        ".mp3": ["-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3"],
        ".flac": ["-c:a", "flac"],
        ".m4a": ["-c:a", "alac"] if lossless else ["-c:a", "aac", "-b:a", "256k"],
    }.get(dst.suffix.lower())
    if codec is None:
        raise CensorError(f"unsupported output format '{dst.suffix}' (use .mp3, .m4a or .flac)")

    cover = ["-map", "1:v:0", "-c:v", "copy", "-disposition:v:0", "attached_pic"] if probe.has_cover else []
    tags = ["-metadata", "rating="]  # an empty value drops Apple's explicit rating
    if probe.tags.get("title"):
        tags += ["-metadata", f"title={probe.tags['title']}{CLEAN_SUFFIX}"]
    for key, value in probe.tags.items():
        if key.startswith("lyrics"):
            tags += ["-metadata", f"{key}={censor_text(value, rx)}"]

    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "2", "-i", "pipe:0", "-i", str(src),
         "-map", "0:a", *cover, "-map_metadata", "1", *codec, *tags, str(dst)],
        input=audio.astype(np.float32).tobytes(), check=True, capture_output=True,
    )


# --- Entry points ---

@dataclass
class CensorResult:
    status: str                    # "censored", "dry-run", "no-explicit" or "skipped"
    output: Path | None = None
    windows: list[tuple[float, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def clean_edit_path(src: Path) -> Path:
    return src.with_name(f"{src.stem}{CLEAN_SUFFIX}{src.suffix}")


def censor_file(src: Path, dst: Path | None = None, *, rx: re.Pattern, models: Models,
                replace: bool = False, lossless: bool = False, dry_run: bool = False) -> CensorResult:
    """
    Writes a clean edit of `src` (default: "<name> (Clean Edit).<ext>" next to
    it), plus a censored copy of its .ttml. With replace, the original and its
    .ttml are deleted afterwards.
    """
    if src.stem.endswith(CLEAN_SUFFIX.strip()):
        return CensorResult("skipped", notes=["already a clean edit"])
    dst = dst or clean_edit_path(src)
    if dst.resolve() == src.resolve():
        raise CensorError("output must differ from the input file")

    probe = probe_file(src)
    found = scan(src, probe, rx)
    if not found.found:
        notes = ["Apple rates it explicit, but its lyrics have no listed explicit words"] if found.rated_explicit else []
        return CensorResult("no-explicit", notes=notes)

    windows, notes = locate_words(src, found, probe.duration, rx, models)
    if dry_run:
        return CensorResult("dry-run", windows=windows, notes=notes)

    edited = remove_vocals(decode(src, SAMPLE_RATE, 2), windows, models)
    encode(edited, src, dst, probe, rx, lossless)

    ttml = src.with_suffix(".ttml")
    if ttml.exists():
        dst.with_suffix(".ttml").write_text(censor_text(ttml.read_text(encoding="utf-8"), rx), encoding="utf-8")
    if replace:
        src.unlink()
        ttml.unlink(missing_ok=True)
    return CensorResult("censored", dst, windows, notes)


def describe_windows(windows: list[tuple[float, float]]) -> str:
    return ", ".join(f"{_ts(s)}-{_ts(e)}" for s, e in windows)


def main():
    parser = argparse.ArgumentParser(
        description="Make clean edits of tracks by removing the vocals under each explicit word.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("files", nargs="+", type=Path,
                        help="Audio files (.m4a, .mp3, .flac). A .ttml lyrics file with the same name is used when present.")
    parser.add_argument("-o", "--output", type=Path,
                        help="Output file, for a single input. Default: '<name> (Clean Edit).<ext>' next to the input.")
    parser.add_argument("--replace", action="store_true",
                        help="Delete the original (and its .ttml) after writing the clean edit.")
    parser.add_argument("--extra-words", default="",
                        help="Comma-separated extra words to censor, e.g. 'damn,hell,ass'.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only report where vocals would be removed; write nothing.")
    args = parser.parse_args()

    if args.output and len(args.files) > 1:
        parser.error("--output only works with a single input file.")
    if missing := missing_dependencies():
        parser.error(f"missing {', '.join(missing)}; install with: uv sync --extra censor")

    rx = explicit_regex(args.extra_words.split(","))
    models = Models()
    failures = 0
    for src in args.files:
        try:
            result = censor_file(src, args.output, rx=rx, models=models,
                                 replace=args.replace, dry_run=args.dry_run)
        except (CensorError, subprocess.CalledProcessError, OSError, ElementTree.ParseError) as e:
            failures += 1
            print(f"[FAIL] '{src}': {e}")
            continue
        if result.status == "censored":
            print(f"[CENSORED] '{src}' -> '{result.output}'\n    vocals removed at {describe_windows(result.windows)}")
        elif result.status == "dry-run":
            print(f"[DRY RUN] '{src}': would remove vocals at {describe_windows(result.windows)}")
        elif result.status == "no-explicit":
            print(f"[CLEAN] '{src}': no explicit lyrics found.")
        else:
            print(f"[SKIPPED] '{src}': {'; '.join(result.notes)}")
            continue
        for note in result.notes:
            print(f"    note: {note}")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
