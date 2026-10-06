#!/usr/bin/env python3
import copy
import pathlib
import tempfile
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "lib"))
import monitoring_config


class ConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.example = yaml.safe_load((ROOT / "site.example.yml").read_text(encoding="utf-8"))

    def load(self, value):
        with tempfile.NamedTemporaryFile("w", suffix=".yml", encoding="utf-8") as stream:
            yaml.safe_dump(value, stream, sort_keys=False)
            stream.flush()
            return monitoring_config.load_site(stream.name)

    def test_example_renders_all_supported_engines(self):
        site = self.load(self.example)
        rendered = yaml.safe_load(monitoring_config.render_prometheus(site))
        worker_labels = [
            static["labels"]
            for job in rendered["scrape_configs"]
            for static in job["static_configs"]
            if static["labels"].get("service") == "model-worker"
        ]
        self.assertEqual({labels["engine"] for labels in worker_labels}, {"vllm", "sglang", "llamacpp", "tensorfold"})
        vllm = next(job for job in rendered["scrape_configs"] if job["job_name"] == "vllm-node-a")
        self.assertEqual(len(vllm["metric_relabel_configs"]), 2)
        self.assertEqual(vllm["metric_relabel_configs"][1]["replacement"], "demo-vllm-model-alt")

    def test_tensorfold_engine_requires_exact_activity_metric(self):
        self.assertEqual(monitoring_config.ENGINES["tensorfold"], "tensorfold:requests_running")
        fixture = copy.deepcopy(self.example)
        row = next(row for row in fixture["model_telemetry"] if row["engine"] == "tensorfold")
        row["activity_metric"] = "tensorfold:requests_waiting"
        with self.assertRaisesRegex(ValueError, "unsupported for tensorfold"):
            self.load(fixture)

    def test_tensorfold_health_engine_requires_its_own_activity_metric(self):
        self.assertEqual(monitoring_config.ENGINES["tensorfold-health"], "tensorfold_requests_inflight")
        fixture = copy.deepcopy(self.example)
        row = next(row for row in fixture["model_telemetry"] if row["engine"] == "tensorfold")
        row["engine"] = "tensorfold-health"
        with self.assertRaisesRegex(ValueError, "unsupported for tensorfold-health"):
            self.load(fixture)
        row["activity_metric"] = "tensorfold_requests_inflight"
        site = self.load(fixture)
        loaded = next(row for row in site["model_telemetry"] if row["engine"] == "tensorfold-health")
        self.assertEqual(loaded["activity_metric"], "tensorfold_requests_inflight")

    def test_tensorfold_row_renders_worker_labels_and_target(self):
        site = self.load(self.example)
        row = next(row for row in site["model_telemetry"] if row["engine"] == "tensorfold")
        self.assertEqual(row["activity_metric"], "tensorfold:requests_running")
        self.assertEqual(row["identities"][0]["model"], "demo-tensorfold-model")
        rendered = yaml.safe_load(monitoring_config.render_prometheus(site))
        job = next(job for job in rendered["scrape_configs"] if job["job_name"] == "tensorfold-node-a")
        self.assertEqual(job["static_configs"][0]["targets"], ["127.0.0.1:32103"])
        labels = job["static_configs"][0]["labels"]
        self.assertEqual(labels["engine"], "tensorfold")
        self.assertEqual(labels["model"], "demo-tensorfold-model")
        self.assertEqual(labels["service"], "model-worker")
        self.assertEqual(job.get("metric_relabel_configs"), None)

    def test_host_scrape_preserves_textfile_node_labels(self):
        site = self.load(self.example)
        rendered = yaml.safe_load(monitoring_config.render_prometheus(site))
        job = next(job for job in rendered["scrape_configs"] if job["job_name"] == "node")
        self.assertTrue(job["honor_labels"])
        self.assertEqual(job["static_configs"][0]["targets"], ["127.0.0.1:9100"])

    def test_duplicate_active_identity_is_rejected(self):
        fixture = copy.deepcopy(self.example)
        fixture["model_telemetry"][1]["identities"][0]["running"] = "demo-vllm-model"
        with self.assertRaisesRegex(ValueError, "duplicate active identity"):
            self.load(fixture)

    def test_wrong_metric_for_engine_is_rejected(self):
        fixture = copy.deepcopy(self.example)
        fixture["model_telemetry"][0]["activity_metric"] = "sglang:num_running_reqs"
        with self.assertRaisesRegex(ValueError, "unsupported for vllm"):
            self.load(fixture)

if __name__ == "__main__":
    unittest.main()
