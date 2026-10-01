#!/usr/bin/env python3
"""Benchmark sweep on the C++ cycle-level model (which is cycle-exact against
the RTL for every workload checked in tests/test_cosim_rtl.py).

All numbers are SIMULATED cycle counts of this microarchitecture. They are not
silicon measurements and carry no clock frequency, area or power information.

  python3 benchmarks/run_benchmarks.py [--rtl-check]

Writes results/benchmarks/benchmarks.json and results/benchmarks/benchmarks.md.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))
import workloads as W  # noqa: E402
cosim = W.cosim
OUT = ROOT / "results" / "benchmarks"
CFG_DIR = ROOT / "architecture" / "configuration"


def metrics(perf, cfg):
    n = dict(zip(cosim.PERF_NAMES, perf))
    cyc = max(n["cycles"], 1)
    simd = n["issued"] - n["ctrl_instrs"]
    return {
        **n,
        "ipc": round(n["issued"] / cyc, 3),
        "thread_ipc": round(n["thread_instrs"] / cyc, 3),
        "simd_efficiency": round(n["thread_instrs"] / max(simd * cfg["num_lanes"], 1), 3),
        "stall_frac": round(1 - n["issued"] / cyc, 3),
        "lanes_per_lsu_round": round(n["lsu_lane_accesses"] / max(n["lsu_rounds"], 1), 3),
    }


def run(inst, cfg):
    res = cosim.run_cpp(inst.job, cfg, trace=False, compare_regs=inst.func_regs)
    assert res["models_agree"], res["stderr"]
    errs = W.check_golden(inst, res["spm"])
    assert not errs, errs
    return metrics(res["perf"], cfg)


def derived_config(base: dict, **kw) -> dict:
    cfg = {k: v for k, v in base.items() if not k.startswith("_")} | kw
    cfg["name"] = base["name"] + "".join(f"_{k}{v}" for k, v in kw.items())
    f = Path(tempfile.mkstemp(suffix=".json", dir=cosim.BUILD)[1])
    f.write_text(json.dumps(cfg))
    cfg["_path"] = str(f)
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rtl-check", action="store_true", help="re-run the policy table's small kernels on RTL")
    a = ap.parse_args(argv)
    cosim.BUILD.mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    report = {"note": "Simulated cycle counts from the C++ cycle model (cycle-exact vs RTL in the regression). "
                      "Not hardware measurements.",
              "host": platform.platform(), "policy_sweep": [], "bank_sweep": [], "mul_latency_sweep": [],
              "warp_scaling": [], "rtl_checked": []}
    configs = {n: cosim.load_config(CFG_DIR / f"{n}.json") for n in ("tfx_base", "tfx_small", "tfx_onebank", "tfx_wide")}

    # 1) every workload x every policy x every shipped config
    for cname, cfg in configs.items():
        for wname, fn in W.WORKLOADS.items():
            for pol in cosim.POLICIES:
                m = run(fn(cfg, policy=pol), cfg)
                report["policy_sweep"].append({"config": cname, "workload": wname, "policy": pol, **m})

    # 2) bank-conflict stride sweep (base: 8 banks, 8 lanes)
    for cname in ("tfx_base", "tfx_onebank", "tfx_wide"):
        cfg = configs[cname]
        for s in (0, 1, 2, 3, 4, 8, 16):
            m = run(W.stride(cfg, s=s, repeats=16), cfg)
            report["bank_sweep"].append({"config": cname, "stride": s, **m})

    # 3) multiplier latency: serial chain vs ILP, 4 warps and 1 warp
    base = configs["tfx_base"]
    for lat in (2, 3, 4, 6, 8):
        cfg = derived_config(base, mul_latency=lat)
        for wname in ("dep_chain", "ilp"):
            for warps in (1, 4):
                inst = W.WORKLOADS[wname](cfg)
                inst.job.warps = warps
                if warps == 1:  # golden only covers launched threads
                    inst.expected = {k: v for k, v in inst.expected.items() if k < cfg["num_lanes"]}
                m = run(inst, cfg)
                report["mul_latency_sweep"].append({"mul_latency": lat, "workload": wname, "warps": warps, **m})

    # 4) latency hiding: same per-thread work, 1..8 resident warps (wide config, 16 lanes)
    wide = configs["tfx_wide"]
    for warps in range(1, 9):
        cfg = derived_config(wide, num_warps=warps)
        for wname in ("dep_chain", "matmul", "mandelbrot"):
            m = run(W.WORKLOADS[wname](cfg), cfg)
            report["warp_scaling"].append({"warps": warps, "workload": wname, **m})

    # 6) regression vs the milestone-2 baseline (same kernels, same configs)
    base_file = OUT / "milestone2_baseline.json"
    report["baseline_compare"] = None
    if base_file.exists():
        old = {(x["config"], x["workload"], x["policy"]): x["cycles"] for x in json.loads(base_file.read_text())["policy_sweep"]}
        new = {(x["config"], x["workload"], x["policy"]): x["cycles"] for x in report["policy_sweep"]}
        common = sorted(set(old) & set(new))
        changed = [(k, old[k], new[k]) for k in common if old[k] != new[k]]
        report["baseline_compare"] = {"runs_compared": len(common), "changed": changed}

    # 7) simulation-only L1 data-cache model: one-parameter-at-a-time sweeps around a default
    L1D = dict(l1_enable=1, l1_size_bytes=1024, l1_line_bytes=32, l1_assoc=2, l1_policy="lru",
               l1_hit_latency=1, l1_miss_latency=20)
    sweeps = [("default", {})] + [(f"size={v}", {"l1_size_bytes": v}) for v in (256, 4096)] \
        + [(f"assoc={v}", {"l1_assoc": v}) for v in (1, 4)] + [(f"line={v}", {"l1_line_bytes": v}) for v in (16, 64)] \
        + [(f"policy={v}", {"l1_policy": v}) for v in ("fifo", "random")] \
        + [(f"miss_lat={v}", {"l1_miss_latency": v}) for v in (10, 40)]
    report["l1_sweep"] = []
    l1_kernels = ("vector_add", "matmul", "blur", "reduction", "histogram", "stride1", "stride_bank", "raster")
    for label, kw in sweeps:
        cfg = derived_config(base, **(L1D | kw))
        cfg["name"] = "tfx_base_l1_" + label
        for wname in l1_kernels:
            inst = W.WORKLOADS[wname](cfg)
            res = cosim.run_cpp(inst.job, cfg, trace=False, compare_regs=inst.func_regs)
            assert res["models_agree"] and not W.check_golden(inst, res["spm"]), (label, wname)
            l1 = res["l1"]
            loads = l1["load_hits"] + l1["load_misses"]
            report["l1_sweep"].append({"variant": label, "workload": wname, "cycles": res["perf"][0],
                                       "ipc": round(res["perf"][1] / res["perf"][0], 3),
                                       "load_hit_rate": round(l1["load_hits"] / loads, 3) if loads else None, **l1})

    if a.rtl_check:
        cfg = configs["tfx_base"]
        for wname in ("vector_add", "dep_chain", "reduction", "histogram", "divergence"):
            for pol in cosim.POLICIES:
                inst = W.WORKLOADS[wname](cfg, policy=pol)
                rep = cosim.cosim(inst.job, cfg, seed=4, func_regs=inst.func_regs)
                report["rtl_checked"].append({"workload": wname, "policy": pol, "cycles": rep["cpp"]["perf"][0],
                                              "rtl_identical": rep["ok"]})
    report["runtime_s"] = round(time.time() - t0, 1)
    (OUT / "benchmarks.json").write_text(json.dumps(report, indent=1))
    (OUT / "benchmarks.md").write_text(markdown(report))
    print(markdown(report))
    return 0


def markdown(r) -> str:
    L = ["# CharanGPU TFX-X benchmark results (simulated)", "",
         "> " + r["note"] + " Regenerate with `python3 benchmarks/run_benchmarks.py`.", ""]
    L += ["## 1. Scheduler policies (tfx_base: 4 warps x 8 lanes, 8 banks, MUL latency 3)", "",
          "Cycles per policy; best per row in **bold**. IPC = warp instructions issued per cycle (max 1.0).", "",
          "| workload | lrr | gto | prio | memfirst | IPC (lrr) | SIMD eff. | stall: scoreboard / struct / barrier / other |",
          "|---|---:|---:|---:|---:|---:|---:|---|"]
    rows = [x for x in r["policy_sweep"] if x["config"] == "tfx_base"]
    for w in dict.fromkeys(x["workload"] for x in rows):
        by = {x["policy"]: x for x in rows if x["workload"] == w}
        best = min(v["cycles"] for v in by.values())
        cells = [f"**{by[p]['cycles']}**" if by[p]["cycles"] == best else str(by[p]["cycles"]) for p in cosim.POLICIES]
        l = by["lrr"]
        st = f"{l['stall_scoreboard']} / {l['stall_structural']} / {l['stall_barrier']} / {l['stall_other']}"
        L.append(f"| {w} | " + " | ".join(cells) + f" | {l['ipc']} | {l['simd_efficiency']} | {st} |")
    L += ["", "## 2. Cycles by configuration (LRR)", "", "| workload | tfx_small (2x4, 2 banks) | tfx_base (4x8, 8) | "
          "tfx_onebank (4x8, 1) | tfx_wide (8x16, 16) |", "|---|---:|---:|---:|---:|"]
    lrr = [x for x in r["policy_sweep"] if x["policy"] == "lrr"]
    for w in dict.fromkeys(x["workload"] for x in lrr):
        c = {x["config"]: x for x in lrr if x["workload"] == w}
        L.append(f"| {w} | " + " | ".join(f"{c[k]['cycles']} (IPC {c[k]['ipc']})"
                                          for k in ("tfx_small", "tfx_base", "tfx_onebank", "tfx_wide")) + " |")
    L += ["", "Note: input sizes scale with the configuration (see benchmarks/workloads.py), so compare cycles across "
          "columns only for workloads with fixed work per thread.", "",
          "## 3. Scratchpad bank conflicts (stride microbenchmark, 16 loads per lane)", "",
          "| stride (words) | base: LSU rounds | base: lanes/round | onebank: rounds | wide: rounds | wide: lanes/round |",
          "|---:|---:|---:|---:|---:|---:|"]
    for s in dict.fromkeys(x["stride"] for x in r["bank_sweep"]):
        c = {x["config"]: x for x in r["bank_sweep"] if x["stride"] == s}
        L.append(f"| {s} | {c['tfx_base']['lsu_rounds']} | {c['tfx_base']['lanes_per_lsu_round']} | "
                 f"{c['tfx_onebank']['lsu_rounds']} | {c['tfx_wide']['lsu_rounds']} | {c['tfx_wide']['lanes_per_lsu_round']} |")
    L += ["", "## 4. Multiplier latency: dependent chain vs independent chains", "",
          "| MUL latency | dep_chain 1 warp | dep_chain 4 warps | ilp 1 warp | ilp 4 warps |", "|---:|---:|---:|---:|---:|"]
    for lat in dict.fromkeys(x["mul_latency"] for x in r["mul_latency_sweep"]):
        c = {(x["workload"], x["warps"]): x for x in r["mul_latency_sweep"] if x["mul_latency"] == lat}
        L.append(f"| {lat} | " + " | ".join(f"{c[k]['cycles']} (IPC {c[k]['ipc']})" for k in
                                             (("dep_chain", 1), ("dep_chain", 4), ("ilp", 1), ("ilp", 4))) + " |")
    L += ["", "## 5. Latency hiding: resident warps (16 lanes, 16 banks)", "",
          "Work per thread is fixed for dep_chain; matmul/mandelbrot have a fixed total problem size.", "",
          "| warps | dep_chain IPC | matmul cycles (IPC) | mandelbrot cycles (IPC, SIMD eff.) |", "|---:|---:|---:|---:|"]
    for w in dict.fromkeys(x["warps"] for x in r["warp_scaling"]):
        c = {x["workload"]: x for x in r["warp_scaling"] if x["warps"] == w}
        L.append(f"| {w} | {c['dep_chain']['ipc']} | {c['matmul']['cycles']} ({c['matmul']['ipc']}) | "
                 f"{c['mandelbrot']['cycles']} ({c['mandelbrot']['ipc']}, {c['mandelbrot']['simd_efficiency']}) |")
    bc = r.get("baseline_compare")
    if bc:
        L += ["", "## 6. Regression vs milestone 2 (pipelined multiplier + structured-branch fields)", "",
              f"{bc['runs_compared']} benchmark runs (13 workloads x 4 policies x 4 configs) compared with "
              f"`milestone2_baseline.json`: **{len(bc['changed'])} changed cycle counts**." +
              (" The new multiplier keeps the issue-to-writeback latency, so cycle behaviour is unchanged by design."
               if not bc["changed"] else " Changed: " + str(bc["changed"][:10]))]
    if r.get("l1_sweep"):
        L += ["", "## 7. L1 data-cache model (SIMULATION ONLY, no RTL) on tfx_base", "",
              "Default: 1 KiB, 32 B lines, 2-way, LRU, hit 1 / miss 20 cycles, write-through no-write-allocate, "
              "blocking. Each row changes one parameter. Cells: cycles (load hit rate). The scratchpad column "
              "is the RTL-equivalent design (1-cycle banked SRAM) for reference; it is a different memory "
              "system, not a 'no-cache' version of the same one.", ""]
        ks = list(dict.fromkeys(x["workload"] for x in r["l1_sweep"]))
        L += ["| variant | " + " | ".join(ks) + " |", "|---|" + "---:|" * len(ks)]
        spm = {x["workload"]: x["cycles"] for x in r["policy_sweep"] if x["config"] == "tfx_base" and x["policy"] == "lrr"}
        L.append("| scratchpad (RTL design) | " + " | ".join(str(spm[k]) for k in ks) + " |")
        for v in dict.fromkeys(x["variant"] for x in r["l1_sweep"]):
            c = {x["workload"]: x for x in r["l1_sweep"] if x["variant"] == v}
            L.append(f"| {v} | " + " | ".join(
                f"{c[k]['cycles']} ({c[k]['load_hit_rate'] if c[k]['load_hit_rate'] is not None else '-'})" for k in ks) + " |")
    if r["rtl_checked"]:
        ok = sum(x["rtl_identical"] for x in r["rtl_checked"])
        L += ["", f"## RTL cross-check: {ok}/{len(r['rtl_checked'])} benchmark runs re-simulated on RTL were "
                  "cycle- and state-identical."]
    L += ["", f"_Sweep runtime {r['runtime_s']} s on {r['host']}._", ""]
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
