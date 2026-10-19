#!/usr/bin/env bash
# Seal the backup passphrase with systemd-creds (TPM2 + host key) and install the nightly backup timer.
# The passphrase is read hidden, piped, never an argument or a plain-text file. Keep a copy of it somewhere
# else (password manager): the sealed copy only decrypts on this machine, and a backup without it is lost.
set -euo pipefail
CRED=/etc/credstore.encrypted/family-budget-backup.cred
cd "$(dirname "$0")/.."

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo make backup-setup"
  exit 1
fi

read -rsp "Backup passphrase (12+ characters, hidden): " PASSPHRASE; echo
read -rsp "Repeat passphrase: " REPEAT; echo
[ "$PASSPHRASE" = "$REPEAT" ] || { echo "Passphrases differ"; exit 1; }
[ ${#PASSPHRASE} -ge 12 ] || { echo "Use at least 12 characters"; exit 1; }

install -d -m 700 /etc/credstore.encrypted
printf '%s\n' "$PASSPHRASE" | systemd-creds encrypt --with-key=host+tpm2 --name=backup-passphrase - "$CRED"
unset PASSPHRASE REPEAT
chmod 600 "$CRED"
echo "Sealed $CRED (decryptable only on this machine)."

make install-backup
