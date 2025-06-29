#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Flattens a nested music directory into a single directory, creating
descriptive filenames to avoid conflicts.
"""

import argparse
import shutil
from pathlib import Path


def flatten_directory(
    source_dir: Path,
    dest_dir: Path,
    action: str,
    formats: list[str]
) -> None:
    """
    Scans, renames, and moves/copies files from a source to a destination.

    Args:
        source_dir: The nested directory to scan for music files.
        dest_dir: The flat directory to place music files into.
        action: Either 'copy' or 'move' the files.
        formats: A list of file extensions to process (e.g., ['mp3', 'flac']).
    """
    if not source_dir.is_dir():
        print(f"Error: Source directory '{source_dir}' does not exist.")
        return

    # Create the destination directory if it doesn't exist
    dest_dir.mkdir(parents=True, exist_ok=True)
    print(f"Source: '{source_dir.resolve()}'")
    print(f"Destination: '{dest_dir.resolve()}'")
    print(f"Action: {action.upper()}")
    print("-" * 50)

    # Gather all files of the specified formats
    all_files = []
    for fmt in formats:
        # Sanitize format in case user includes a dot (e.g., .mp3)
        clean_fmt = fmt.strip().lstrip('.')
        found = list(source_dir.glob(f"**/*.{clean_fmt}"))
        all_files.extend(found)
        print(f"Found {len(found)} '.{clean_fmt}' files.")

    if not all_files:
        print("No matching music files found to process.")
        return

    # Process each file
    processed_count = 0
    skipped_count = 0
    for source_path in all_files:
        # This assumes a structure of .../Artist/Album/Song.ext
        # We extract the parent (album) and grandparent (artist) folders
        try:
            album = source_path.parent.name
            artist = source_path.parent.parent.name

            # Avoid using the top-level source directory name in the new filename
            if source_path.parent.parent == source_dir:
                 new_name = f"{album} - {source_path.name}"
            elif source_path.parent == source_dir:
                new_name = source_path.name
            else:
                 new_name = f"{artist} - {album} - {source_path.name}"
        except IndexError:
            # Fallback for files at the top level of the source directory
            new_name = source_path.name

        dest_path = dest_dir / new_name

        if dest_path.exists():
            print(f"[SKIP] '{dest_path.name}' already exists in destination.")
            skipped_count += 1
            continue

        try:
            if action == "move":
                shutil.move(source_path, dest_path)
            else:  # 'copy' is the default
                shutil.copy2(source_path, dest_path) # copy2 preserves metadata

            print(f"[{action.upper()}] '{source_path.relative_to(source_dir)}' -> '{dest_path.name}'")
            processed_count += 1
        except Exception as e:
            print(f"[ERROR] Could not process '{source_path.name}': {e}")
            skipped_count += 1

    print("-" * 50)
    print("Flattening process complete.")
    print(f"  - Files {action}ed: {processed_count}")
    print(f"  - Files skipped:  {skipped_count}")


def main():
    """Parses command-line arguments and starts the process."""
    parser = argparse.ArgumentParser(
        description="Flatten a nested directory of music files into a single folder.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("source_dir", help="The source directory with nested music files.")
    parser.add_argument("dest_dir", help="The destination directory for the flat file structure.")
    parser.add_argument(
        "--action", default="copy", choices=["copy", "move"],
        help="Choose to 'copy' or 'move' files. 'copy' is safer and the default."
    )
    parser.add_argument(
        "--formats", nargs='+', default=["mp3"],
        help="A space-separated list of file extensions to flatten (e.g., mp3 flac)."
    )
    args = parser.parse_args()

    flatten_directory(
        Path(args.source_dir),
        Path(args.dest_dir),
        args.action,
        args.formats
    )


if __name__ == "__main__":
    main()