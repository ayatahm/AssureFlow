#!/usr/bin/env python
"""Questionnaire reduction metrics for a rule set (evaluation chapter material).

    python tools/reduction_report.py seed_demo/ruleset_demo.json [--baseline FILE]

Reports, across every triage combination (or the cases in a baseline file),
how many requirements are applicable and how many questions are asked versus
the full question bank.
"""
import argparse
import json
import statistics
from collections import Counter

import _bootstrap  # noqa: F401
from generate_baseline import all_answer_sets, load_config

from apps.engine.engine import evaluate  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ruleset")
    parser.add_argument("--baseline")
    args = parser.parse_args()
    data, cfg = load_config(args.ruleset)
    if args.baseline:
        answer_sets = [c["answers"] for c in json.load(open(args.baseline, encoding="utf-8"))["cases"]]
    else:
        answer_sets = list(all_answer_sets(cfg))
    total_q = len(cfg["question_order"])
    total_c = len(cfg["controls"])
    asked, applicable, classes = [], [], Counter()
    for a in answer_sets:
        r = evaluate(cfg, a)
        asked.append(len(r.questions))
        applicable.append(sum(1 for c in r.controls if c.keep))
        classes[r.classification] += 1
    print(f"# Reduction report — {data['version']}{' (SYNTHETIC)' if data.get('synthetic') else ''}")
    print(f"Scenarios evaluated: {len(answer_sets)}")
    print(f"Question bank: {total_q}; requirements: {total_c}")
    print(f"Questions asked: mean {statistics.mean(asked):.1f}, min {min(asked)}, max {max(asked)} "
          f"(mean reduction {100 * (1 - statistics.mean(asked) / total_q):.0f}%)")
    print(f"Applicable requirements: mean {statistics.mean(applicable):.1f}, min {min(applicable)}, max {max(applicable)}")
    print("Classification distribution: " + ", ".join(f"{k} {v}" for k, v in sorted(classes.items())))


if __name__ == "__main__":
    main()
