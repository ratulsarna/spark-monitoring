#!/usr/bin/env python3
"""Strict site configuration and Prometheus rendering for spark-monitoring."""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import tempfile
from collections import defaultdict
from urllib.parse import urlsplit

import yaml


ENGINES = {
    "vllm": "vllm:num_requests_running",
    "sglang": "sglang:num_running_reqs",
    "llamacpp": "llamacpp:requests_processing",
    "tensorfold": "tensorfold:requests_running",
}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,191}")
JOB = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
VOLUME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}")


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _mapping(value, name, required, optional=()):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    keys = set(value)
    required = set(required)
    optional = set(optional)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise ValueError(f"{name} is missing keys: {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{name} has unknown keys: {', '.join(sorted(unknown))}")
    return value


def _list(value, name, *, nonempty=True):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{name} must be a{' non-empty' if nonempty else ''} list")
    return value


def _string(value, name, pattern=None):
    if not isinstance(value, str) or not value or (pattern and not pattern.fullmatch(value)):
        raise ValueError(f"{name} is invalid")
    return value


def _port(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError(f"{name} must be an integer from 1 through 65535")
    return value


def _endpoint(value, name, *, base_url=False):
    value = _string(value, name)
    parsed = urlsplit(value if base_url else f"http://{value}")
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{name} has an unsupported scheme")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{name} has an invalid authority")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{name} has an invalid port") from error
    if port is None:
        raise ValueError(f"{name} must include an explicit port")
    _port(port, name)
    if parsed.query or parsed.fragment or (base_url and parsed.path not in {"", "/"}):
        raise ValueError(f"{name} must not include a path, query, or fragment")
    if not base_url and (parsed.path or parsed.query or parsed.fragment):
        raise ValueError(f"{name} must be a host and explicit port")
    return value.rstrip("/") if base_url else value


