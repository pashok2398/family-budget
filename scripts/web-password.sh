#!/usr/bin/env bash
# Set the login for the web app (HTTP basic auth at the gateway). Stores only a SHA-512 crypt hash, readable by
# the gateway's nginx user (uid 101). The password is read hidden and piped, never an argument.
set -euo pipefail
FILE=${FAMILY_BUDGET_HTPASSWD:-/etc/family-budget/htpasswd}
cd "$(dirname "$0")/.."
[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo make web-password"; exit 1; }

read -rp "User name [family]: " NAME
NAME=${NAME:-family}
[[ "$NAME" =~ ^[A-Za-z0-9._-]{1,32}$ ]] || { echo "Use letters, digits, . _ -"; exit 1; }
read -rsp "Password (12+ characters, hidden): " PASSWORD; echo
read -rsp "Repeat password: " REPEAT; echo
[ "$PASSWORD" = "$REPEAT" ] || { echo "Passwords differ"; exit 1; }
[ ${#PASSWORD} -ge 12 ] || { echo "Use at least 12 characters"; exit 1; }

install -d -m 755 "$(dirname "$FILE")"
[ -d "$FILE" ] && rmdir "$FILE"  # docker creates a directory when the file was missing
HASH=$(printf '%s' "$PASSWORD" | openssl passwd -6 -stdin)
unset PASSWORD REPEAT
umask 027
printf '%s:%s\n' "$NAME" "$HASH" > "$FILE.new"
chown root:101 "$FILE.new"
chmod 640 "$FILE.new"
mv "$FILE.new" "$FILE"
echo "Saved login '$NAME' in $FILE."
docker compose up -d --force-recreate gateway
