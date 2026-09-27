"""Conformance of the engine against frozen baselines (spec 30.1).

* Synthetic regression baseline (committed): seed_demo/baseline_demo.json.
* Approved private baseline (never committed): private_data/baseline/*.json,
  paired with the rule set named in each file. Missing private data is reported
  as a skip — and the release gate treats any skip as a failure.
"""
import hashlib
import json
from pathlib import Path

import pytest

from apps.content.loader import file_to_engine_config
from apps.engine.engine import evaluate

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_RULESET = ROOT / "seed_demo" / "ruleset_demo.json"
SYNTHETIC_BASELINE = ROOT / "seed_demo" / "baseline_demo.json"
PRIVATE_DIR = ROOT / "private_data" / "baseline"


def run_baseline(ruleset_path, baseline_path):
    raw = ruleset_path.read_bytes()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert baseline["format"] == "assureflow-baseline-v1"
    assert baseline["ruleset_sha256"] == hashlib.sha256(raw).hexdigest(), "baseline was frozen for different rules"
    cfg = file_to_engine_config(json.loads(raw.decode("utf-8")))
    mismatches = []
    for i, case in enumerate(baseline["cases"]):
        actual = evaluate(cfg, case["answers"]).as_dict()
        if actual != case["expected"]:
            mismatches.append((i, case["answers"]))
    return len(baseline["cases"]), mismatches


def test_synthetic_baseline_conformance():
    total, mismatches = run_baseline(SYNTHETIC_RULESET, SYNTHETIC_BASELINE)
    assert total >= 100
    assert mismatches == []


@pytest.mark.private_content
def test_private_baseline_conformance():
    files = sorted(PRIVATE_DIR.glob("*.json")) if PRIVATE_DIR.exists() else []
    if not files:
        pytest.skip("No approved private baseline in private_data/baseline/ (required for release conformance).")
    for baseline_path in files:
        meta = json.loads(baseline_path.read_text(encoding="utf-8"))
        ruleset_path = ROOT / "private_data" / "seed" / meta.get("ruleset_file", "ruleset.json")
        total, mismatches = run_baseline(ruleset_path, baseline_path)
        assert mismatches == [], f"{baseline_path.name}: {len(mismatches)} of {total} cases differ"
