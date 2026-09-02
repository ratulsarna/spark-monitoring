# Spark monitoring

This repository owns a loopback-bound Prometheus, Grafana, and node-exporter
stack for model-serving telemetry. The serving stack owns model processes,
gateway routing, and raw engine metrics. Tailscale Serve is the external Grafana
boundary.

Prometheus listens on `127.0.0.1:9090`, Grafana on `127.0.0.1:3100`, and
node-exporter on `127.0.0.1:9100`. Prometheus history and Grafana state live in
the external Docker volumes named in `site.yml`; the lifecycle never replaces
an existing volume.

## Site configuration

Copy the public example, fill in the local topology, and keep it private:

```bash
cp site.example.yml site.yml
chmod 600 site.yml
```

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
`vllm:num_requests_running`, `sglang:num_running_reqs`, or
`llamacpp:requests_processing` for the corresponding engine. Models sharing a
job may use separate targets; an unloaded sibling target may be down. Models
that share one endpoint belong in one row under `identities`, with
`model_label_source` naming the raw metric label that identifies the current
model. Shell code does not contain model-name cases.

`install` and `reload` render the ignored `prometheus.yml` and
`.local/spark-monitoring.service` directly from this configuration. The
generated unit uses the checkout's absolute path, so clones may live anywhere.

Before installation, create the exact HTTPS Serve route configured under
`grafana` and enable user lingering:

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:3100
sudo loginctl enable-linger "$USER"
```

The lifecycle verifies these prerequisites and does not modify them.

## Lifecycle

On a new empty host:

```bash
./bin/monitoring initialize
./bin/monitoring install
```

`initialize` refuses existing volumes. `install` validates the site, fetches
pinned images, installs and enables the user service, and starts the Compose
project. Ordinary `validate` is read-only and does not fetch images.

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
