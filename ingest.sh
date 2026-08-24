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
# migrate() first: this script imports Downbeat's own source straight off
# disk and runs standalone, so — unlike downbeat.service, which migrates at
# boot — it picks up a schema change the moment it's pulled, even if the
# service itself hasn't restarted (deploy is git-push-triggered) yet. A
# step further down querying a column/table that migrate() would have added
# fails outright (confirmed happening for real: it took the storage-cap
# step down mid-run, after the download had already landed, leaving the
# queue row stuck since step 5 never got to clear it). Calling migrate()
# here first keeps this script's view of the schema honest regardless of
# service restart timing.
(cd "$DOWNBEAT_DIR" && node --env-file-if-exists=.env -e "
import('./server/db/migrate.js').then(async ({ migrate }) => {
  migrate();
  const { db } = await import('./server/db/connection.js');
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
# --cleanup: without it, the source .m4a is never deleted after conversion —
# once its .mp3 gets moved out of staging in step 3, the *next* unrelated
# ingest run finds the .m4a still sitting there with no .mp3 next to it
# anymore, silently reconverts it, and re-adds the same track to the
# library again (confirmed happening for real, resurrecting an already
# deleted mismatched-artist track on an unrelated run).
~/.local/bin/uv run process-music --list-file "$LIST_FILE" -o "$STAGING_DIR" -f "$FORMAT" --cleanup

echo "=== 3/5: flattening into Downbeat's library ==="
# ttml alongside the audio format: gamdl (via process-music, --synced-lyrics-format
# ttml) downloads Apple's own synced lyrics for every track that has them —
# same rename convention lands both files under the same basename, giving
# Downbeat a co-located sidecar it can prefer over an LRCLIB search match.
~/.local/bin/uv run flatten-directory "$STAGING_DIR" "$DOWNBEAT_DIR/library" --formats "$FORMAT" ttml --action move

echo "=== 4/5: rescanning, marking new tracks for separation, enforcing storage cap ==="
# Marks new tracks 'queued' directly rather than calling requestSeparation()
# here — that would spawn Demucs as *this short-lived script's own* child
# process, keeping the script (and step 5, clearing the queue) blocked
# until separation finishes, potentially minutes after the track was
# actually fetched. Instead this just writes the DB row and touches a
# trigger file downbeat.service (already running, staying up regardless of
# how long separation takes) watches to pick up the actual work.
(cd "$DOWNBEAT_DIR" && node --env-file-if-exists=.env -e "
import('./server/services/scanner.service.js').then(async ({ scanLibrary }) => {
  console.log('rescan:', JSON.stringify(await scanLibrary()));

  const { enforceLibraryStorageCap } = await import('./server/services/library.service.js');
  const { db } = await import('./server/db/connection.js');
  const fs = await import('node:fs');

  const unsplit = db.prepare('SELECT id FROM tracks WHERE id NOT IN (SELECT track_id FROM track_stems)').all();
  const insertQueued = db.prepare(\"INSERT INTO track_stems (track_id, status) VALUES (?, 'queued')\");
  for (const row of unsplit) insertQueued.run(row.id);
  console.log('marked', unsplit.length, 'track(s) queued for separation');
  fs.writeFileSync('data/separation-queue.trigger', '');

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