def _scrape(value, name, *, extra=False):
    required = {"job", "target"} | ({"service"} if extra else set())
    raw = _mapping(value, name, required, {"scheme", "metrics_path"})
    scheme = raw.get("scheme", "http")
    if scheme not in {"http", "https"}:
        raise ValueError(f"{name}.scheme must be http or https")
    path = raw.get("metrics_path", "/metrics")
    if not isinstance(path, str) or not re.fullmatch(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*", path):
        raise ValueError(f"{name}.metrics_path is invalid")
    result = {
        "job": _string(raw["job"], f"{name}.job", JOB),
        "target": _endpoint(raw["target"], f"{name}.target"),
        "scheme": scheme,
        "metrics_path": path,
    }
    if extra:
        if raw["service"] != "model-throughput":
            raise ValueError(f"{name}.service is unsupported")
        result["service"] = raw["service"]
    return result


def load_site(path):
    with open(path, encoding="utf-8") as stream:
        site = yaml.load(stream, Loader=UniqueKeyLoader)
    site = _mapping(
        site,
        "site",
        {"storage", "grafana", "prometheus", "serving_nodes", "model_telemetry"},
    )

    storage = _mapping(
        site["storage"], "storage", {"prometheus_volume", "grafana_volume"}
    )
    prom_volume = _string(storage["prometheus_volume"], "storage.prometheus_volume", VOLUME)
    graf_volume = _string(storage["grafana_volume"], "storage.grafana_volume", VOLUME)
    if prom_volume == graf_volume:
        raise ValueError("Prometheus and Grafana volumes must be different")

    grafana = _mapping(site["grafana"], "grafana", {"serve_host", "serve_port"})
    serve_host = _string(grafana["serve_host"], "grafana.serve_host", HOST)
    serve_port = _port(grafana["serve_port"], "grafana.serve_port")

    prometheus = _mapping(
        site["prometheus"], "prometheus", {"cluster", "node_exporter_node"}
    )
    cluster = _string(prometheus["cluster"], "prometheus.cluster", IDENTIFIER)
    node_exporter_node = _string(
        prometheus["node_exporter_node"], "prometheus.node_exporter_node", IDENTIFIER
    )

    nodes = []
    node_names = set()
    gateway_jobs = set()
    for index, raw in enumerate(_list(site["serving_nodes"], "serving_nodes")):
        name = f"serving_nodes[{index}]"
        raw = _mapping(raw, name, {"name", "running_url", "gateway_scrape"})
        node = _string(raw["name"], f"{name}.name", IDENTIFIER)
        if node in node_names:
            raise ValueError(f"duplicate serving node: {node}")
        node_names.add(node)
        scrape = _scrape(raw["gateway_scrape"], f"{name}.gateway_scrape")
        if scrape["job"] in gateway_jobs:
            raise ValueError(f"duplicate gateway job: {scrape['job']}")
        gateway_jobs.add(scrape["job"])
        nodes.append(
            {
                "name": node,
                "running_url": _endpoint(raw["running_url"], f"{name}.running_url", base_url=True),
                "gateway_scrape": scrape,
            }
        )

    telemetry = []
    running_keys = set()
    dashboard_keys = set()
    primary_groups = {}
    primary_targets = set()
    all_jobs = set(gateway_jobs) | {"node"}
    extra_jobs = set()
    for index, raw in enumerate(_list(site["model_telemetry"], "model_telemetry")):
        name = f"model_telemetry[{index}]"
        raw = _mapping(
            raw,
            name,
            {"node", "identities", "engine", "proxy_port", "activity_metric", "scrape"},
            {"model_label_source", "extra_scrapes"},
        )
        node = _string(raw["node"], f"{name}.node", IDENTIFIER)
        if node not in node_names:
            raise ValueError(f"{name}.node does not name a serving node")
        engine = raw["engine"]
        if engine not in ENGINES:
            raise ValueError(f"{name}.engine is unsupported")
        metric = raw["activity_metric"]
        if metric != ENGINES[engine]:
            raise ValueError(f"{name}.activity_metric is unsupported for {engine}")
        identities = []
        for identity_index, identity_raw in enumerate(_list(raw["identities"], f"{name}.identities")):
            identity_name = f"{name}.identities[{identity_index}]"
            identity_raw = _mapping(identity_raw, identity_name, {"running", "model"}, {"metric"})
            running = _string(identity_raw["running"], f"{identity_name}.running", IDENTIFIER)
            model = _string(identity_raw["model"], f"{identity_name}.model", IDENTIFIER)
            metric_model = _string(identity_raw.get("metric", running), f"{identity_name}.metric", IDENTIFIER)
            if (node, running) in running_keys:
                raise ValueError(f"duplicate active identity: node={node} model={running}")
            if (node, model) in dashboard_keys:
                raise ValueError(f"duplicate dashboard identity: node={node} model={model}")
            running_keys.add((node, running))
            dashboard_keys.add((node, model))
            identities.append({"running": running, "model": model, "metric": metric_model})
        label_source = raw.get("model_label_source")
        if label_source is not None:
            label_source = _string(label_source, f"{name}.model_label_source", LABEL)
        elif len(identities) != 1:
            raise ValueError(f"{name} needs model_label_source for multiple identities")
        scrape = _scrape(raw["scrape"], f"{name}.scrape")
        group_shape = (scrape["scheme"], scrape["metrics_path"])
        previous_shape = primary_groups.setdefault(scrape["job"], group_shape)
        if previous_shape != group_shape:
            raise ValueError(f"shared job has inconsistent scheme or metrics path: {scrape['job']}")
        key = (scrape["job"], scrape["target"])
        if key in primary_targets:
            raise ValueError(f"duplicate model scrape target: job={key[0]} target={key[1]}")
        primary_targets.add(key)
        extras = []
        for extra_index, extra_raw in enumerate(raw.get("extra_scrapes", [])):
            extra = _scrape(extra_raw, f"{name}.extra_scrapes[{extra_index}]", extra=True)
            if len(identities) != 1:
                raise ValueError(f"{name}.extra_scrapes requires exactly one identity")
            if extra["job"] in extra_jobs:
                raise ValueError(f"duplicate extra scrape job: {extra['job']}")
            extra_jobs.add(extra["job"])
            extras.append(extra)
        telemetry.append(
            {
                "node": node,
                "identities": identities,
                "engine": engine,
                "proxy_port": _port(raw["proxy_port"], f"{name}.proxy_port"),
                "activity_metric": metric,
                "scrape": scrape,
                "model_label_source": label_source,
                "extra_scrapes": extras,
            }
        )

    model_jobs = set(primary_groups)
    overlaps = all_jobs & model_jobs | all_jobs & extra_jobs | model_jobs & extra_jobs
    if overlaps:
        raise ValueError(f"job names overlap across job types: {', '.join(sorted(overlaps))}")

    return {
        "storage": {"prometheus_volume": prom_volume, "grafana_volume": graf_volume},
        "grafana": {"serve_host": serve_host, "serve_port": serve_port},
        "prometheus": {"cluster": cluster, "node_exporter_node": node_exporter_node},
        "serving_nodes": nodes,
        "model_telemetry": telemetry,
    }


def _job(name, scheme, path, static_configs, relabels=None, honor_labels=False):
    result = {"job_name": name, "metrics_path": path, "static_configs": static_configs}
    if scheme != "http":
        result["scheme"] = scheme
    if relabels:
        result["metric_relabel_configs"] = relabels
    if honor_labels:
        result["honor_labels"] = True
    return result


def _prom_regex_escape(value):
    return "".join("\\" + character if character in r"\.^$|?*+()[]{}" else character for character in value)


def render_prometheus(site):
    jobs = []
    for node in site["serving_nodes"]:
        scrape = node["gateway_scrape"]
        jobs.append(
            _job(
                scrape["job"], scrape["scheme"], scrape["metrics_path"],
                [{"targets": [scrape["target"]], "labels": {"service": "model-gateway", "node": node["name"]}}],
            )
        )

    grouped = defaultdict(list)
    for entry in site["model_telemetry"]:
        grouped[entry["scrape"]["job"]].append(entry)
    for job_name, entries in grouped.items():
        scrape = entries[0]["scrape"]
        static_configs = []
        relabels = []
        for entry in entries:
            labels = {"service": "model-worker", "engine": entry["engine"], "node": entry["node"]}
            if entry["model_label_source"] is None:
                labels["model"] = entry["identities"][0]["model"]
            else:
                for identity in entry["identities"]:
                    relabels.append(
                        {
                            "source_labels": [entry["model_label_source"]],
                            "regex": f"^{_prom_regex_escape(identity['metric'])}$",
                            "target_label": "model",
                            "replacement": identity["model"],
                        }
                    )
            static_configs.append({"targets": [entry["scrape"]["target"]], "labels": labels})
        jobs.append(_job(job_name, scrape["scheme"], scrape["metrics_path"], static_configs, relabels))

    for entry in site["model_telemetry"]:
        for scrape in entry["extra_scrapes"]:
            labels = {
                "service": scrape["service"],
                "model": entry["identities"][0]["model"],
                "engine": entry["engine"],
                "node": entry["node"],
            }
            jobs.append(
                _job(scrape["job"], scrape["scheme"], scrape["metrics_path"], [{"targets": [scrape["target"]], "labels": labels}])
            )

    jobs.append(
        _job(
            "node", "http", "/metrics",
            [{"targets": ["127.0.0.1:9100"], "labels": {"service": "host", "node": site["prometheus"]["node_exporter_node"]}}],
            honor_labels=True,
        )
    )
    document = {
        "global": {
            "scrape_interval": "5s",
            "scrape_timeout": "4s",
            "evaluation_interval": "5s",
            "external_labels": {"cluster": site["prometheus"]["cluster"]},
        },
        "scrape_configs": jobs,
    }
    return yaml.safe_dump(document, sort_keys=False, width=1000)


def _render_unit(path, root):
    text = pathlib.Path(path).read_text(encoding="utf-8")
    working_root = "".join(
        "%%" if character == "%" else character
        if character.isascii() and (character.isalnum() or character in "/._-")
        else "".join(f"\\x{byte:02x}" for byte in character.encode("utf-8"))
        for character in str(root)
    )
    escaped_root = str(root).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    replacements = {"CHECKOUT_DIRECTORY": working_root, "CHECKOUT_ROOT": escaped_root}
    for key, value in replacements.items():
        token = "{{" + key + "}}"
        if token not in text:
            raise ValueError(f"missing unit template token: {key}")
        text = text.replace(token, value)
    if "{{" in text or "}}" in text:
        raise ValueError("unresolved unit template token")
    return text


def _atomic_write(path, content, mode):
    path = pathlib.Path(path)
    if path.is_file() and not path.is_symlink() and path.read_text(encoding="utf-8") == content:
        os.chmod(path, mode)
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        stream.write(content)
        temporary = pathlib.Path(stream.name)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def runtime(action, root, site_path, prom_output, unit_template, unit_output):
    site = load_site(site_path)
    prometheus = render_prometheus(site)
    unit = _render_unit(unit_template, pathlib.Path(root))
    outputs = ((pathlib.Path(prom_output), prometheus, 0o644), (pathlib.Path(unit_output), unit, 0o600))
    if action == "check":
        if any(not path.is_file() or path.is_symlink() or path.read_text(encoding="utf-8") != content for path, content, _ in outputs):
            raise ValueError("rendered runtime files are absent or stale")
    elif action == "write":
        for path, content, mode in outputs:
            _atomic_write(path, content, mode)
    else:
        raise ValueError("invalid render action")


def main(argv):
    if len(argv) == 3 and argv[1] == "validate":
        print(json.dumps(load_site(argv[2]), separators=(",", ":")))
        return
    if len(argv) == 8 and argv[1] == "runtime":
        runtime(*argv[2:])
        return
    raise SystemExit("usage: monitoring_config.py validate SITE | runtime ACTION ROOT SITE PROM UNIT_TEMPLATE UNIT_OUTPUT")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise SystemExit(str(error)) from error
