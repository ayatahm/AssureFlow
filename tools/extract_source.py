#!/usr/bin/env python
"""Build an AssureFlow rule-set JSON from CSV exports of an approved source workbook.

Run on an approved machine only; write output under private_data/ (gitignored):

    python tools/extract_source.py private_data/source/csv -o private_data/seed/ruleset.json \
        --version NBB-2026.1 --label "Approved rule set 2026.1"

Expected files in the input directory (UTF-8 CSV with a header row):
  triage.csv          code,kind,options,text,help_text          (kind: LEVEL|YES_NO; options ';'-separated)
  gates.csv           code,question,skip_value,note             (skip when <question> == <skip_value>)
  controls.csv        code,domain,title,description,gates,PUBLIC,SENSITIVE,CONFIDENTIAL,SECRET,questions
  questions.csv       code,section,text,always_ask              (always_ask: yes/no)
  framework_refs.csv  control,framework,reference,description,edition   (optional)
  guidance.csv        level,decision,text                        (optional)
  classification.json {"default": ..., "rules": [...]}          (condition syntax: see apps/engine/engine.py)
  routing.json        [{"code", "label", "when"}]               (optional)

This tool does not interpret spreadsheet formulas; the owner of the source
material is responsible for exporting evaluated values faithfully. Record the
source file hash alongside the output.
"""
import argparse
import csv
import json
from pathlib import Path


def rows(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def split(value):
    return [v.strip() for v in (value or "").split(";") if v.strip()]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input_dir")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--synthetic", action="store_true", help="Mark output as synthetic (demo content only).")
    a = p.parse_args()
    src = Path(a.input_dir)
    refs = {}
    for r in rows(src / "framework_refs.csv"):
        refs.setdefault(r["control"], []).append({
            "framework": r["framework"], "reference": r["reference"], "description": r.get("description", ""),
            "edition": r.get("edition") or "Not specified",
        })
    out = {
        "format": "assureflow-ruleset-v1",
        "synthetic": bool(a.synthetic),
        "version": a.version,
        "label": a.label,
        "triage_questions": [
            {"code": r["code"], "kind": r["kind"], "options": split(r.get("options")), "text": r["text"],
             "help_text": r.get("help_text", "")} for r in rows(src / "triage.csv")
        ],
        "classification": json.loads((src / "classification.json").read_text(encoding="utf-8")),
        "routing": json.loads((src / "routing.json").read_text(encoding="utf-8")) if (src / "routing.json").exists() else [],
        "gates": [
            {"code": r["code"], "question": r["question"], "skip_when": {"q": r["question"], "eq": r["skip_value"]},
             "note": r.get("note", "")} for r in rows(src / "gates.csv")
        ],
        "controls": [
            {"code": r["code"], "domain": r.get("domain", ""), "title": r["title"],
             "description": r.get("description", ""), "gates": split(r.get("gates")),
             "levels": {k: r[k] for k in ("PUBLIC", "SENSITIVE", "CONFIDENTIAL", "SECRET")},
             "questions": split(r.get("questions")), "framework_refs": refs.get(r["code"], [])}
            for r in rows(src / "controls.csv")
        ],
        "assessment_questions": [
            {"code": r["code"], "section": r["section"], "text": r["text"],
             "always_ask": (r.get("always_ask") or "").strip().lower() in {"yes", "true", "1"}}
            for r in rows(src / "questions.csv")
        ],
        "compliance_guidance": [
            {"level": r["level"], "decision": r["decision"], "text": r["text"]} for r in rows(src / "guidance.csv")
        ],
    }
    Path(a.output).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {a.output}: {len(out['controls'])} controls, {len(out['assessment_questions'])} questions")


if __name__ == "__main__":
    main()
