#!/usr/bin/env python3
"""Validate cluster configuration files against
architecture/schemas/cluster_config.schema.json (dependency-free subset of JSON
Schema: type/required/additionalProperties/minimum/maximum/enum/pattern) plus
the custom ``powerOfTwo`` keyword and cross-field RTL constraints."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "architecture/schemas/cluster_config.schema.json").read_text())


def validate(cfg: dict) -> list[str]:
    errs = []
    props = SCHEMA["properties"]
    for k in SCHEMA["required"]:
        if k not in cfg:
            errs.append(f"missing required field '{k}'")
    for k, v in cfg.items():
        if k not in props:
            errs.append(f"unknown field '{k}'")
            continue
        p = props[k]
        if p["type"] == "integer":
            if not isinstance(v, int) or isinstance(v, bool):
                errs.append(f"{k}: expected integer")
                continue
            if v < p.get("minimum", v) or v > p.get("maximum", v):
                errs.append(f"{k}={v} outside [{p.get('minimum')}, {p.get('maximum')}]")
            if p.get("powerOfTwo") and (v & (v - 1)) != 0:
                errs.append(f"{k}={v} must be a power of two")
        elif p["type"] == "string":
            if not isinstance(v, str):
                errs.append(f"{k}: expected string")
                continue
            if "enum" in p and v not in p["enum"]:
                errs.append(f"{k}='{v}' not in {p['enum']}")
            if "pattern" in p and not re.match(p["pattern"], v):
                errs.append(f"{k}='{v}' does not match {p['pattern']}")
    if not errs and cfg["spm_words"] <= cfg["spm_banks"]:
        errs.append("spm_words must exceed spm_banks (RTL needs at least 2 rows per bank)")
    if not errs and cfg.get("l1_enable", 0):
        line, ways, size = cfg.get("l1_line_bytes", 32), cfg.get("l1_assoc", 2), cfg.get("l1_size_bytes", 1024)
        sets = size // (line * ways) if line * ways else 0
        if sets < 1 or size % (line * ways) or sets & (sets - 1):
            errs.append("l1_size_bytes / (l1_line_bytes * l1_assoc) must be a power-of-two number of sets")
        if cfg.get("l1_miss_latency", 20) < cfg.get("l1_hit_latency", 1):
            errs.append("l1_miss_latency must be >= l1_hit_latency")
    return errs


def main(argv=None) -> int:
    files = (argv if argv is not None else sys.argv[1:]) or sorted(str(p) for p in (ROOT / "architecture/configuration").glob("*.json"))
    bad = 0
    for f in files:
        e = validate(json.loads(Path(f).read_text()))
        print(f"{'OK  ' if not e else 'FAIL'} {f}" + ("" if not e else ": " + "; ".join(e)))
        bad += bool(e)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
