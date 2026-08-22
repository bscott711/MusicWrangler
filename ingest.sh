#!/bin/bash
# Manual, on-demand ingestion: pulls queued Artist/Title requests straight
# from Downbeat's SQLite DB (added via Tier 2's "Add to queue" button),
# downloads + converts via gamdl/ffmpeg, flattens into Downbeat's library,
# rescans, auto-splits any track that doesn't have a stem yet, enforces the
# storage cap, then clears the queue rows it just processed.
#
# Downbeat's own backend never touches gamdl/process-music — this script is
# the only thing that does, and it's the one reaching into Downbeat's DB,
# not the other way around.
#
# One-time prerequisite: gamdl needs its own cookies.txt (place at
# ./cookies.txt, its default) and a --wvd-path — run `uv run gamdl --help`
# and see gamdl's own first-run prompts if these aren't configured yet.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$(pwd)/bin:/home/opc/.nvm/versions/node/v24.19.0/bin:$PATH"

DOWNBEAT_DIR="/home/opc/downbeat"
STAGING_DIR="$(pwd)/staging"
LIST_FILE="$(pwd)/staging-list.txt"
FORMAT="mp3"

echo "=== 1/5: reading the queue from Downbeat ==="
(cd "$DOWNBEAT_DIR" && node --env-file-if-exists=.env -e "
import('./server/db/connection.js').then(({ db }) => {
  const rows = db.prepare('SELECT track_name, artist_name FROM pending_ingest ORDER BY id').all();
  for (const row of rows) console.log(\`\${row.artist_name} - \${row.track_name}\`);
});
") > "$LIST_FILE"

if [ ! -s "$LIST_FILE" ]; then
  echo "Queue is empty — nothing to do."
  rm -f "$LIST_FILE"
  exit 0
fi

echo "Queued:"
cat "$LIST_FILE"

echo "=== 2/5: downloading + converting ==="
~/.local/bin/uv run process-music --list-file "$LIST_FILE" -o "$STAGING_DIR" -f "$FORMAT"

echo "=== 3/5: flattening into Downbeat's library ==="
~/.local/bin/uv run flatten-directory "$STAGING_DIR" "$DOWNBEAT_DIR/library" --formats "$FORMAT" --action move

echo "=== 4/5: rescanning, auto-splitting new tracks, enforcing storage cap ==="
(cd "$DOWNBEAT_DIR" && node --env-file-if-exists=.env -e "
import('./server/services/scanner.service.js').then(async ({ scanLibrary }) => {
  console.log('rescan:', JSON.stringify(await scanLibrary()));

  const { requestSeparation } = await import('./server/services/separation.service.js');
  const { enforceLibraryStorageCap } = await import('./server/services/library.service.js');
  const { db } = await import('./server/db/connection.js');

  const unsplit = db.prepare('SELECT id FROM tracks WHERE id NOT IN (SELECT track_id FROM track_stems)').all();
  for (const row of unsplit) requestSeparation(row.id);
  console.log('auto-split requested for', unsplit.length, 'track(s)');

  console.log('eviction:', JSON.stringify(enforceLibraryStorageCap()));
});
")

echo "=== 5/5: clearing the processed queue ==="
(cd "$DOWNBEAT_DIR" && node --env-file-if-exists=.env -e "
import('./server/db/connection.js').then(({ db }) => {
  db.exec('DELETE FROM pending_ingest');
  console.log('queue cleared');
});
")

rm -f "$LIST_FILE"
echo "Done."
