#!/usr/bin/env bash
set -Eeuo pipefail

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MONITORING_SOURCE_ONLY=true source "$root_dir/bin/monitoring"
site_json=$(python3 "$root_dir/lib/monitoring_config.py" validate "$root_dir/site.example.yml")

known_snapshot='[{"node":"node-a","model":"demo-sglang-model","proxy":"http://127.0.0.1:32101"}]'

if validate_active_snapshot '[{"node":"node-a","model":"missing-model","proxy":"http://127.0.0.1:32101"}]' 2>/dev/null; then
  fail 'missing active mapping fixture passed'
fi

if validate_active_snapshot '[{"node":"node-a","model":"demo-sglang-model","proxy":"http://127.0.0.1:39999"}]' 2>/dev/null; then
  fail 'wrong proxy port fixture passed'
fi

targets='{"data":{"activeTargets":[
  {"labels":{"job":"sglang-node-a","instance":"127.0.0.1:32101","service":"model-worker","node":"node-a","engine":"sglang"},"scrapeUrl":"http://127.0.0.1:32101/metrics","health":"up"},
  {"labels":{"job":"sglang-node-a","instance":"127.0.0.1:39998","service":"model-worker","node":"node-a","engine":"sglang"},"scrapeUrl":"http://127.0.0.1:39998/metrics","health":"down"}
]}}'

curl() {
  printf '%s\n' '{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[0,"1"]}]}}'
}
telemetry_loaded=()
active_worker_telemetry_healthy "$known_snapshot" "$targets"
[[ ${telemetry_loaded[0]} == node-a/demo-sglang-model:sglang-node-a@127.0.0.1:32101 ]]

curl() {
  printf '%s\n' '{"status":"success","data":{"resultType":"vector","result":[]}}'
}
telemetry_loaded=()
if active_worker_telemetry_healthy "$known_snapshot" "$targets"; then
  fail 'stale or wrongly labeled metric fixture passed'
fi

snapshot_counter=$(mktemp)
trap 'rm -f -- "$snapshot_counter"' EXIT
printf '0\n' >"$snapshot_counter"
fetch_active_snapshot() {
  local count
  count=$(<"$snapshot_counter")
  if ((count == 0)); then
    printf '1\n' >"$snapshot_counter"
    printf '%s\n' "$known_snapshot"
  else
    printf '%s\n' '[{"node":"node-a","model":"demo-vllm-model","proxy":"http://127.0.0.1:32100"}]'
  fi
}
fetch_targets() { printf '%s\n' '{"data":{"activeTargets":[]}}'; }
target_job_healthy() { return 0; }
active_worker_telemetry_healthy() { return 0; }
if qualify_active_workers 2>/dev/null; then
  fail 'active-set change fixture passed'
fi

# Restore the real lifecycle functions after the qualify_active_workers stubs.
MONITORING_SOURCE_ONLY=true source "$root_dir/bin/monitoring"
site_json=$(python3 "$root_dir/lib/monitoring_config.py" validate "$root_dir/site.example.yml")

# TensorFold active-worker policy and telemetry fixtures.
tensorfold_snapshot='[{"node":"node-a","model":"demo-tensorfold-model","proxy":"http://127.0.0.1:32103"}]'

if validate_active_snapshot '[{"node":"node-a","model":"demo-tensorfold-model","proxy":"http://127.0.0.1:39997"}]' 2>/dev/null; then
  fail 'tensorfold wrong proxy port fixture passed'
fi

if validate_active_snapshot '[{"node":"node-a","model":"demo-tensorfold-model","proxy":"http://127.0.0.1:32100"}]' 2>/dev/null; then
  fail 'tensorfold wrong policy row fixture passed'
fi

tensorfold_targets_up='{"data":{"activeTargets":[
  {"labels":{"job":"tensorfold-node-a","instance":"127.0.0.1:32103","service":"model-worker","node":"node-a","engine":"tensorfold"},"scrapeUrl":"http://127.0.0.1:32103/metrics","health":"up"},
  {"labels":{"job":"tensorfold-node-a","instance":"127.0.0.1:32104","service":"model-worker","node":"node-a","engine":"tensorfold"},"scrapeUrl":"http://127.0.0.1:32104/metrics","health":"up"}
]}}'

curl() {
  printf '%s\n' '{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[0,"1"]}]}}'
}
telemetry_loaded=()
active_worker_telemetry_healthy "$tensorfold_snapshot" "$tensorfold_targets_up"
[[ ${telemetry_loaded[0]} == node-a/demo-tensorfold-model:tensorfold-node-a@127.0.0.1:32103 ]]

tensorfold_targets_wrong_engine='{"data":{"activeTargets":[
  {"labels":{"job":"tensorfold-node-a","instance":"127.0.0.1:32103","service":"model-worker","node":"node-a","engine":"vllm"},"scrapeUrl":"http://127.0.0.1:32103/metrics","health":"up"}
]}}'

telemetry_loaded=()
if active_worker_telemetry_healthy "$tensorfold_snapshot" "$tensorfold_targets_wrong_engine" 2>/dev/null; then
  fail 'tensorfold wrong engine label fixture passed'
fi

curl() {
  printf '%s\n' '{"status":"success","data":{"resultType":"vector","result":[]}}'
}
telemetry_loaded=()
if active_worker_telemetry_healthy "$tensorfold_snapshot" "$tensorfold_targets_up" 2>/dev/null; then
  fail 'tensorfold stale or wrongly labeled metric fixture passed'
fi

printf 'telemetry fixtures passed\n'
