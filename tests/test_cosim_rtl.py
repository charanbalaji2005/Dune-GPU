"""RTL (Icarus) vs C++ cycle model: exact cycles, counters, trace, registers, memory, faults."""
import pytest
import cosim
import faults
import fuzz
import workloads as W
from conftest import config, rtl

FAST = ["vector_add", "dep_chain", "ilp", "divergence", "reduction", "histogram", "stride1", "stride_bank", "broadcast"]


@rtl
@pytest.mark.parametrize("cfg_name", ["tfx_base", "tfx_small", "tfx_onebank"])
@pytest.mark.parametrize("name", FAST)
def test_workload_rtl(cfg_name, name):
    cfg = config(cfg_name)
    inst = W.WORKLOADS[name](cfg)
    rep = cosim.cosim(inst.job, cfg, seed=11, func_regs=inst.func_regs)
    assert rep["ok"], rep["mismatches"]
    assert W.check_golden(inst, rep["rtl"]["spm"]) == []


@rtl
@pytest.mark.parametrize("policy", ["gto", "prio", "memfirst"])
def test_policies_rtl(policy):
    cfg = config("tfx_base")
    for name in ("vector_add", "reduction", "histogram"):
        inst = W.WORKLOADS[name](cfg, policy=policy)
        rep = cosim.cosim(inst.job, cfg, seed=3, func_regs=inst.func_regs)
        assert rep["ok"], (name, rep["mismatches"])


@rtl
def test_faults_rtl():
    cfg = config("tfx_small")
    for name, (src, code) in faults.fault_programs(cfg).items():
        job = cosim.job_from_source(name, src)
        rep = cosim.cosim(job, cfg, seed=5)
        assert rep["ok"], (name, rep["mismatches"])
        assert rep["rtl"]["faults"][1] == (code, faults.expected_fault_pc(name, cfg, job.symbols))


@rtl
def test_fuzz_rtl_small():
    assert fuzz.main(["--config", config("tfx_small")["_path"], "--count", "20", "--seed", "9"]) == 0


@rtl
@pytest.mark.slow
@pytest.mark.parametrize("name", ["matmul", "blur", "mandelbrot", "raster"])
def test_large_kernels_rtl(name):
    cfg = config("tfx_base")
    inst = W.WORKLOADS[name](cfg)
    rep = cosim.cosim(inst.job, cfg, seed=2, func_regs=inst.func_regs)
    assert rep["ok"], rep["mismatches"]
