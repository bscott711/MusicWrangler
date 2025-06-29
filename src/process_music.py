#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A master script to find, download, and/or convert music in parallel.

This script can use multiple processes to speed up both the download
and conversion phases.
"""

import argparse
import os
import subprocess
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import requests


# --- Worker Functions (for parallel execution) ---

def download_one_song(line: str, output_dir: Path) -> tuple[str, str | None]:
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

    # Search for the song URL
    try:
        response = requests.get(
            "https://itunes.apple.com/search",
            params={"term": search_term, "entity": "song", "media": "music", "limit": 1},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("resultCount", 0) > 0:
            url = data["results"][0].get("trackViewUrl")
        else:
            return "not_found", f"'{search_term}' not found on Apple Music."
    except requests.exceptions.RequestException as e:
        return "fail", f"API request failed for '{search_term}': {e}"

    if not url:
        return "not_found", f"URL not found in API response for '{search_term}'."

    # Download the song
    try:
        subprocess.run(
            ["gamdl", "--output-path", str(output_dir), url],
            check=True, capture_output=True, text=True
        )
        return "success", f"Successfully downloaded '{search_term}'."
    except subprocess.CalledProcessError as e:
        return "fail", f"gamdl failed for '{search_term}'. Error: {e.stderr}"
    except FileNotFoundError:
        return "fail", "gamdl command not found. Please ensure it is installed."


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

def download_phase(file_path: Path, output_dir: Path, num_workers: int):
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
        future_to_line = {executor.submit(download_one_song, line, output_dir): line for line in lines}
        for future in as_completed(future_to_line):
            status, message = future.result()
            print(f"[{status.upper()}] {message}")


def conversion_phase(directory: Path, audio_format: str, cleanup: bool, num_workers: int):
    """Phase 2: Converts M4A files in parallel."""
    print("\n" + "=" * 50)
    print(f"PHASE 2: CONVERTING TO {audio_format.upper()} (using up to {num_workers} workers)")
    print("=" * 50)

    if audio_format == "m4a":
        print("Target format is M4A, no conversion necessary.")
        return

    m4a_files = list(directory.glob("**/*.m4a"))
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
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not args.convert_only:
        if not args.list_file:
            parser.error("--list-file is required unless --convert-only is used.")
        download_phase(Path(args.list_file), output_dir, args.download_workers)

    conversion_phase(output_dir, args.format, args.cleanup, args.convert_workers)

    print("\n" + "=" * 50)
    print("All processes complete.")
    print("=" * 50)


if __name__ == "__main__":
    main()
