#!/bin/sh
# Started by family-budget-backup.service. Writes one encrypted archive into /data/backups, proves it decrypts
# by restoring it into the container's tmpfs, then removes archives older than KEEP_DAYS.
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)
pass="${CREDENTIALS_DIRECTORY:?start through systemd}/backup-passphrase"
name="budget-$(date +%Y%m%d-%H%M%S).fbb"
app() { /usr/bin/docker compose --project-directory "$repo" exec -T budget "$@"; }

app mkdir -p -m 700 /data/backups
app family-budget backup "/data/backups/$name" --passphrase-stdin < "$pass"
check=$(app mktemp -d)
app family-budget restore "/data/backups/$name" "$check/restored" --passphrase-stdin < "$pass" >/dev/null
app rm -rf "$check"
app find /data/backups -name 'budget-*.fbb' -mtime "+${KEEP_DAYS:-30}" -print -delete
echo "backup ok: $name ($(app du -h "/data/backups/$name" | cut -f1))"
