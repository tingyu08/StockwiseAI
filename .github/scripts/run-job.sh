#!/usr/bin/env bash
set -euo pipefail

job="${1:?job name is required}"
base="${BASE:?BACKEND_URL secret is required}"
token="${JOB_TOKEN:?JOB_TOKEN secret is required}"

start_response=$(curl -sf --max-time 60 -X POST "$base/api/v1/jobs/$job:run" \
  -H "X-Job-Token: $token")
run_id=$(jq -er '.data.run_id' <<<"$start_response")
echo "started $job as run $run_id"

for _ in $(seq 1 720); do
  if ! response=$(curl -sf --max-time 30 "$base/api/v1/jobs/runs/$run_id" \
    -H "X-Job-Token: $token"); then
    echo "status request failed temporarily; retrying"
    sleep 10
    continue
  fi
  status=$(jq -er '.data.status' <<<"$response")
  case "$status" in
    succeeded)
      jq '.data.result' <<<"$response"
      exit 0
      ;;
    failed)
      jq -r '.data.error // "job failed"' <<<"$response" >&2
      exit 1
      ;;
    queued|running)
      sleep 10
      ;;
    *)
      echo "unknown job status: $status" >&2
      exit 1
      ;;
  esac
done

echo "$job did not complete within two hours" >&2
exit 1
