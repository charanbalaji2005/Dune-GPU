"""Simulation-only L1 data-cache model (C++ cycle simulator). There is no RTL for it;
these tests check its timing contracts, replacement policies, counters and that it
never changes architectural results."""
import pytest

import cosim
import workloads as W
from conftest import config, derived

L1 = dict(l1_enable=1, l1_size_bytes=1024, l1_line_bytes=32, l1_assoc=2, l1_policy="lru",
          l1_hit_latency=1, l1_miss_latency=20)


def run(src, cfg, **kw):
    res = cosim.run_cpp(cosim.job_from_source("t", src, **kw), cfg, trace=True, compare_regs=True)
    assert res["models_agree"], res["stderr"]
    return res, dict(zip(cosim.PERF_NAMES, res["perf"]))


def test_disabled_l1_is_bit_identical_to_baseline():
    base = config("tfx_base")
    off = derived("tfx_base", **(L1 | {"l1_enable": 0}))
    for name in ("vector_add", "matmul", "histogram", "reduction"):
        a = cosim.run_cpp(W.WORKLOADS[name](base).job, base)
        b = cosim.run_cpp(W.WORKLOADS[name](off).job, off)
        assert a["trace"] == b["trace"] and a["perf"] == b["perf"] and b["l1"] is None


def test_rtl_refuses_l1_configs():
    with pytest.raises(ValueError):
        cosim.build_rtl(config("tfx_base_l1"))


@pytest.mark.parametrize("miss,hit", [(20, 1), (7, 3), (1, 1)])
def test_load_use_latency_hit_and_miss(miss, hit):
    cfg = derived("tfx_base", **(L1 | {"l1_miss_latency": miss, "l1_hit_latency": hit}))
    _, p = run("lw r2, 0(r0)\naddi r3, r2, 1\nexit", cfg, warps=1, mem={0: 5})
    assert p["stall_scoreboard"] == miss + 1          # cold miss
    res, p = run("lw r2, 0(r0)\nlw r4, 4(r0)\naddi r3, r4, 1\nexit", cfg, warps=1, mem={0: 5, 1: 9})
    assert res["l1"]["load_misses"] == 1 and res["l1"]["load_hits"] == 1
    assert res["regs"][(0, 3, 3)] == 10


@pytest.mark.parametrize("policy,hits", [("lru", 2), ("fifo", 1)])
def test_replacement_policy_lru_vs_fifo(policy, hits):
    # one set, two ways, 32-byte lines: A B A C A
    cfg = derived("tfx_base", **(L1 | {"l1_size_bytes": 64, "l1_policy": policy}))
    src = "lw r1, 0(r0)\nlw r2, 32(r0)\nlw r3, 0(r0)\nlw r4, 64(r0)\nlw r5, 0(r0)\nexit"
    res, _ = run(src, cfg, warps=1)
    assert res["l1"]["load_hits"] == hits and res["l1"]["evictions"] == (1 if policy == "lru" else 2)


def test_random_policy_is_deterministic_per_seed():
    src = "".join(f"lw r1, {(i * 37 % 16) * 32}(r0)\n" for i in range(40)) + "exit"
    out = []
    for seed in (1, 1, 2):
        cfg = derived("tfx_base", **(L1 | {"l1_size_bytes": 128, "l1_policy": "random", "l1_seed": seed}))
        out.append(run(src, cfg, warps=1)[0]["l1"])
    assert out[0] == out[1]


def test_associativity_removes_conflict_misses():
    src = "".join("lw r1, 0(r0)\nlw r2, 1024(r0)\n" for _ in range(6)) + "exit"   # set 0 in both (32 / 16 sets)
    dm = run(src, derived("tfx_base", **(L1 | {"l1_assoc": 1})), warps=1)[0]["l1"]
    tw = run(src, derived("tfx_base", **(L1 | {"l1_assoc": 2})), warps=1)[0]["l1"]
    assert dm["load_hits"] == 0 and dm["load_misses"] == 12
    assert tw["load_misses"] == 2 and tw["load_hits"] == 10


def test_line_size_coalescing():
    src = "s2r r1, lane_id\nslli r1, r1, 2\nlw r2, 0(r1)\nexit"   # 8 lanes, 32 consecutive bytes
    for line, tx in ((32, 1), (16, 2), (4, 8)):
        cfg = derived("tfx_base", **(L1 | {"l1_line_bytes": line}))
        res, p = run(src, cfg, warps=1)
        assert p["lsu_rounds"] == tx and res["l1"]["load_misses"] == tx and p["lsu_lane_accesses"] == 8


def test_store_no_write_allocate_and_atomic_invalidate():
    cfg = config("tfx_base_l1")
    res, _ = run("movi r1, 3\nsw r1, 0(r0)\nlw r2, 0(r0)\nexit", cfg, warps=1)
    assert res["l1"]["store_misses"] == 1 and res["l1"]["load_misses"] == 1
    res, _ = run("movi r1, 1\nlw r2, 0(r0)\natom.add r3, r0, r1\nlw r4, 0(r0)\nexit", cfg, warps=1, mem={0: 10})
    l1 = res["l1"]
    assert l1["atomic_bypasses"] == 8 and l1["atomic_invalidations"] == 1 and l1["load_misses"] == 2
    assert res["spm"][0] == 18 and res["regs"][(0, 7, 4)] == 18


L1_VARIANTS = [dict(), dict(l1_assoc=1), dict(l1_assoc=4, l1_policy="fifo"), dict(l1_policy="random", l1_seed=7),
               dict(l1_line_bytes=4, l1_size_bytes=256), dict(l1_line_bytes=128, l1_size_bytes=4096, l1_miss_latency=60)]


@pytest.mark.parametrize("variant", range(len(L1_VARIANTS)))
@pytest.mark.parametrize("name", list(W.WORKLOADS))
def test_l1_never_changes_results(variant, name):
    cfg = derived("tfx_base", **(L1 | L1_VARIANTS[variant]))
    inst = W.WORKLOADS[name](cfg)
    res = cosim.run_cpp(inst.job, cfg, trace=False, compare_regs=inst.func_regs)
    assert res["models_agree"], res["stderr"]
    assert W.check_golden(inst, res["spm"]) == []
    p = dict(zip(cosim.PERF_NAMES, res["perf"]))
    assert p["cycles"] == p["issued"] + sum(p[k] for k in ("stall_scoreboard", "stall_structural",
                                                               "stall_barrier", "stall_other"))


def test_fuzz_with_l1():
    import fuzz
    assert fuzz.main(["--config", config("tfx_base_l1")["_path"], "--count", "60", "--seed", "8", "--no-rtl"]) == 0
