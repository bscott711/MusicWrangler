# **MusicWrangler**

A collection of Python scripts to automate the process of finding, downloading, converting, and organizing a digital music library. This workflow is designed to take a simple list of songs and turn it into a neatly organized, flat-directory music collection suitable for any MP3 player, car stereo, or local media library.

## **Features**

* **Automated Downloading:** Takes a simple Artist \- Title text file and uses gamdl to find and download the corresponding tracks from Apple Music.  
* **Flexible Audio Conversion:** Uses ffmpeg to convert the downloaded M4A files into MP3 (high-quality VBR), FLAC (lossless), or ALAC (Apple Lossless).  
* **Parallel Processing:** Maximizes speed by running both downloads and conversions in parallel, utilizing multiple CPU cores and network connections.  
* **Directory Flattening:** Includes a utility to reorganize the nested Artist/Album/Song folder structure into a single, flat directory.  
* **Intelligent Renaming:** Automatically renames files during the flattening process to Song Title \- Artist \- Album.ext to prevent conflicts and keep metadata visible.  
* **Smart & Safe:** Skips files that already exist, can automatically clean up source files after conversion, and allows for "convert-only" runs on already-downloaded content.
* **Clean Edits:** Prefers Apple's clean versions, and when a song has none, makes its own clean edit by removing just the singer's voice under each explicit word while the music keeps playing.

## **Workflow Overview**

This project consists of three scripts that work together:

1. process\_music.py: The main script that handles finding, downloading, and converting the music.  
2. flatten\_directory.py: A utility script to reorganize the output for simple MP3 players.  
3. censor\_track.py: Makes clean edits of tracks with explicit lyrics (also built into process\_music.py via `--censor`).

\[song\_list.txt\] \--\> \[process\_music.py\] \--\> \[Nested Music Folder\] \--\> \[flatten\_directory.py\] \--\> \[Flat Music Folder\]

## **Prerequisites**

Before using these scripts, ensure you have the following installed.

### **Command-Line Tools**

