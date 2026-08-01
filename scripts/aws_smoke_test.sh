#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:?Usage: scripts/aws_smoke_test.sh https://app.example.com}"
COOKIE_FILE="$(mktemp)"
EMAIL="smoke-$(date +%s)@example.com"
trap 'rm -f "$COOKIE_FILE"' EXIT

curl --fail --silent --show-error --cookie-jar "$COOKIE_FILE" \
  --header "Content-Type: application/json" \
  --data "{\"email\":\"$EMAIL\",\"password\":\"deployment-smoke-password\"}" \
  "$BASE_URL/api/platform/auth/signup" >/dev/null

EXPERIMENT_ID="$(curl --fail --silent --show-error --cookie "$COOKIE_FILE" \
  --header "Content-Type: application/json" \
  --data '{"name":"Deployment smoke test","biologic_target":"human insulin","polymer_target":"PEG"}' \
  "$BASE_URL/api/platform/experiments" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

for attempt in $(seq 1 30); do
  BODY="$(curl --fail --silent --show-error --cookie "$COOKIE_FILE" "$BASE_URL/api/platform/experiments/$EXPERIMENT_ID")"
  STATUS="$(printf '%s' "$BODY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  if [ "$STATUS" = "done" ]; then
    printf '%s' "$BODY" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["progress"] == 100; assert data["results"]["summary"]["pdb_id"] == "4F1C"'
    echo "Deployment smoke test passed"
    exit 0
  fi
  if [ "$STATUS" = "failed" ]; then
    echo "$BODY"
    exit 1
  fi
  sleep 2
done

echo "Experiment did not finish within 60 seconds"
exit 1
