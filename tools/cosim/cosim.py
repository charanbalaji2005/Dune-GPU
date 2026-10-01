#!/usr/bin/env python3
"""CharanGPU TFX-X co-simulation harness.

Runs one kernel launch ("job") on
  * the C++ functional model   (simulator/src/tfx_sim.cpp, FunctionalModel)
  * the C++ cycle-level model  (TimingModel)
  * the RTL                    (sim/tb_charangpu_top.sv -> charangpu_tfx_top, Icarus Verilog)
and compares final scratchpad, registers, faults, all 16 performance counters
and the per-cycle issue/stall trace.

The RTL is driven exclusively through its 64-bit host command port (the same
path a real host would use): WRITE_IMEM, WRITE_SPM, SET_ARG, LAUNCH, then
READ_SPM / READ_PERF / READ_FAULT / READ_REG to pull state back out, with
random valid gaps and response backpressure applied by the testbench.

Usage (CLI):
  python3 tools/cosim/cosim.py --config architecture/configuration/tfx_base.json \
      --program examples/vector_add/vector_add.tfx --mem-file m.txt --arg 0=0 ...
Most users call this module from tests/ and benchmarks/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
sys.path.insert(0, str(ROOT / "compiler"))
import tfxasm  # noqa: E402

POLICIES = {"lrr": 0, "gto": 1, "prio": 2, "memfirst": 3}
PERF_NAMES = ["cycles", "issued", "thread_instrs", "alu_instrs", "mul_instrs", "mem_instrs", "ctrl_instrs",
              "stall_scoreboard", "stall_structural", "stall_barrier", "stall_other", "lsu_rounds",
              "lsu_lane_accesses", "divergent_ifs", "barrier_releases", "lsu_busy_cycles"]
FAULT_NAMES = {0: "none", 1: "ILLEGAL", 2: "MISALIGNED", 3: "OOB", 4: "PC_RANGE", 5: "STACK_OVF",
               6: "STACK_UNF", 7: "EXIT_DIVERGENT", 8: "BRANCH_DEPTH"}

RTL_SOURCES = [
    "rtl/core/tfx_alu.sv", "rtl/core/tfx_mul_pipe.sv", "rtl/core/tfx_decoder.sv", "rtl/scheduler/tfx_warp_scheduler.sv",
    "rtl/memory/tfx_scratchpad.sv", "rtl/memory/tfx_lsu.sv", "rtl/compute/tfx_simt_cluster.sv",
    "rtl/command_processor/tfx_command_processor.sv", "rtl/interconnect/tfx_fifo.sv",
    "rtl/interconnect/tfx_token_channel.sv", "rtl/top/charangpu_tfx_top.sv", "sim/tb_charangpu_top.sv",
]
CPP_SOURCES = ["simulator/src/tfx_sim.cpp", "simulator/include/tfx_config.hpp", "simulator/include/tfx_machine.hpp",
               "simulator/include/tfx_timing.hpp", "software/reference/tfx_isa.hpp", "software/reference/tfx_sched.hpp"]


# --------------------------------------------------------------------------- configuration
def load_config(path) -> dict:
    cfg = json.loads(Path(path).read_text())
    cfg["_path"] = str(Path(path).resolve())
    return cfg


def rtl_params(cfg: dict) -> dict:
    return {"NW": cfg["num_warps"], "NL": cfg["num_lanes"], "IMEM_DEPTH": cfg["imem_depth"],
            "SPM_WORDS": cfg["spm_words"], "SPM_BANKS": cfg["spm_banks"], "MUL_LAT": cfg["mul_latency"],
            "STACK_DEPTH": cfg["stack_depth"]}


# --------------------------------------------------------------------------- jobs
@dataclass
class Job:
    name: str
    words: list[int]
    mem: dict[int, int] = field(default_factory=dict)      # word address -> value (written explicitly)
    args: dict[int, int] = field(default_factory=dict)
    warps: int | None = None
    policy: str = "lrr"
    symbols: dict = field(default_factory=dict)


def job_from_source(name: str, source: str, **kw) -> Job:
    words, _listing, syms = tfxasm.assemble(source)
    return Job(name=name, words=words, symbols=syms, **kw)


def job_from_file(path, **kw) -> Job:
    p = Path(path)
    return job_from_source(p.stem, p.read_text(), **kw)


# --------------------------------------------------------------------------- builds
def _stale(target: Path, sources) -> bool:
    if not target.exists():
        return True
    t = target.stat().st_mtime
    return any((ROOT / s).stat().st_mtime > t for s in sources)


def build_cpp() -> Path:
    BUILD.mkdir(exist_ok=True)
    exe = BUILD / "tfx_sim"
    if _stale(exe, CPP_SOURCES):
        cxx = os.environ.get("CXX", "g++")
        subprocess.run([cxx, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Wpedantic", "-Werror",
                        "-Isoftware/reference", "-Isimulator/include", "simulator/src/tfx_sim.cpp", "-o", str(exe)],
                       cwd=ROOT, check=True)
    return exe


def rtl_compatible(cfg: dict) -> bool:
    """The L1 data-cache model exists only in the C++ cycle simulator."""
    return not int(cfg.get("l1_enable", 0))


def build_rtl(cfg: dict) -> Path:
    if not rtl_compatible(cfg):
        raise ValueError(f"config {cfg['name']} enables the C++-only L1 model; it has no RTL equivalent")
    BUILD.mkdir(exist_ok=True)
    p = rtl_params(cfg)
    tag = "_".join(f"{k}{v}" for k, v in p.items())
    exe = BUILD / f"tb_top_{hashlib.sha1(tag.encode()).hexdigest()[:10]}.vvp"
    if _stale(exe, RTL_SOURCES):
        cmd = ["iverilog", "-g2012", "-s", "tb_charangpu_top", "-o", str(exe)]
        cmd += [f"-Ptb_charangpu_top.{k}={v}" for k, v in p.items()]
        cmd += [str(ROOT / s) for s in RTL_SOURCES]
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        errs = [ln for ln in (r.stdout + r.stderr).splitlines()
                if "error:" in ln.lower() and "sorry" not in ln.lower()]
        if r.returncode != 0 or errs:
            raise RuntimeError("iverilog failed:\n" + r.stdout + r.stderr)
    return exe


# --------------------------------------------------------------------------- C++ model
def _write_inputs(job: Job, d: Path):
    (d / "prog.hex").write_text("".join(f"{w:08x}\n" for w in job.words))
    (d / "mem.txt").write_text("".join(f"{a:x} {v & 0xFFFFFFFF:08x}\n" for a, v in sorted(job.mem.items())))


def _parse_regs(text: str) -> dict:
    regs = {}
    for ln in text.splitlines():
        w, l, r, v = ln.split()
        regs[(int(w), int(l), int(r))] = int(v, 16)
    return regs


def run_cpp(job: Job, cfg: dict, mode="both", trace=True, compare_regs=False, max_cycles=5_000_000) -> dict:
    exe = build_cpp()
    with tempfile.TemporaryDirectory(dir=BUILD) as td:
        d = Path(td)
        _write_inputs(job, d)
        cmd = [str(exe), "--config", cfg["_path"], "--program", str(d / "prog.hex"), "--mem", str(d / "mem.txt"),
               "--mode", mode, "--policy", job.policy, "--dump-mem", str(d / "mem.out"),
               "--dump-regs", str(d / "regs.out"), "--stats", str(d / "stats.json"),
               "--max-cycles", str(max_cycles)]
        if job.warps is not None:
            cmd += ["--warps", str(job.warps)]
        for i, v in sorted(job.args.items()):
            cmd += ["--arg", f"{i}=0x{v & 0xFFFFFFFF:x}"]
        if trace and mode != "functional":
            cmd += ["--trace", str(d / "trace.txt")]
        if compare_regs:
            cmd += ["--compare-regs"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        res = {"exit": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
        if r.returncode not in (0, 3):
            raise RuntimeError(f"tfx_sim failed ({r.returncode}): {r.stderr}")
        stats = json.loads((d / "stats.json").read_text())
        res["stats"] = stats
        res["models_agree"] = stats["models_agree"]
        res["spm"] = [int(x, 16) for x in (d / "mem.out").read_text().split()]
        res["regs"] = _parse_regs((d / "regs.out").read_text())
        res["faults"] = [(f["code"], f["pc"]) for f in stats["faults"]]
        res["perf"] = [stats["perf"].get(n, 0) for n in PERF_NAMES] if stats["perf"] else None
        res["l1"] = stats.get("l1")
        res["trace"] = (d / "trace.txt").read_text().splitlines() if trace and mode != "functional" else None
    return res


# --------------------------------------------------------------------------- RTL
def cmd(op: int, arg: int = 0, data: int = 0) -> int:
    return ((op & 0xF) << 60) | ((arg & 0xFFFFFFF) << 32) | (data & 0xFFFFFFFF)


def build_commands(job: Job, cfg: dict, read_regs=True, read_mem=True) -> list[int]:
    c = [cmd(0)]  # a NOP first: exercises the no-response path
    for i, w in enumerate(job.words):
        c.append(cmd(1, i, w))
    for a, v in sorted(job.mem.items()):
        c.append(cmd(2, a, v))
    for i, v in sorted(job.args.items()):
        c.append(cmd(4, i, v))
    warps = cfg["num_warps"] if job.warps is None else job.warps
    c.append(cmd(5, 0, (warps & 0xF) | (POLICIES[job.policy] << 4)))
    for i in range(16):
        c.append(cmd(6, i))
    for w in range(cfg["num_warps"]):
        c.append(cmd(7, w))
    if read_mem:
        for a in range(cfg["spm_words"]):
            c.append(cmd(3, a))
    if read_regs:
        for w in range(cfg["num_warps"]):
            for l in range(cfg["num_lanes"]):
                for r in range(32):
                    c.append(cmd(8, (w << 10) | (l << 5) | r))
    c.append(cmd(0xE))  # unknown opcode -> must produce an ERROR response
    return c


def run_rtl_commands(cmds: list[int], cfg: dict, seed=1, gap=20, bp=20, trace=False, maxcyc=3_000_000) -> dict:
    """Run an arbitrary host command stream on the RTL top level; returns raw responses.
    TFX_RTL_MAXCYC (environment) caps the testbench watchdog, e.g. for mutation testing."""
    maxcyc = min(maxcyc, int(os.environ.get("TFX_RTL_MAXCYC", maxcyc)))
    exe = build_rtl(cfg)
    with tempfile.TemporaryDirectory(dir=BUILD) as td:
        d = Path(td)
        (d / "cmds.hex").write_text("".join(f"{x:016x}\n" for x in cmds))
        plus = [f"+CMDS={d / 'cmds.hex'}", f"+RESP={d / 'resp.hex'}", f"+SEED={seed}", f"+GAP={gap}", f"+BP={bp}",
                f"+MAXCYC={maxcyc}"]
        if trace:
            plus.append(f"+TRACE={d / 'trace.txt'}")
        r = subprocess.run(["vvp", "-n", str(exe), *plus], capture_output=True, text=True)
        out = r.stdout + r.stderr
        if r.returncode != 0 or "TB_DONE" not in out:
            raise RuntimeError("RTL simulation failed:\n" + out[-4000:])
        return {"resp": [int(x, 16) for x in (d / "resp.hex").read_text().split()],
                "trace": (d / "trace.txt").read_text().splitlines() if trace else None,
                "assertions": [ln for ln in out.splitlines() if "error" in ln.lower()]}


def parse_responses(resp: list[int], cfg: dict) -> dict:
    res = {"spm": [0] * cfg["spm_words"], "regs": {}, "perf": [None] * 16, "faults": [(0, 0)] * cfg["num_warps"],
           "launch_cycles": [], "errors": []}
    for x in resp:
        op, arg, data = x >> 60, (x >> 32) & 0xFFFFFFF, x & 0xFFFFFFFF
        if op == 3:
            res["spm"][arg] = data
        elif op == 5:
            res["launch_cycles"].append(data)
        elif op == 6:
            res["perf"][arg] = data
        elif op == 7:
            code = (data >> 16) & 0xF
            res["faults"][arg] = (code, data & 0xFFFF if code else 0)
        elif op == 8:
            res["regs"][((arg >> 10) & 7, (arg >> 5) & 31, arg & 31)] = data
        elif op == 0xF:
            res["errors"].append(arg & 0xF)
    return res


def run_rtl(job: Job, cfg: dict, seed=1, gap=20, bp=20, trace=True, read_regs=True, read_mem=True,
            maxcyc=3_000_000) -> dict:
    cmds = build_commands(job, cfg, read_regs=read_regs, read_mem=read_mem)
    raw = run_rtl_commands(cmds, cfg, seed=seed, gap=gap, bp=bp, trace=trace, maxcyc=maxcyc)
    res = parse_responses(raw["resp"], cfg)
    res["trace"], res["assertions"], res["n_resp"] = raw["trace"], raw["assertions"], len(raw["resp"])
    res["launch_cycles"] = res["launch_cycles"][0] if res["launch_cycles"] else None
    expected_resp = len([c for c in cmds if (c >> 60) in (3, 5, 6, 7, 8, 0xE)])
    if len(raw["resp"]) != expected_resp:
        res["assertions"].append(f"response count {len(raw['resp'])} != expected {expected_resp}")
    if res["errors"] != [0xE]:
        res["assertions"].append(f"unexpected ERROR responses {res['errors']}")
    return res


# --------------------------------------------------------------------------- comparison
def compare(a: dict, b: dict, cfg: dict, what=("spm", "faults", "perf", "regs", "trace"), la="cpp", lb="rtl"):
    """Returns a list of human-readable mismatch descriptions (empty == identical)."""
    mism = []
    if "spm" in what and a["spm"] != b["spm"]:
        bad = [i for i in range(cfg["spm_words"]) if a["spm"][i] != b["spm"][i]]
        mism.append(f"scratchpad differs at {len(bad)} words, first {bad[:5]}: "
                    f"{la}={[hex(a['spm'][i]) for i in bad[:3]]} {lb}={[hex(b['spm'][i]) for i in bad[:3]]}")
    if "faults" in what and list(a["faults"]) != list(b["faults"]):
        mism.append(f"faults differ: {la}={a['faults']} {lb}={b['faults']}")
    if "perf" in what and a.get("perf") and b.get("perf") and a["perf"] != b["perf"]:
        d = {PERF_NAMES[i]: (a["perf"][i], b["perf"][i]) for i in range(16) if a["perf"][i] != b["perf"][i]}
        mism.append(f"perf counters differ ({la},{lb}): {d}")
    if "regs" in what and b["regs"]:
        bad = [k for k in a["regs"] if a["regs"][k] != b["regs"].get(k)]
        if bad:
            mism.append(f"registers differ at {len(bad)} entries, first {bad[:3]}: "
                        f"{la}={[hex(a['regs'][k]) for k in bad[:3]]} {lb}={[hex(b['regs'].get(k, 0)) for k in bad[:3]]}")
    if "trace" in what and a.get("trace") is not None and b.get("trace") is not None and a["trace"] != b["trace"]:
        n = min(len(a["trace"]), len(b["trace"]))
        i = next((k for k in range(n) if a["trace"][k] != b["trace"][k]), n)
        mism.append(f"issue trace diverges at line {i}: {la}={a['trace'][i] if i < len(a['trace']) else 'EOF'!r} "
                    f"{lb}={b['trace'][i] if i < len(b['trace']) else 'EOF'!r}")
    return mism


def cosim(job: Job, cfg: dict, seed=1, gap=20, bp=20, rtl=True, func_regs=True) -> dict:
    """Run all three models and cross-check. Returns a report dict with 'ok' and 'mismatches'.
    RTL is skipped (not faked) for configurations that enable the C++-only L1 model."""
    rtl = rtl and rtl_compatible(cfg)
    cpp = run_cpp(job, cfg, mode="both", compare_regs=func_regs)
    rep = {"job": job.name, "config": cfg["name"], "policy": job.policy, "seed": seed,
           "cpp": cpp, "mismatches": []}
    if not cpp["models_agree"]:
        rep["mismatches"].append("C++ functional and timing models disagree: " + cpp["stderr"].strip())
    if rtl:
        r = run_rtl(job, cfg, seed=seed, gap=gap, bp=bp)
        rep["rtl"] = r
        rep["mismatches"] += compare(cpp, r, cfg)
        if r["launch_cycles"] != cpp["perf"][0]:
            rep["mismatches"].append(f"LAUNCH response cycles {r['launch_cycles']} != timing model {cpp['perf'][0]}")
        rep["mismatches"] += [f"RTL: {e}" for e in r["assertions"]]
    rep["ok"] = not rep["mismatches"]
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--program", required=True, help=".tfx source")
    ap.add_argument("--mem-file", help="lines 'WORD_ADDR VALUE' (hex)")
    ap.add_argument("--arg", action="append", default=[], help="I=V")
    ap.add_argument("--warps", type=int)
    ap.add_argument("--policy", default="lrr", choices=POLICIES)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--no-rtl", action="store_true")
    ap.add_argument("--no-func-regs", action="store_true", help="skip functional register compare (atomics)")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    mem = {}
    if a.mem_file:
        for ln in Path(a.mem_file).read_text().splitlines():
            if ln.strip() and not ln.startswith("#"):
                k, v = ln.split()[:2]
                mem[int(k, 16)] = int(v, 16)
    args = {int(kv.split("=")[0]): int(kv.split("=")[1], 0) for kv in a.arg}
    job = job_from_file(a.program, mem=mem, args=args, warps=a.warps, policy=a.policy)
    rep = cosim(job, cfg, seed=a.seed, rtl=not a.no_rtl, func_regs=not a.no_func_regs)
    p = rep["cpp"]["perf"]
    print(f"{job.name} [{cfg['name']}, {job.policy}] cycles={p[0]} issued={p[1]} thread_instrs={p[2]}")
    for m in rep["mismatches"]:
        print("MISMATCH:", m)
    print("PASS" if rep["ok"] else "FAIL")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
