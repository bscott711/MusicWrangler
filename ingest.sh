#!/bin/bash
# Manual, on-demand ingestion: reads song_list.txt, downloads + converts via
# gamdl/ffmpeg, flattens the result straight into Downbeat's library, then
# triggers a library rescan so the new tracks show up immediately.
#
# One-time prerequisite: gamdl needs its own cookies.txt (place at
# ./cookies.txt, its default) and a --wvd-path — run `uv run gamdl --help`
# and see gamdl's own first-run prompts if these aren't configured yet.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$(pwd)/bin:/home/opc/.nvm/versions/node/v24.19.0/bin:$PATH"

DOWNBEAT_DIR="/home/opc/downbeat"
STAGING_DIR="./staging"
FORMAT="mp3"

echo "=== 1/3: downloading + converting ==="
~/.local/bin/uv run process-music --list-file song_list.txt -o "$STAGING_DIR" -f "$FORMAT"

echo "=== 2/3: flattening into Downbeat's library ==="
~/.local/bin/uv run flatten-directory "$STAGING_DIR" "$DOWNBEAT_DIR/library" --formats "$FORMAT" --action move

echo "=== 3/3: triggering Downbeat rescan ==="
cd "$DOWNBEAT_DIR"
node --env-file-if-exists=.env -e "
import('./server/services/scanner.service.js').then(async (m) => {
  console.log('rescan:', JSON.stringify(await m.scanLibrary()));
});
"

echo "Done."