* [**FFmpeg**](https://ffmpeg.org/)**:** The core engine for audio conversion.  
  * On macOS, install via Homebrew: brew install ffmpeg  
* [**gamdl**](https://github.com/glomatico/gamdl)**:** The tool for downloading from Apple Music (version 3.8.5 or newer; 2.x breaks when Apple changes its web player). Installed automatically by `uv sync`.  
  * Requires Python 3.10+ and an active Apple Music subscription.  
  * Export your Apple Music cookies in Netscape format while logged in at music.apple.com and save them as `cookies.txt` in the directory you run `process-music` from. This file contains your login session, so keep it out of version control (it is listed in `.gitignore`).  
  * If downloads fail with "Error fetching wrapper account info", gamdl's generated `~/.gamdl/config.ini` has the wrapper enabled; pass `--ignore-gamdl-config` to `process-music` to bypass it.

### **Python Libraries**

* **requests:** Used for making API calls to the iTunes Search API.  
  * Install via pip: pip install requests

## **Usage**

### **1\. The Main Script: process\_music.py**

This is your primary tool for getting and converting music.  
First, create a song\_list.txt file with one song per line, formatted as Artist \- Title.  
**Example song\_list.txt:**  
\# My Playlist  
Queen \- Bohemian Rhapsody  
The Beatles \- Let It Be  
Lizzo \- Truth Hurts

#### **Commands:**

Example 1: Basic Download & Convert to MP3  
Downloads songs from the list and converts them to MP3 in a folder named MyMusic. Uses default parallel settings.  
./process\_music.py \--list-file song\_list.txt \-o ./MyMusic \-f mp3

Example 2: Convert to Lossless (FLAC) with Cleanup  
Downloads, converts to FLAC, and deletes the original M4A files after a successful conversion.  
./process\_music.py \--list-file song\_list.txt \-o ./LosslessMusic \-f flac \--cleanup

Example 3: Convert-Only Mode  
Skips the download phase and just converts existing M4A files in a directory.  
./process\_music.py \--convert-only \-o ./MyMusic \-f mp3

Example 4: Aggressive Parallel Processing  
Uses 10 workers for downloading and 8 workers for converting.  
./process\_music.py \--list-file song\_list.txt \-o ./MyMusic \-f mp3 \--download-workers 10 \--convert-workers 8

Example 5: Clean Versions Only  
Skips anything Apple flags as explicit and uses the clean (or unflagged) edition instead. Songs with no clean edition are reported as NOT\_FOUND rather than downloaded.  
./process\_music.py \--list-file song\_list.txt \-o ./MyMusic \-f mp3 \--clean

Example 6: Clean Versions, Censoring Any Song Without One  
Like Example 5, but songs with no clean edition are downloaded explicit and then given a clean edit (see [Clean Edits](#clean-edits) below). Clean edits are saved as `<name> (Clean Edit).mp3`.  
uv run process-music \--list-file song\_list.txt \-o ./MyMusic \-f mp3 \--cleanup \--clean \--censor

### **2\. The Utility Script: flatten\_directory.py**

Use this script *after* process\_music.py to reorganize your library into a single folder.

#### **Commands:**

Example 1: Copy MP3s to a Flat Directory  
Recursively finds all .mp3 files in MyMusic and copies them with new names into FlatMusic.  
./flatten\_directory.py ./MyMusic ./FlatMusic \--formats mp3

Example 2: Move Multiple Formats  
Moves all .mp3 and .flac files from MyMusic into FlatMusic. This is a destructive action on the source directory.  
./flatten\_directory.py ./MyMusic ./FlatMusic \--action move \--formats mp3 flac

### **3\. Clean Edits: censor\_track.py**

Makes a clean edit of a track by removing only the singer's voice under each explicit word, so the band keeps playing instead of the whole song dropping out. Every other moment of the song is left exactly as it was.

#### **Setup**

This needs Whisper and Demucs, which pull in PyTorch (a large download), so they're an optional extra. Install them once:  
uv sync \--extra censor

A plain `uv run` keeps them installed, but a bare `uv sync` removes them; use `uv sync --extra censor` instead. The first run also downloads the Whisper (small.en) and Demucs (htdemucs) models into your cache.

#### **Commands:**

Example 1: Make a Clean Edit  
Writes `Song (Clean Edit).mp3` (and a censored `Song (Clean Edit).ttml`) next to the original, which is kept.  
uv run censor-track "Song.mp3"

Example 2: Preview Without Writing Anything  
Reports where vocals would be removed, e.g. `would remove vocals at 2:35.22-2:35.81`.  
uv run censor-track \--dry-run "Song.m4a"

Example 3: Also Censor Milder Words, Replacing the Original  
uv run censor-track \--extra-words damn,hell,ass \--replace "Song.m4a"

#### **How it works**

1. **Find the words.** Explicit words are looked up in the track's synced lyrics (the `.ttml` file saved next to each download), or in its embedded lyrics tag if there's no `.ttml`.
2. **Pin them down.** Apple's lyrics are usually timed per line, so Whisper's word timestamps locate each word within its line. If Whisper can't hear a word clearly, the vocals for that whole line are removed instead, and a note is printed.
3. **Remove just the voice.** Demucs separates the vocals around each word, and only the vocal part is subtracted, only inside the word (with short fades so there are no clicks).
4. **Clean up the tags.** Cover art and tags are carried over, the title gets "(Clean Edit)", the embedded lyrics and `.ttml` are censored (`f******`), and Apple's explicit rating is removed.

With `process-music --censor`, censoring runs between downloading and converting. If a track can't be censored, it's left unconverted, so an explicit file never reaches your library by accident.

#### **Limitations**

* It works from the lyrics, so explicit words missing from Apple's lyrics (like ad-libs) aren't caught.
* On live recordings, crowd voices under the word are removed along with the singer's.
* Separation isn't perfect; give each clean edit a quick listen at the times it prints.

## **License**

This project is released under the MIT License.
