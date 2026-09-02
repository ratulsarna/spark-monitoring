# Setup guide for coding agents

This guide installs spark-monitoring on one Linux host and connects it to an
existing model-serving fleet. It is written for a coding agent working under an
operator's supervision.

The repository does not install inference engines, start models, or expose
worker metrics. Complete those parts before installing monitoring.

## Know what this deployment monitors

Choose one machine as the monitoring host. It runs Prometheus, Grafana, and one
node-exporter through Docker Compose.

A `serving_nodes` entry represents a serving gateway, not a physical GPU host.
For example:

- Two Sparks with separate gateways need two `serving_nodes` entries.
- One tensor-parallel model spanning two Sparks through one gateway needs one
  `serving_nodes` entry and one worker telemetry entry.

Prometheus must be able to reach every configured gateway and worker metrics
endpoint from the monitoring host. The bundled node-exporter reports host data
only for the monitoring host. It does not collect CPU, memory, filesystem, or
thermal data from the second Spark.

Each serving gateway must provide a `/running` response with this shape:

```json
{
  "running": [
    {
      "model": "model-id",
      "proxy": "http://127.0.0.1:32100"
    }
  ]
}
```

The runtime expects this llama-swap API contract. It does not install or manage
llama-swap. A different gateway must expose compatible `/running` data and a
scrapeable Prometheus endpoint. Without the `llamaswap_*` series, model-worker
panels still work but the gateway hardware panels remain empty.

The worker endpoint must export Prometheus metrics for one supported engine:

| Engine | Required activity metric |
|---|---|
| vLLM | `vllm:num_requests_running` |
| SGLang | `sglang:num_running_reqs` |
| llama.cpp | `llamacpp:requests_processing` |

The gateway itself must also expose Prometheus metrics at the configured
`gateway_scrape` target.

## Work safely

Start with read-only discovery. Do not load or unload models, restart serving
processes, send inference requests, or change network listeners while gathering
the values for `site.yml`.

Ask the operator before installing packages, changing Tailscale Serve, enabling
user lingering, or creating Docker volumes. Follow these rules throughout the
setup:

- Keep `site.yml` untracked and mode `0600`.
- Keep Prometheus, Grafana, and node-exporter bound to loopback.
- Use tailnet-only endpoints or an existing private reverse proxy for remote
  scrapes. Do not expose raw metrics to the public internet.
- Do not commit runtime responses, generated files, private hostnames,
  addresses, or credentials.

Record the `/running` response from every gateway before making changes. Compare
it with the response after installation. The active model set and proxy ports
must not change.

## Check the monitoring host

The monitoring host needs:

- Linux with systemd user services
- Docker Engine with Docker Compose v2
- Tailscale connected to the intended tailnet, with Tailscale Serve available
- `curl`, `git`, `id`, `jq`, `loginctl`, `python3`, `stat`, `systemctl`, and
  `systemd-analyze`
- PyYAML for Python 3
- permission to run Docker as the current user

The container limits add up to 1.625 GiB of memory and 2.5 CPU cores.
Prometheus retains up to 5 GB. Check that the monitoring host has room before
installing the stack beside a model server.

Run this preflight without changing the host:

```bash
for setup_command in curl docker git id jq loginctl python3 stat systemctl systemd-analyze tailscale; do
  command -v "$setup_command" || exit 1
done
docker compose version
python3 -c 'import yaml'
docker info >/dev/null
tailscale status
```

If a dependency is missing, stop and ask before installing it.

## Discover the serving topology

For every gateway, collect:

1. A base URL that the monitoring host can query at `/running`.
2. A Prometheus scrape target for the gateway.
3. The exact model names returned by `/running`.
4. The explicit port from each model's `proxy` URL.
5. A Prometheus scrape target for each worker.
6. The inference engine and its required activity metric.
7. The raw metric label that identifies the model, if one endpoint can report
   more than one model.

Check each endpoint from the monitoring host. Replace the example URLs with the
real private endpoints:

```bash
curl -fsS https://spark-a.example.invalid/running | jq .
curl -fsS https://spark-a.example.invalid/metrics | head
curl -fsS https://spark-a.example.invalid/workers/model-a/metrics \
  | grep -E '^(vllm:num_requests_running|sglang:num_running_reqs|llamacpp:requests_processing)'
```

Repeat the checks for the second gateway if it has one. A worker target may use
`127.0.0.1` only when the worker metrics endpoint runs on the monitoring host.
Remote targets need a tailnet-reachable hostname or address and an explicit
port. If a required endpoint is loopback-only on the remote Spark, stop and ask
the operator to expose it through a private route. This repository does not
manage serving-side routes.

Do not continue until the monitoring host can query every gateway and every
currently loaded worker. Unloaded worker endpoints may be down after they have
a valid policy entry.

## Clone and configure the repository

Run these commands on the monitoring host:

```bash
git clone https://github.com/ratulsarna/spark-monitoring.git
cd spark-monitoring
cp site.example.yml site.yml
chmod 600 site.yml
```

Edit `site.yml`. This example assumes that each Spark runs its own gateway and
one vLLM worker:

