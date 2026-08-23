#!/bin/bash
# Watchdog: reacts to Downbeat's "Add to queue" button by running ingest.sh
# automatically, instead of requiring someone to SSH in and run it by hand.
#
# Downbeat's queueIngestRequest() touches an inert marker file
# (downbeat/data/pending_ingest.trigger) whenever a row is added to
# pending_ingest — this script is the only thing that watches it. Downbeat
# itself never invokes gamdl/process-music, directly or indirectly; this is
# the one place in either repo that does, matching the standing DRM
# boundary (MusicWrangler reaches into Downbeat, never the other way).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

TRIGGER_FILE="/home/opc/downbeat/data/pending_ingest.trigger"
LOCK_FILE="$(pwd)/.watch-queue.lock"
DEBOUNCE_SECONDS=10

echo "watch-queue: watching $TRIGGER_FILE"

while true; do
  inotifywait -e modify,attrib "$TRIGGER_FILE" >/dev/null 2>&1

  # Several "Add to queue" clicks in quick succession each touch the file —
  # wait for things to go quiet before actually running, so they land in
  # one ingest.sh pass instead of several overlapping ones.
  while inotifywait -e modify,attrib -t "$DEBOUNCE_SECONDS" "$TRIGGER_FILE" >/dev/null 2>&1; do
    :
  done

  echo "watch-queue: trigger fired, running ingest.sh"
  # flock guards against a second run starting while a slow gamdl download
  # from a previous trigger is still in flight. The || here (not set -e)
  # is what keeps the watchdog looping after a failed or skipped run —
  # ingest.sh's own output above this line has the real error, if any.
  flock -n "$LOCK_FILE" ./ingest.sh || echo "watch-queue: run skipped (already in progress) or ingest.sh failed — see above"
done
