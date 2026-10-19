#!/usr/bin/env bash
# Started by family-budget-backup.service. Streams the unencrypted backup archive out of the app container
# straight into age (BACKUP_AGE_RECIPIENT: the owner's ssh-ed25519 or age1 public key), so plaintext never
# reaches a file. The same bytes are test-restored in the container's tmpfs; the archive is kept only if that
# restore passes. Then: copy to BACKUP_REMOTE with rclone (optional) and prune both sides after KEEP_DAYS.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
: "${BACKUP_AGE_RECIPIENT:?set in /etc/family-budget-backup.env}"
keep=${KEEP_DAYS:-30}
compose() { /usr/bin/docker compose --project-directory "$repo" "$@"; }
app() { compose exec -T budget "$@"; }

data=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' \
  "$(compose ps -q budget)")
dir="$data/backups"
install -d -m 700 "$dir"
name="budget-$(date +%Y%m%d-%H%M%S).zip.age"
partial="$dir/.$name.partial"
work=$(mktemp -d -p /run)
check=$(app mktemp -d)
trap 'rm -f "$partial"; rm -rf "$work"; app rm -rf "$check"' EXIT
mkfifo "$work/check"

app family-budget restore - "$check/restored" --plain < "$work/check" 2>/dev/null &
restore=$!
app family-budget backup - --plain | tee "$work/check" | age -r "$BACKUP_AGE_RECIPIENT" > "$partial"
restored=0
wait "$restore" || restored=$?
[ "$restored" -eq 0 ] || { echo "test restore failed; archive discarded" >&2; exit 1; }
chmod 600 "$partial"
mv "$partial" "$dir/$name"
find "$dir" -maxdepth 1 -name 'budget-*.age' -mtime "+$keep" -print -delete

if [ -n "${BACKUP_REMOTE:-}" ]; then
  rclone copyto "$dir/$name" "$BACKUP_REMOTE/$name"  # verifies the uploaded MD5
  rclone lsf "$BACKUP_REMOTE/$name" | grep -q . || { echo "upload not found on $BACKUP_REMOTE" >&2; exit 1; }
  rclone delete "$BACKUP_REMOTE" --min-age "${keep}d" --include 'budget-*.age'
  echo "uploaded: $BACKUP_REMOTE/$name"
fi
echo "backup ok: $dir/$name ($(du -h "$dir/$name" | cut -f1))"
