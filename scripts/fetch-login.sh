#!/usr/bin/env bash
# Save one login for the scheduled fetcher, sealed with systemd-creds (TPM2 + host key), and (re)start the
# fetcher. Usage: fetch-login.sh isracard|hapoalim. Values are read interactively (password hidden), passed
# through a pipe, never as command arguments, and never written in plain text.
set -euo pipefail

SOURCE=${1:-}
case "$SOURCE" in
  isracard | hapoalim) ;;
  *) echo "usage: $0 isracard|hapoalim"; exit 1 ;;
esac
CRED=/etc/credstore.encrypted/family-budget-$SOURCE.cred
cd "$(dirname "$0")/.."

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo make $SOURCE-login"
  exit 1
fi

if [ "$SOURCE" = isracard ]; then
  read -rp "Isracard ID number (תעודת זהות): " LOGIN_ID
  read -rp "First 6 digits of one card: " LOGIN_CARD6
  [[ "$LOGIN_ID" =~ ^[0-9]{5,9}$ ]] || { echo "ID must be 5-9 digits"; exit 1; }
  [[ "$LOGIN_CARD6" =~ ^[0-9]{6}$ ]] || { echo "Card prefix must be exactly 6 digits"; exit 1; }
  read -rsp "Isracard website password (hidden): " LOGIN_PASSWORD
else
  read -rp "Bank Hapoalim user code (קוד משתמש): " LOGIN_ID
  [[ "$LOGIN_ID" =~ ^[A-Za-z0-9]{2,20}$ ]] || { echo "User code must be 2-20 letters or digits"; exit 1; }
  LOGIN_CARD6=
  read -rsp "Bank Hapoalim website password (hidden): " LOGIN_PASSWORD
fi
echo
[ -n "$LOGIN_PASSWORD" ] || { echo "Password is empty"; exit 1; }

install -d -m 700 /etc/credstore.encrypted
SOURCE="$SOURCE" LOGIN_ID="$LOGIN_ID" LOGIN_CARD6="$LOGIN_CARD6" LOGIN_PASSWORD="$LOGIN_PASSWORD" python3 -c '
import json, os
e = os.environ
login = {"id": e["LOGIN_ID"], "card6Digits": e["LOGIN_CARD6"]} if e["SOURCE"] == "isracard" else {"userCode": e["LOGIN_ID"]}
print(json.dumps({**login, "password": e["LOGIN_PASSWORD"]}))' \
  | systemd-creds encrypt --with-key=host+tpm2 --name="$SOURCE.json" - "$CRED"
unset LOGIN_PASSWORD
chmod 600 "$CRED"
echo "Sealed $CRED (decryptable only on this machine)."

make install-fetcher
