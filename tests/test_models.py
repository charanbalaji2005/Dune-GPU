"""C++ functional + cycle models against the independent Python golden models (fast, no RTL)."""
import pytest
import cosim
import faults
import fuzz
import workloads as W
from conftest import config

CONFIGS = ["tfx_base", "tfx_small", "tfx_onebank", "tfx_wide"]


@pytest.mark.parametrize("cfg_name", CONFIGS)
@pytest.mark.parametrize("name", list(W.WORKLOADS))
def test_workload_golden(cfg_name, name):
    cfg = config(cfg_name)
    for policy in cosim.POLICIES:
        inst = W.WORKLOADS[name](cfg, policy=policy)
        res = cosim.run_cpp(inst.job, cfg, trace=False, compare_regs=inst.func_regs)
        assert res["models_agree"], res["stderr"]
        assert W.check_golden(inst, res["spm"]) == []
        assert all(f == (0, 0) for f in res["faults"])


@pytest.mark.parametrize("cfg_name", ["tfx_base", "tfx_small"])
def test_faults_cpp(cfg_name):
    cfg = config(cfg_name)
    for name, (src, code) in faults.fault_programs(cfg).items():
        job = cosim.job_from_source(name, src)
        res = cosim.run_cpp(job, cfg, trace=False)
        assert res["models_agree"]
        assert res["faults"][1] == (code, faults.expected_fault_pc(name, cfg, job.symbols)), name
        assert all(f == (0, 0) for w, f in enumerate(res["faults"]) if w != 1)


@pytest.mark.parametrize("cfg_name", CONFIGS)
def test_fuzz_cpp(cfg_name):
    assert fuzz.main(["--config", config(cfg_name)["_path"], "--count", "60", "--seed", "5", "--no-rtl"]) == 0


def test_perf_counter_invariants():
    cfg = config("tfx_base")
    for name in W.WORKLOADS:
        p = cosim.run_cpp(W.WORKLOADS[name](cfg).job, cfg, trace=False)["perf"]
        n = dict(zip(cosim.PERF_NAMES, p))
        assert n["issued"] == n["alu_instrs"] + n["mul_instrs"] + n["mem_instrs"] + n["ctrl_instrs"]
        assert n["cycles"] == n["issued"] + n["stall_scoreboard"] + n["stall_structural"] + n["stall_barrier"] + n["stall_other"]
        assert n["thread_instrs"] <= (n["issued"] - n["ctrl_instrs"]) * cfg["num_lanes"]
