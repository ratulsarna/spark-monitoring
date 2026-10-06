# Spark monitoring

<img width="1441" height="960" alt="Pasted 2026-09-02 at 5 59 11 PM" src="https://github.com/user-attachments/assets/f8724017-76d6-4ac5-8545-5ba06b57c8ce" />

---

This repository owns a loopback-bound Prometheus, Grafana, and node-exporter
stack for model-serving telemetry. The serving stack owns model processes,
gateway routing, and raw engine metrics. Tailscale Serve is the external Grafana
boundary.

For a new deployment, follow [SETUP.md](SETUP.md). It gives coding agents the
full procedure for a two-Spark serving fleet.

The runtime expects a llama-swap-compatible `/running` endpoint from every
serving gateway. The host telemetry panels also query llama-swap's
`llamaswap_*` Prometheus metrics. This repository does not install or manage
llama-swap.

Prometheus listens on `127.0.0.1:9090`, Grafana on `127.0.0.1:3100`, and
node-exporter on `127.0.0.1:9100`. Prometheus history and Grafana state live in
the external Docker volumes named in `site.yml`; the lifecycle never replaces
an existing volume.

## Site configuration

`site.yml` is ignored, must be a regular file with mode `0600`, and is
validated strictly: unknown keys, duplicate YAML keys, unsupported
engine/metric pairs, ambiguous identities, invalid endpoints, ports, or shared
jobs all fail closed.

`serving_nodes` defines each gateway's `/running` base URL and its Prometheus
gateway scrape. `model_telemetry` is the only model policy table. Each row
defines:

- its serving node and one or more `/running` identities;
- the normalized `model` label used by the shared dashboard;
- the engine and its activity metric;
- the expected proxy port reported by `/running`;
- the Prometheus job, exact target, scheme, and metrics path;
- optional `model_label_source` relabeling for endpoints that can swap models;
- optional extra throughput scrapes for a single identity.

To add a model, add or extend one `model_telemetry` row. Use
`vllm:num_requests_running`, `sglang:num_running_reqs`,
`llamacpp:requests_processing`, `tensorfold:requests_running`, or
`tensorfold_requests_inflight` (engine `tensorfold-health`) for the
corresponding engine. Models sharing a
job may use separate targets; an unloaded sibling target may be down. Models
that share one endpoint belong in one row under `identities`, with
`model_label_source` naming the raw metric label that identifies the current
model. Shell code does not contain model-name cases.

`install` and `reload` render the ignored `prometheus.yml` and
`.local/spark-monitoring.service` directly from this configuration. The
generated unit uses the checkout's absolute path, so clones may live anywhere.

The lifecycle verifies the configured Tailscale Serve route and user lingering.
It does not modify either one.

## Lifecycle

Use [SETUP.md](SETUP.md) for the first installation. Ordinary `validate` is
read-only and does not fetch images.

Normal operations are:

```bash
./bin/monitoring validate
./bin/monitoring status
systemctl --user start spark-monitoring.service
systemctl --user stop spark-monitoring.service
systemctl --user reload spark-monitoring.service
systemctl --user restart spark-monitoring.service
```

`status` verifies service and mount ownership, external volume identity, the
Tailscale route, Prometheus and Grafana health, dashboard provisioning, required
gateway/node targets, and every active worker. For a loaded worker it requires
one exact healthy target, the expected proxy port, the normalized current model
label, the engine activity metric, and a sample newer than 15 seconds. It
rechecks `/running` after every convergence attempt and fails if the active set
changes.

Reload validates first, then recreates only Prometheus and Grafana. Stopping is
site-independent and removes monitoring containers while retaining external
volumes. A known-good commit plus the private `site.yml` is the recovery unit;
do not recreate the volumes during recovery.

Credentials, concrete topology, runtime state, logs, model inventory, generated
configuration, and model data do not belong in Git.
