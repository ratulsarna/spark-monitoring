# Spark monitoring

This repository owns the model-serving monitoring stack: Prometheus, Grafana,
node-exporter, scrape and relabeling policy, dashboard provisioning, validation,
and lifecycle operations. The serving stack owns model processes, gateway
routing, and the raw metrics contract.

Prometheus listens on `127.0.0.1:9090`, Grafana on `127.0.0.1:3100`, and
node-exporter on `127.0.0.1:9100`. Compose uses host networking, while the local
HTTP listeners remain bound to loopback. Tailscale Serve is the external
boundary for Grafana.

Persistent state uses the external Docker volumes configured under `storage` in
`site.yml`. Compose cannot create them. Their configured identities keep
Prometheus history and Grafana state independent of the checkout path and
Compose project.

## Site configuration

Copy the example and replace every `.example.invalid` hostname with the local
site topology:

```bash
cp site.example.yml site.yml
chmod 600 site.yml
```

`site.yml` contains the external volume identities, Grafana Serve endpoint,
peer gateway addresses, and peer worker ports. It is ignored and must remain
mode `0600`. `install` renders the ignored `prometheus.yml` and
`.local/spark-monitoring.service` from the tracked templates. The generated
service uses the absolute path of the current checkout, so clones can live
anywhere.

Before installation, create the exact HTTPS Serve route configured in
`site.yml` and enable user lingering. With the example HTTPS port, the commands
are:

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:3100
sudo loginctl enable-linger "$USER"
```

These are explicit host prerequisites. The lifecycle verifies them and never
changes Tailscale Serve or lingering.

## Lifecycle

On a new empty host, create the state volumes and install the stack:

```bash
./bin/monitoring initialize
./bin/monitoring install
```

`initialize` refuses existing volumes. `install` verifies the site prerequisites
before writing runtime files, fetches pinned images, validates configuration,
installs and enables the user service, and starts the Compose project. Ordinary
`validate` does not fetch images or change the host.

Use these commands for normal operation:

```bash
./bin/monitoring validate
./bin/monitoring status
systemctl --user start spark-monitoring.service
systemctl --user stop spark-monitoring.service
systemctl --user reload spark-monitoring.service
systemctl --user restart spark-monitoring.service
```

`status` verifies service ownership, mounts, volume identity, the exact Serve
route, Prometheus and Grafana health, dashboard provisioning, required gateway
and node-exporter targets, and correctly labeled telemetry for every active
worker reported by each configured gateway. Unloaded worker targets may be
down. Active-worker telemetry gets a bounded convergence window, and the check
fails if the active set changes during that window.

Reload renders and validates current site configuration, then recreates only
Prometheus and Grafana so both receive the current files and environment.
Stopping removes the monitoring containers without requiring `site.yml`; the
external volumes remain. A known-good Git commit plus the local `site.yml` is
the recovery unit: check it out and run
`./bin/monitoring install`. Do not recreate the external volumes during
recovery.

Credentials, tokens, concrete site topology, runtime state, logs, model data,
and generated configuration do not belong in Git.
