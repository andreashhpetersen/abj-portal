#!/usr/bin/env bash
#
# Assert that a running portal serves what it is supposed to.
#
# CI runs this against the freshly built container, the deploy workflow runs it
# against production after a release, and you can run it by hand against a local
# container. Same assertions every time, so a green pull request means the same
# thing as a green terminal.
#
#   ./deploy/smoke.sh                             # http://127.0.0.1:8000
#   ./deploy/smoke.sh http://127.0.0.1:8011       # dev runserver already owns 8000
#   ./deploy/smoke.sh https://portal.ab-jaeger.dk # a real deployment
#
# Every expectation holds over both http and https, so the same script works
# against a container with the HTTPS redirect disabled and against the real site
# behind Caddy.
#
# Prints every result, then exits non-zero if any of them failed.

set -uo pipefail

BASE=${1:-http://127.0.0.1:8000}
READY_TIMEOUT=${READY_TIMEOUT:-30}

fail=0

ok() { printf 'ok   %-24s %s\n' "$1" "$2"; }
bad() {
	printf 'FAIL %-24s %s\n' "$1" "$2"
	fail=1
}

# Wait for gunicorn to bind before judging anything, or a slow start reads as a
# broken deployment.
waited=0
until curl -fsS --max-time 3 "$BASE/api/health/" >/dev/null 2>&1; do
	waited=$((waited + 1))
	if [ "$waited" -ge "$READY_TIMEOUT" ]; then
		printf 'FAIL %s/api/health/ did not answer within %ss\n' "$BASE" "$READY_TIMEOUT"
		exit 1
	fi
	sleep 1
done

check() { # path  expected-status  what it proves
	code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$BASE$1")
	if [ "$code" = "$2" ]; then
		ok "$1" "$code  $3"
	else
		bad "$1" "$code (want $2)  $3"
	fi
}

# The health endpoint is exempt from the HTTPS redirect, which is what lets the
# container's own HEALTHCHECK reach it without X-Forwarded-Proto.
check /api/health/ 200 'health endpoint answers'
check / 200 'SPA shell'
check /booking/2026-08 200 'client-side route falls back to index.html'
# The one that matters most: without the lookahead in the catch-all, this would
# be 200 with HTML, and every mistyped API path would look like a working page.
check /api/does-not-exist/ 404 'unknown API route must NOT get the SPA'
check /admin/ 302 'admin still reachable, not swallowed by the SPA'
# 403 rather than 404 or 200 proves two things at once: the shop-rental routes
# are mounted, and they are closed to anyone not logged in. A 200 here would mean
# applicant data had been left readable by the whole internet.
check /api/shop-rentals/applications/ 403 'shop-rental applications are mounted and closed'
check /erhverv 200 'committee route falls back to index.html'

body=$(curl -s --max-time 5 "$BASE/")
case $body in
*'id="root"'*) ok / 'index.html carries the React mount point' ;;
*) bad / 'index.html is not the SPA shell' ;;
esac
case $body in
*'/assets/index-'*) ok / 'index.html links a hashed bundle' ;;
*) bad / 'index.html links no built bundle' ;;
esac

exit $fail
