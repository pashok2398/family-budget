#!/bin/sh
# Started by family-budget-fetcher.service. Joins the logins systemd decrypted into $CREDENTIALS_DIRECTORY
# (<source>.json each) into one JSON object {"<source>": login, ...} and pipes it to the fetcher's stdin.
# Nothing is written to disk and no value appears in arguments or logs.
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)
[ -n "${CREDENTIALS_DIRECTORY:-}" ] || { echo "no credentials directory; start through systemd" >&2; exit 1; }
{
  printf '{'
  sep=
  for f in "$CREDENTIALS_DIRECTORY"/*.json; do
    [ -e "$f" ] || continue
    name=${f##*/}
    printf '%s"%s":' "$sep" "${name%.json}"
    cat "$f"
    sep=,
  done
  printf '}'
} | exec /usr/bin/docker compose --project-directory "$repo" --profile fetch run --rm -T --name family-budget-fetcher fetcher
