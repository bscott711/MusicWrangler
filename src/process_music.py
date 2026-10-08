#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A master script to find, download, and/or convert music in parallel.

This script can use multiple processes to speed up both the download
and conversion phases.
"""

import argparse
import os
import re
import subprocess
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import requests

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _tail(*streams: str | None, limit: int = 300) -> str:
    """Joins process output, strips color codes, and keeps the last `limit` chars."""
    text = ANSI_ESCAPE.sub("", "\n".join(s or "" for s in streams)).strip()
    return text[-limit:] or "no output"


# --- Worker Functions (for parallel execution) ---

def _title_match_score(track_name: str, query: str, whole_words: bool = False) -> int:
    """
    Scores how well a track title matches the query (3 exact, 2 prefix,
    1 substring, 0 none). With whole_words, prefix/substring matches must end
    on a word boundary, so "Change" does not match "Changes".
    """
    a = (track_name or "").strip().lower()
    b = (query or "").strip().lower()
    if not a or not b:
        return 0
    if a == b:
        return 3
    if whole_words:
        short, long_ = sorted((a, b), key=len)
        if long_.startswith(short) and not long_[len(short)].isalnum():
            return 2
        if short is b and re.search(rf"(?<!\w){re.escape(b)}(?!\w)", a):
            return 1
        return 0
    if a.startswith(b) or b.startswith(a):
        return 2
    if b in a:
        return 1
    return 0


def find_track_url(
    artist: str, title: str, clean: bool = False, explicit_fallback: bool = False
) -> str | None:
    """
    Resolves an Artist/Title pair to an Apple Music track URL by searching
    for the artist first, then matching the title within *that artist's*
    own catalog. A plain combined "Artist - Title" search silently returns
    the wrong recording whenever the artist name collides with a common
    word or another artist of the same name — confirmed for real: "Lit -
    My Own Worst Enemy" resolved to Casting Crowns' song of the same title,
    because "Lit" collides with 9+ other artists plus the slang word.
    Mirrors downbeat's apple.service.js searchAppleByArtistAndTrack, which
    fixed the identical problem for Tier 2's own search box.

    With clean=True, tracks Apple flags as explicit are skipped, so a
    "cleaned" or unflagged edition is used instead (or nothing, if none
    exists). The artist lookup is capped at 200 tracks and can miss cleaned
    editions of big catalogs, so a song search is merged in for that case,
    restricted to tracks whose credited artists include `artist` as a word.
    With explicit_fallback too, an explicit version is used when no clean one
    matches, for --censor to clean up after download.
    """
    try:
        artist_resp = requests.get(
            "https://itunes.apple.com/search",
            params={"term": artist, "entity": "musicArtist", "attribute": "artistTerm", "limit": 10},
            timeout=15,
        )
        artist_resp.raise_for_status()
        artist_results = artist_resp.json().get("results", [])
    except requests.exceptions.RequestException:
        return None

    normalized_artist = artist.strip().lower()
    exact_matches = [
        a for a in artist_results
        if (a.get("artistName") or "").strip().lower() == normalized_artist
    ]
    candidate_artists = (exact_matches or artist_results)[:3]

    all_tracks = []
    for candidate in candidate_artists:
        artist_id = candidate.get("artistId")
        if not artist_id:
            continue
        try:
            lookup_resp = requests.get(
                "https://itunes.apple.com/lookup",
                params={"id": artist_id, "entity": "song", "limit": 200},
                timeout=15,
            )
            lookup_resp.raise_for_status()
            all_tracks.extend(
                r for r in lookup_resp.json().get("results", []) if r.get("wrapperType") == "track"
            )
        except requests.exceptions.RequestException:
            continue

    if clean:
        credited = re.compile(rf"(?<!\w){re.escape(normalized_artist)}(?!\w)")
        try:
            search_resp = requests.get(
                "https://itunes.apple.com/search",
                params={"term": f"{artist} {title}", "entity": "song", "media": "music", "limit": 50},
                timeout=15,
            )
            search_resp.raise_for_status()
            all_tracks.extend(
                r for r in search_resp.json().get("results", [])
                if credited.search((r.get("artistName") or "").lower())
            )
        except requests.exceptions.RequestException:
            pass
        clean_tracks = [t for t in all_tracks if t.get("trackExplicitness") != "explicit"]
        candidate_sets = [clean_tracks, all_tracks] if explicit_fallback else [clean_tracks]
    else:
        candidate_sets = [all_tracks]

    for tracks in candidate_sets:
        scored = [(_title_match_score(t.get("trackName", ""), title, whole_words=clean), t) for t in tracks]
        scored = [s for s in scored if s[0] > 0]
        if scored:
            scored.sort(key=lambda s: s[0], reverse=True)
            return scored[0][1].get("trackViewUrl")
    return None


def download_one_song(
    line: str, output_dir: Path, ignore_gamdl_config: bool = False, clean: bool = False,
    explicit_fallback: bool = False,
) -> tuple[str, str | None]:
    """
    Worker task that processes a single line from the song list.
    Searches for the URL and calls gamdl.
    Returns a status and a message.
    """
    if " - " not in line:
        return "skipped", f"Invalid format: {line}"

    artist, title = line.split(" - ", 1)
    artist, title = artist.strip(), title.strip()
    search_term = f"{artist} - {title}"

    url = find_track_url(artist, title, clean, explicit_fallback)
    if not url:
        kind = "clean version" if clean else "match"
        return "not_found", f"No {kind} of '{search_term}' found on Apple Music."

    # Download the song. ignore_gamdl_config passes --no-config-file, for
    # machines where ~/.gamdl/config.ini was generated with the wrapper enabled.
    try:
        cmd = ["gamdl"]
        if ignore_gamdl_config:
            cmd.append("--no-config-file")
        cmd += [
            "--output-path", str(output_dir),
            "--synced-lyrics-format", "ttml", url,
        ]
        result = subprocess.run(
            cmd, check=True, capture_output=True, text=True,
            stdin=subprocess.DEVNULL,  # gamdl prompts (e.g. missing cookies.txt)
        )
    except subprocess.CalledProcessError as e:
        return "fail", f"gamdl failed for '{search_term}'. Error: {_tail(e.stdout, e.stderr)}"
    except FileNotFoundError:
        return "fail", "gamdl command not found. Please ensure it is installed."

    # gamdl exits 0 even when a download fails, so check its own summary line.
    output = ANSI_ESCAPE.sub("", result.stdout + result.stderr)
    summary = re.search(r"Finished with (\d+) error\(s\)", output)
    if not summary or int(summary.group(1)) > 0:
        return "fail", f"gamdl failed for '{search_term}'. Error: {_tail(output)}"
    return "success", f"Successfully downloaded '{search_term}'."


def convert_one_file(m4a_file: Path, base_dir: Path, audio_format: str, cleanup: bool) -> tuple[str, str]:
    """
    Worker task that converts a single M4A file to the target format.
    Returns a status and a message.
    """
    codec_map = {
        "mp3": ("libmp3lame", ".mp3", ["-q:a", "2"]),
        "flac": ("flac", ".flac", []),
        "alac": ("alac", ".m4a", []),
    }
    codec, extension, quality_flags = codec_map[audio_format]

    output_file = m4a_file.with_suffix(extension)
    if audio_format == "alac" and m4a_file == output_file:
        output_file = m4a_file.with_name(f"{m4a_file.stem} (ALAC).m4a")

    if output_file.exists():
        return "skipped", f"'{m4a_file.relative_to(base_dir)}' already converted."

    command = [
        "ffmpeg", "-i", str(m4a_file), "-c:v", "copy",
        "-c:a", codec, *quality_flags, "-hide_banner", "-loglevel", "error",
        str(output_file),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        if cleanup:
            m4a_file.unlink()
            return "success", f"Converted '{m4a_file.relative_to(base_dir)}' and removed original."
        else:
            return "success", f"Converted '{m4a_file.relative_to(base_dir)}'."
    except subprocess.CalledProcessError as e:
        return "fail", f"ffmpeg failed for '{m4a_file.relative_to(base_dir)}'. Error: {e.stderr}"
    except FileNotFoundError:
        return "fail", "ffmpeg command not found. Please ensure it is installed."


# --- Main Orchestration Functions ---

def download_phase(
    file_path: Path, output_dir: Path, num_workers: int,
    ignore_gamdl_config: bool = False, clean: bool = False, explicit_fallback: bool = False,
):
    """Phase 1: Downloads songs in parallel."""
    print("=" * 50)
    print(f"PHASE 1: DOWNLOADING SONGS (using up to {num_workers} workers)")
    print("=" * 50)
    try:
        with file_path.open("r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    except FileNotFoundError:
        print(f"Error: The file '{file_path}' was not found.")
        return

    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        # Submit all tasks to the executor
        future_to_line = {executor.submit(download_one_song, line, output_dir, ignore_gamdl_config, clean, explicit_fallback): line for line in lines}
        for future in as_completed(future_to_line):
            status, message = future.result()
            print(f"[{status.upper()}] {message}")


def censor_phase(directory: Path, converting: bool) -> set[Path]:
    """
    Makes clean edits of downloaded tracks with explicit lyrics (see
    censor_track), one at a time since the models are large. Returns the files
    that couldn't be censored, so they're kept out of conversion instead of
    reaching the library explicit.
    """
    from censor_track import CLEAN_SUFFIX, Models, censor_file, describe_windows, explicit_regex

    print("\n" + "=" * 50)
    print("CENSORING EXPLICIT LYRICS")
    print("=" * 50)

    m4a_files = sorted(f for f in directory.glob("**/*.m4a") if not f.stem.endswith(CLEAN_SUFFIX.strip()))
    rx, models, failed, censored = explicit_regex(), Models(), set(), 0
    for m4a_file in m4a_files:
        name = m4a_file.relative_to(directory)
        try:
            # Lossless intermediate when a conversion follows, to avoid a second lossy generation.
            result = censor_file(m4a_file, rx=rx, models=models, replace=True, lossless=converting)
        except Exception as e:
            failed.add(m4a_file)
            outcome = "leaving it unconverted" if converting else "the explicit original is still in place"
            print(f"[FAIL] Couldn't censor '{name}'; {outcome}. Error: {e}")
            continue
        if result.status == "censored":
            censored += 1
            print(f"[CENSORED] '{name}' -> '{result.output.name}' (vocals removed at {describe_windows(result.windows)})")
        for note in result.notes:
            print(f"[NOTE] '{name}': {note}")
    print(f"Checked {len(m4a_files)} file(s); censored {censored}.")
    return failed


def conversion_phase(directory: Path, audio_format: str, cleanup: bool, num_workers: int,
                     skip: set[Path] = frozenset()):
    """Phase 2: Converts M4A files in parallel."""
    print("\n" + "=" * 50)
    print(f"PHASE 2: CONVERTING TO {audio_format.upper()} (using up to {num_workers} workers)")
    print("=" * 50)

    if audio_format == "m4a":
        print("Target format is M4A, no conversion necessary.")
        return

    m4a_files = [f for f in directory.glob("**/*.m4a") if f not in skip]
    if skip:
        print(f"Not converting {len(skip)} file(s) that couldn't be censored.")
    if not m4a_files:
        print(f"No .m4a files found in '{directory.resolve()}' to convert.")
        return

    print(f"Found {len(m4a_files)} .m4a file(s) for conversion.")
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        future_to_file = {executor.submit(convert_one_file, m4a_file, directory, audio_format, cleanup): m4a_file for m4a_file in m4a_files}
        for future in as_completed(future_to_file):
            status, message = future.result()
            print(f"[{status.upper()}] {message}")


def main():
    """Parses arguments and orchestrates the process."""
    # Default worker counts
    cpu_count = os.cpu_count() or 1
    default_dl_workers = 4

    parser = argparse.ArgumentParser(
        description="A master script to find, download, and/or convert music in parallel.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("-l", "--list-file", help="Path to the text file with 'Artist - Title' per line.")
    parser.add_argument("-o", "--output-dir", default=".", help="Directory to download and/or convert music in.")
    parser.add_argument("-f", "--format", default="mp3", choices=["mp3", "flac", "alac", "m4a"], help="Target audio format. 'm4a' skips conversion.")
    parser.add_argument("--cleanup", action="store_true", help="Delete original M4A files after successful conversion.")
    parser.add_argument("--convert-only", action="store_true", help="Skip the download phase and only convert existing files.")
    parser.add_argument("--download-workers", type=int, default=default_dl_workers, help="Number of parallel download processes.")
    parser.add_argument("--convert-workers", type=int, default=cpu_count, help="Number of parallel conversion processes.")
    parser.add_argument("--clean", action="store_true", help="Only download non-explicit (clean/cleaned) versions; songs with none are reported as not found.")
    parser.add_argument("--censor", action="store_true", help="Make clean edits of tracks with explicit lyrics by removing the vocals under each explicit word (needs: uv sync --extra censor). With --clean, falls back to the explicit version when no clean one exists.")
    parser.add_argument("--ignore-gamdl-config", action="store_true", help="Run gamdl with --no-config-file, ignoring ~/.gamdl/config.ini (use if it was generated with the wrapper enabled).")
    args = parser.parse_args()

    if args.censor:
        from censor_track import missing_dependencies
        if missing := missing_dependencies():
            parser.error(f"--censor needs {', '.join(missing)}; install with: uv sync --extra censor")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not args.convert_only:
        if not args.list_file:
            parser.error("--list-file is required unless --convert-only is used.")
        download_phase(Path(args.list_file), output_dir, args.download_workers, args.ignore_gamdl_config, args.clean, args.censor)

    failed_censor = censor_phase(output_dir, converting=args.format != "m4a") if args.censor else set()
    conversion_phase(output_dir, args.format, args.cleanup, args.convert_workers, failed_censor)

    print("\n" + "=" * 50)
    print("All processes complete.")
    print("=" * 50)


if __name__ == "__main__":
    main()
