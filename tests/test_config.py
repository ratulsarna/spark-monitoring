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
        self.assertEqual({labels["engine"] for labels in worker_labels}, {"vllm", "sglang", "llamacpp"})
        vllm = next(job for job in rendered["scrape_configs"] if job["job_name"] == "vllm-node-a")
        self.assertEqual(len(vllm["metric_relabel_configs"]), 2)
        self.assertEqual(vllm["metric_relabel_configs"][1]["replacement"], "demo-vllm-model-alt")

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
