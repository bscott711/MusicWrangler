# **MusicWrangler**

A collection of Python scripts to automate the process of finding, downloading, converting, and organizing a digital music library. This workflow is designed to take a simple list of songs and turn it into a neatly organized, flat-directory music collection suitable for any MP3 player, car stereo, or local media library.

## **Features**

* **Automated Downloading:** Takes a simple Artist \- Title text file and uses gamdl to find and download the corresponding tracks from Apple Music.  
* **Flexible Audio Conversion:** Uses ffmpeg to convert the downloaded M4A files into MP3 (high-quality VBR), FLAC (lossless), or ALAC (Apple Lossless).  
* **Parallel Processing:** Maximizes speed by running both downloads and conversions in parallel, utilizing multiple CPU cores and network connections.  
* **Directory Flattening:** Includes a utility to reorganize the nested Artist/Album/Song folder structure into a single, flat directory.  
* **Intelligent Renaming:** Automatically renames files during the flattening process to Song Title \- Artist \- Album.ext to prevent conflicts and keep metadata visible.  
* **Smart & Safe:** Skips files that already exist, can automatically clean up source files after conversion, and allows for "convert-only" runs on already-downloaded content.

## **Workflow Overview**

This project consists of two main scripts that work together:

1. process\_music.py: The main script that handles finding, downloading, and converting the music.  
2. flatten\_directory.py: A utility script to reorganize the output for simple MP3 players.

\[song\_list.txt\] \--\> \[process\_music.py\] \--\> \[Nested Music Folder\] \--\> \[flatten\_directory.py\] \--\> \[Flat Music Folder\]

## **Prerequisites**

Before using these scripts, ensure you have the following installed.

### **Command-Line Tools**

* [**FFmpeg**](https://ffmpeg.org/)**:** The core engine for audio conversion.  
  * On macOS, install via Homebrew: brew install ffmpeg  
* [**gamdl**](https://github.com/glomatico/gamdl)**:** The tool for downloading from Apple Music.  
  * Follow its installation instructions. Typically: pip install gamdl

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

### **2\. The Utility Script: flatten\_directory.py**

Use this script *after* process\_music.py to reorganize your library into a single folder.

#### **Commands:**

Example 1: Copy MP3s to a Flat Directory  
Recursively finds all .mp3 files in MyMusic and copies them with new names into FlatMusic.  
./flatten\_directory.py ./MyMusic ./FlatMusic \--formats mp3

Example 2: Move Multiple Formats  
Moves all .mp3 and .flac files from MyMusic into FlatMusic. This is a destructive action on the source directory.  
./flatten\_directory.py ./MyMusic ./FlatMusic \--action move \--formats mp3 flac

## **License**

This project is released under the MIT License.
