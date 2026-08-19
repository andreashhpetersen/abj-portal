#!/usr/bin/env bash
#
# Nightly logical backup of the portal database.
#
# UpCloud's managed PostgreSQL already does continuous WAL archiving, but a
# single-node plan only keeps three days of point-in-time recovery. Three days
# covers hardware failure; it does not cover the likelier disaster, which is
# data deleted or corrupted and nobody noticing until next month's board
# meeting. This script is what closes that gap, and it is the reason the cheap
# database plan is defensible — see INFRASTRUCTURE.md.
#
# The dump is encrypted to a public key, so this server can write backups but
# cannot read them back. Keep the matching private key offline, off this
# machine, and in more than one board member's hands: losing it destroys every
# archive at once.
#
# Requires: docker, age, rclone. Install on the server with
#   apt-get install -y age rclone
# and configure an S3-compatible remote for UpCloud Managed Object Storage:
#   rclone config    # type: s3, provider: Other, set endpoint + credentials
#
# Schedule it as root, and let cron mail failures somewhere a person reads:
#   17 3 * * *  /opt/abj-portal/backup.sh
#
# RESTORE-TEST THIS QUARTERLY. An untested backup is not a backup:
#   rclone cat "$BACKUP_REMOTE/<file>" | age -d -i key.txt > dump.pgc
#   pg_restore --clean --if-exists -d "$SCRATCH_DATABASE_URL" dump.pgc
# then compare row counts against production before trusting it.

set -euo pipefail

ENV_FILE=${ENV_FILE:-/opt/abj-portal/.env}
# Must be at least the major version of the managed database (currently 18).
# pg_dump refuses to run against a server newer than itself, so this pin has to
# be raised before the database is upgraded, not after.
POSTGRES_IMAGE=${POSTGRES_IMAGE:-postgres:18-alpine}
BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-90}
# A dump this small means the dump failed rather than that the portal is quiet.
MIN_DUMP_BYTES=${MIN_DUMP_BYTES:-4096}

log() { printf '%s backup: %s\n' "$(date --iso-8601=seconds)" "$*"; }
die() { log "FAILED — $*" >&2; exit 1; }

[[ -r $ENV_FILE ]] || die "cannot read $ENV_FILE"
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

: "${DATABASE_URL:?DATABASE_URL missing from $ENV_FILE}"
: "${BACKUP_AGE_RECIPIENT:?BACKUP_AGE_RECIPIENT missing from $ENV_FILE}"
: "${BACKUP_REMOTE:?BACKUP_REMOTE missing from $ENV_FILE}"

# The plaintext dump touches disk only briefly, and only readable by this user.
umask 077
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

stamp=$(date -u +%Y%m%dT%H%M%SZ)
name="abj-portal-${stamp}.pgc.age"
dump="$workdir/dump.pgc"

log "dumping database"
# --format=custom so pg_restore can be selective; no owner or ACL statements,
# because the managed instance's roles are not ours to recreate.
docker run --rm --network host -e PGCONNECT_TIMEOUT=15 "$POSTGRES_IMAGE" \
	pg_dump --format=custom --no-owner --no-privileges "$DATABASE_URL" > "$dump" \
	|| die "pg_dump returned non-zero"

size=$(stat -c %s "$dump")
[[ $size -ge $MIN_DUMP_BYTES ]] || die "dump is only ${size} bytes — refusing to upload it"
log "dumped ${size} bytes"

log "encrypting and uploading $name"
age --encrypt --recipient "$BACKUP_AGE_RECIPIENT" < "$dump" \
	| rclone rcat "$BACKUP_REMOTE/$name" \
	|| die "encrypt or upload failed"

log "pruning backups older than ${BACKUP_RETENTION_DAYS} days"
# Retention is a GDPR obligation as much as a housekeeping one: these archives
# hold resident personal data and must expire on the association's schedule.
rclone delete --min-age "${BACKUP_RETENTION_DAYS}d" "$BACKUP_REMOTE" \
	|| log "WARNING: prune failed; the upload itself succeeded"

log "done"