```yaml
storage:
  prometheus_volume: spark-monitoring_prometheus-data
  grafana_volume: spark-monitoring_grafana-data

grafana:
  serve_host: spark-a.example.invalid
  serve_port: 8443

prometheus:
  cluster: two-spark
  node_exporter_node: spark-a

serving_nodes:
  - name: spark-a
    running_url: http://127.0.0.1:30000
    gateway_scrape:
      job: gateway-spark-a
      target: 127.0.0.1:30000

  - name: spark-b
    running_url: https://spark-b.example.invalid:443
    gateway_scrape:
      job: gateway-spark-b
      target: spark-b.example.invalid:443
      scheme: https

model_telemetry:
  - node: spark-a
    identities:
      - running: model-a
        model: model-a
    engine: vllm
    proxy_port: 32100
    activity_metric: vllm:num_requests_running
    scrape:
      job: vllm-spark-a
      target: 127.0.0.1:32100

  - node: spark-b
    identities:
      - running: model-b
        model: model-b
    engine: vllm
    proxy_port: 32100
    activity_metric: vllm:num_requests_running
    scrape:
      job: vllm-spark-b
      target: spark-b.example.invalid:443
      scheme: https
      metrics_path: /workers/model-b/metrics
```

Set each field from observed data:

| Field | Value |
|---|---|
| `serving_nodes[].name` | Stable local name for the gateway |
| `running_url` | Base URL whose `/running` response matches the required shape |
| `gateway_scrape` | Exact target, scheme, and path for gateway metrics |
| `identities[].running` | Exact value from `/running[].model` |
| `identities[].model` | Model label shown in Grafana |
| `identities[].metric` | Raw worker metric label value when it differs from `running` |
| `engine` | `vllm`, `sglang`, or `llamacpp` |
| `proxy_port` | Explicit port from `/running[].proxy` |
| `activity_metric` | Required metric from the engine table above |
| `scrape` | Worker metrics endpoint reachable from the monitoring host |

For a fixed endpoint with one model, omit `model_label_source`. The renderer
adds the configured `model` label.

For an endpoint that can report more than one model, put the identities in one
telemetry entry and set `model_label_source` to the raw Prometheus label name.
Set `identities[].metric` when that raw label value differs from the model name
returned by `/running`. See `site.example.yml` for the full shape.

Check the private file and parse it before creating state:

```bash
test "$(stat -c '%a' site.yml)" = 600
python3 lib/monitoring_config.py validate site.yml >/dev/null
git status --short
```

`site.yml` is the private source. Git must ignore it and the generated
`prometheus.yml`. Do not run `./bin/monitoring validate` yet. That command checks
installed runtime files, initialized volumes, and a locally available
Prometheus image.

## Configure the Grafana route

Find the monitoring host's Tailscale DNS name:

```bash
tailscale status --json | jq -r '.Self.DNSName | rtrimstr(".")'
```

Set `grafana.serve_host` to that exact name. With operator approval, create the
route and allow the user service to keep running after logout:

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:3100
sudo loginctl enable-linger "$USER"
```

The HTTPS port must match `grafana.serve_port`. The lifecycle checks the exact
host, port, and loopback destination.

## Install

Confirm that the configured volume names do not exist:

```bash
docker volume inspect spark-monitoring_prometheus-data spark-monitoring_grafana-data
```

For a fresh deployment, Docker should report that both volumes are absent. If
either volume exists, stop. Do not delete or replace it without the operator's
approval.

Create the empty volumes and install the user service:

```bash
./bin/monitoring initialize
./bin/monitoring install
```

`install` pulls the pinned images, renders the ignored Prometheus configuration
and systemd unit, starts the Compose project, and runs the live status checks.
It does not manage the serving processes.

## Verify the result

Run:

```bash
./bin/monitoring status
systemctl --user is-active spark-monitoring.service
curl -fsS http://127.0.0.1:9090/-/ready
curl -fsS http://127.0.0.1:3100/api/health | jq .
tailscale serve status
git status --short
```

Query `/running` on every gateway again. Its sorted response must match the
pre-install response. Do not use an inference request as a monitoring test.

Open Grafana at:

```text
https://<grafana.serve_host>:<grafana.serve_port>/
```

Grafana allows anonymous Viewer access through the Tailscale route. Prometheus
and Grafana remain bound to loopback.

A clean completion report states:

- the monitoring host and checkout path;
- the Grafana URL;
- whether `./bin/monitoring status` passed;
- the loaded model mappings reported by `status`;
- whether `/running` stayed unchanged;
- whether any serving process restarted or changed state;
- the worktree state and whether anything was committed or pushed.

## Diagnose setup failures

Use the error from `./bin/monitoring install` or `status` before changing
anything:

| Error | Check |
|---|---|
| `site config is invalid` | Run the config parser directly and fix the named field. |
| `Grafana Serve prerequisite is missing` | Compare the exact Tailscale DNS name, HTTPS port, and loopback destination with `site.yml`. |
| `required scrape job is not healthy` | Query that gateway metrics endpoint from the monitoring host. |
| `no unique scrape policy for active worker` | Match `identities[].running` to the exact `/running[].model` value. |
| `active worker proxy port does not match` | Match `proxy_port` to the explicit port in `/running[].proxy`. |
| `active worker has no healthy scrape` | Check the target, scheme, metrics path, and private network route. |
| `active worker has no correctly labeled telemetry` | Check the activity metric, dashboard model value, `model_label_source`, and optional identity `metric`. |

Do not fix a monitoring failure by restarting or unloading a model. Correct the
endpoint or policy that failed.
