#!/usr/bin/env python3
"""Mutation test of the verification flow itself: inject known RTL bugs one at a
time, run a short fuzz campaign (and, if that passes, the directed regression
tests), and require every mutant to be detected.

Each mutant is applied to a throw-away COPY of the repository (with its own
build/ directory), never to the working tree. An earlier in-place version could
leave a mutant behind if the process was killed, which then made unrelated
fuzz runs fail; this design makes that impossible.
The original file is always restored (even on failure)."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import os
import signal

LOCK = ROOT / "build" / "mutation_test.lock"
_active = {}   # path -> backup of the file currently mutated


def _restore_all(*_):
    for path, backup in list(_active.items()):
        shutil.copy(backup, path)
        path.touch()
        _active.pop(path, None)
    LOCK.unlink(missing_ok=True)
    sys.exit(130)


# A killed run must never leave a mutant in the source tree.
signal.signal(signal.SIGTERM, _restore_all)
signal.signal(signal.SIGINT, _restore_all)
LOCK.parent.mkdir(exist_ok=True)
try:
    fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
except FileExistsError:
    sys.exit(f"another mutation test holds {LOCK}; remove it only if no run is active")
muts = [
 ("rtl/compute/tfx_simt_cluster.sv", " || (f_wr[w] && sbe[f_rd[w]]));", ");", "scoreboard: WAW check removed"),
 ("rtl/compute/tfx_simt_cluster.sv", "|| (f_use_b[w] && sbe[f_rb[w]])", "", "scoreboard: RAW on rs2 removed"),
 ("rtl/core/tfx_alu.sv", "result = {{(DATA_W-1){1'b0}}, operand_a < operand_b};", "result = {{(DATA_W-1){1'b0}}, operand_a <= operand_b};", "ALU: SLTU off-by-one"),
 ("rtl/core/tfx_mul_pipe.sv", "wire [63:0] x5 = {63'd0, sgn};", "wire [63:0] x5 = 64'd0;", "MUL pipe: +1 of the MULH two's-complement correction dropped"),
 ("rtl/core/tfx_mul_pipe.sv", "c4 = ((s3 & c3) | (s3 & c2) | (c3 & c2)) << 1;", "c4 = ((s3 & c3) | (s3 & c2) | (c3 & c2)) << 0;", "MUL pipe: last CSA carry not shifted"),
 ("rtl/compute/tfx_simt_cluster.sv", "if (br_depth != 5'd0 &&", "if (1'b0 &&", "branch-depth check disabled"),
 ("rtl/memory/tfx_lsu.sv", "if (pend[l] && int'(lbank[l]) == b) begin", "if (pend[l] && int'(lbank[l]) == b && !(q_op == 2'd2 && l == 3)) begin", "LSU: lane 3 store dropped (hangs or corrupts)"),
]
for path, a, b, desc in muts:
    if a not in (ROOT / path).read_text():
        LOCK.unlink(missing_ok=True)
        sys.exit(f"pre-flight failed: original text for '{desc}' not found in {path} (tree already mutated?)")
undetected = 0
IGNORE = shutil.ignore_patterns("build", ".git", "__pycache__", ".pytest_cache", "results")
for path, a, b, desc in muts:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "repo"
        shutil.copytree(ROOT, work, ignore=IGNORE)
        target = work / path
        src = target.read_text()
        assert a in src, desc
        target.write_text(src.replace(a, b, 1))
        try:
            r = subprocess.run([sys.executable, str(work / "tools/cosim/fuzz.py"), "--config",
                                str(work / "architecture/configuration/tfx_small.json"), "--count", "25", "--seed", "77",
                                "--save-failures", str(Path(td) / "fail")],
                               capture_output=True, text=True, timeout=600, cwd=work)
            last = (r.stdout + r.stderr).strip().splitlines()[-1]
            if r.returncode == 0:   # fuzzing missed it: also run the directed regression suite
                r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                                    "tests/test_regressions_m3.py", "tests/test_cosim_rtl.py::test_faults_rtl"],
                                   capture_output=True, text=True, timeout=900, cwd=work)
                last = "directed tests: " + (r.stdout + r.stderr).strip().splitlines()[-1]
            how = "fuzz mismatch" if "programs agree" in last else ("directed test failure" if "directed" in last
                                                                     else "simulation error/hang")
            print(f"[{desc}] -> {'DETECTED (' + how + ')' if r.returncode != 0 else 'NOT DETECTED'}: {last[:140]}")
            undetected += r.returncode == 0
        except subprocess.TimeoutExpired:
            print(f"[{desc}] -> DETECTED (simulation hang / timeout)")
print("mutation test:", "PASS (all mutants detected)" if undetected == 0 else f"FAIL ({undetected} undetected)")
sys.exit(1 if undetected else 0)
