"""Milestone-3 regression tests, written BEFORE the corresponding fixes.

Groups:
  * structured-branch rule for jmp/bany/bnone vs the SIMT mask stack (bug fix)
  * pipeline timing contracts: forwarding, MUL RAW/WAW, load-use, latency hiding
  * barriers (incl. exited warps), atomics ordering
  * multiplier edge cases (new pipelined multiplier)
  * reset / relaunch behaviour of the RTL top level
Every C++ check also runs on the RTL when Icarus is available.
"""
import json
import tempfile

import pytest

import cosim
import tfxasm
from conftest import ROOT, config, rtl, HAVE_IVERILOG

F_BRANCH_DEPTH = 8
M32 = 0xFFFFFFFF


def derived(base, **kw):
    cfg = {k: v for k, v in config(base).items() if not k.startswith("_")} | kw
    cfg["name"] = base + "".join(f"_{k}{v}" for k, v in kw.items())
    cosim.BUILD.mkdir(exist_ok=True)
    f = tempfile.NamedTemporaryFile("w", suffix=".json", dir=cosim.BUILD, delete=False)
    json.dump(cfg, f)
    f.close()
    cfg["_path"] = f.name
    return cfg


def both(job, cfg, rtl_too=True, func_regs=True):
    """Run C++ (functional + timing); if possible also RTL and require identity."""
    if rtl_too and HAVE_IVERILOG:
        rep = cosim.cosim(job, cfg, seed=13, func_regs=func_regs)
        assert rep["ok"], rep["mismatches"]
        return rep["cpp"]
    res = cosim.run_cpp(job, cfg, compare_regs=func_regs)
    assert res["models_agree"], res["stderr"]
    return res


def perf(res):
    return dict(zip(cosim.PERF_NAMES, res["perf"]))


def bany_word(rs1, target_off, rd=0, opc=0x29):
    return (opc << 26) | (rd << 21) | (rs1 << 16) | (target_off & 0xFFFF)


# ----------------------------------------------------------------------------- structured branches
def test_assembler_rejects_branch_out_of_if():
    with pytest.raises(tfxasm.AsmError, match="structured"):
        tfxasm.assemble("loop:\n movi r1, 1\n if r1\n bany r1, loop\n endif\n exit")


def test_assembler_rejects_then_body_jump_over_else():
    with pytest.raises(tfxasm.AsmError, match="structured"):
        tfxasm.assemble("movi r1, 1\nif r1\n jmp out\nelse\n nop\nout: endif\nexit")


def test_assembler_rejects_jump_into_if_body():
    with pytest.raises(tfxasm.AsmError, match="structured"):
        tfxasm.assemble("movi r1, 1\njmp inside\nif r1\ninside: nop\nendif\nexit")


def test_assembler_accepts_structured_branches():
    # loop entirely inside an if body, branch to own else, loop around a whole if/endif
    src = """movi r1, 1
top:    if r1
inner:    subi r2, r2, 1
          bany r2, inner
          jmp el
el:     else
          nop
        endif
        subi r1, r1, 1
        bany r1, top
        exit"""
    words, _, syms = tfxasm.assemble(src)
    # branch depth field = static mask-stack depth + 1
    assert (words[syms["inner"] + 1] >> 21) & 31 == 2      # bany inside if: depth 1
    assert (words[syms["top"] + 8] >> 21) & 31 == 1        # bany at top level: depth 0


def test_all_examples_still_assemble():
    for p in sorted((ROOT / "examples").rglob("*.tfx")):
        tfxasm.assemble(p.read_text())


def _escape_program():
    # if over lanes 1..N-1, then a raw (hand-encoded) BANY back to 'loop' from inside the
    # if body, claiming static depth 1 (rd = 2). Second execution sees runtime depth 2.
    return """        s2r   r5, lane_id
loop:   if    r5
bad:      .word {w}
        endif
        exit
""".format(w=hex(bany_word(5, -1, rd=2)))


@pytest.mark.parametrize("cfg_name", ["tfx_base", "tfx_small"])
def test_branch_depth_fault(cfg_name):
    cfg = config(cfg_name)
    job = cosim.job_from_source("escape", _escape_program(), warps=1)
    res = both(job, cfg)
    assert res["faults"][0] == (F_BRANCH_DEPTH, job.symbols["bad"])


def test_legacy_unchecked_branch_encoding_still_runs():
    # rd = 0 keeps the milestone-2 (unchecked) behaviour: here it overflows the stack
    cfg = config("tfx_small")
    src = _escape_program().replace(hex(bany_word(5, -1, rd=2)), hex(bany_word(5, -1, rd=0)))
    job = cosim.job_from_source("legacy", src, warps=1)
    res = both(job, cfg)
    assert res["faults"][0][0] == 5   # STACK_OVF at the IF


# ----------------------------------------------------------------------------- pipeline timing contracts
ONE = dict(warps=1)


@pytest.mark.parametrize("lat", [2, 3, 4, 6, 8])
def test_mul_raw_latency(lat):
    cfg = derived("tfx_small", mul_latency=lat)
    job = cosim.job_from_source("raw", "movi r1, 3\nmul r2, r1, r1\naddi r3, r2, 1\nexit", **ONE)
    res = both(job, cfg, rtl_too=lat in (2, 3, 6))
    assert perf(res)["stall_scoreboard"] == lat - 1
    assert res["regs"][(0, 0, 3)] == 10


@pytest.mark.parametrize("lat", [2, 4, 7])
def test_mul_waw_ordering(lat):
    cfg = derived("tfx_small", mul_latency=lat)
    job = cosim.job_from_source("waw", "movi r1, 3\nmul r2, r1, r1\nmovi r2, 5\nexit", **ONE)
    res = both(job, cfg, rtl_too=lat != 4)
    assert perf(res)["stall_scoreboard"] == lat - 1
    assert all(res["regs"][(0, l, 2)] == 5 for l in range(cfg["num_lanes"]))


def test_alu_forwarding_no_stalls():
    cfg = config("tfx_base")
    src = "movi r1, 1\n" + "addi r1, r1, 1\n" * 12 + "exit"
    res = both(cosim.job_from_source("fwd", src, **ONE), cfg)
    assert perf(res)["stall_scoreboard"] == 0
    assert res["regs"][(0, 7, 1)] == 13


def test_load_use_latency():
    cfg = config("tfx_base")
    job = cosim.job_from_source("lu", "lw r2, 0(r0)\naddi r3, r2, 1\nexit", mem={0: 41}, **ONE)
    res = both(job, cfg)
    # issue c, LSU start c+1, bank read c+2, writeback (DRAIN) c+3 -> dependent issues at c+3
    assert perf(res)["stall_scoreboard"] == 2
    assert res["regs"][(0, 5, 3)] == 42


@pytest.mark.parametrize("lat", [3, 4, 6, 8])
def test_latency_hiding_four_warps(lat):
    cfg = derived("tfx_base", mul_latency=lat)
    job = cosim.job_from_source("hide", "movi r1, 3\nmul r2, r1, r1\naddi r3, r2, 1\nexit")
    res = both(job, cfg, rtl_too=lat == 6)
    assert perf(res)["stall_scoreboard"] == max(0, lat - 4)


def test_counter_identity_on_examples():
    cfg = config("tfx_base")
    for name in ("vector_add", "matmul", "mandelbrot"):
        import workloads as W
        p = perf(cosim.run_cpp(W.WORKLOADS[name](cfg).job, cfg, trace=False))
        assert p["cycles"] == p["issued"] + sum(p[k] for k in ("stall_scoreboard", "stall_structural",
                                                                   "stall_barrier", "stall_other"))


# ----------------------------------------------------------------------------- barriers / atomics
def test_barrier_orders_cross_warp_communication():
    cfg = config("tfx_base")
    src = """        s2r   r1, warp_id
        s2r   r2, global_tid
        bany  r1, wait            ; warps 1..3 go straight to the barrier
        movi  r3, 40              ; warp 0: long loop, then publish
spin:   subi  r3, r3, 1
        bany  r3, spin
        movi  r4, 1234
        sw    r4, 4000(r0)
wait:   bar
        lw    r5, 4000(r0)
        slli  r6, r2, 2
        sw    r5, 0(r6)
        exit"""
    res = both(cosim.job_from_source("bar", src), cfg)
    p = perf(res)
    assert all(res["spm"][t] == 1234 for t in range(32))
    assert p["barrier_releases"] == 1 and p["stall_barrier"] > 0


def test_barrier_ignores_exited_and_faulted_warps():
    cfg = config("tfx_base")
    src = """        s2r   r1, warp_id
        seqi  r2, r1, 3
        bany  r2, leave
        seqi  r2, r1, 2
        bany  r2, crash
        bar
        bar
        s2r   r3, global_tid
        slli  r3, r3, 2
        movi  r4, 7
        sw    r4, 0(r3)
leave:  exit
crash:  .word 0xFC000000"""
    job = cosim.job_from_source("barx", src)
    res = both(job, cfg)
    assert perf(res)["barrier_releases"] == 2
    assert res["faults"][2][0] == 1
    assert [res["spm"][t] for t in range(16)] == [7] * 16 and res["spm"][16] == 0 and res["spm"][24] == 0


def test_atomic_lane_order_single_warp():
    cfg = config("tfx_base")
    job = cosim.job_from_source("at", "movi r3, 1\natom.add r2, r0, r3\nexit", mem={0: 100}, **ONE)
    res = both(job, cfg)
    assert [res["regs"][(0, l, 2)] for l in range(8)] == [100 + l for l in range(8)]
    assert res["spm"][0] == 108


def test_atomic_contention_all_warps():
    cfg = config("tfx_base")
    job = cosim.job_from_source("at4", "s2r r3, lane_id\naddi r3, r3, 1\natom.add r2, r0, r3\nexit", mem={0: 0})
    res = both(job, cfg, func_regs=False)
    assert res["spm"][0] == 4 * sum(range(1, 9))
    olds = sorted(res["regs"][(w, l, 2)] for w in range(4) for l in range(8))
    assert len(set(olds)) == 32 and olds[0] == 0


# ----------------------------------------------------------------------------- multiplier
EDGE = [0, 1, 2, 3, 0x7FFFFFFF, 0x80000000, 0x80000001, 0xFFFFFFFF, 0xFFFFFFFE, 0x0000FFFF, 0xFFFF0000,
        0x00010000, 0x12345678, 0xDEADBEEF, 0x55555555, 0xAAAAAAAA]


def s32(v):
    v &= M32
    return v - (1 << 32) if v & 0x80000000 else v


@pytest.mark.parametrize("cfg_name,lat", [("tfx_small", 2), ("tfx_base", 3), ("tfx_wide", 4)])
def test_multiplier_edge_cases(cfg_name, lat):
    cfg = config(cfg_name) if config(cfg_name)["mul_latency"] == lat else derived(cfg_name, mul_latency=lat)
    L = cfg["num_lanes"]
    T = L * cfg["num_warps"]
    n = len(EDGE)
    # thread t multiplies EDGE[t % n] by EDGE[(t * 7 + 3) % n] with mul, mulhu, mulh, and muli/mulhi/mulhui by -3
    src = f"""        s2r   r1, global_tid
        andi  r2, r1, {n - 1}
        muli  r3, r1, 7
        addi  r3, r3, 3
        andi  r3, r3, {n - 1}
        slli  r2, r2, 2
        slli  r3, r3, 2
        lw    r4, 0(r2)
        lw    r5, 0(r3)
        mul   r6, r4, r5
        mulhu r7, r4, r5
        mulh  r8, r4, r5
        muli  r9, r4, -3
        mulhi r10, r4, -3
        mulhui r11, r4, -3
        mul   r12, r6, r8          ; back-to-back dependent multiplies
        slli  r13, r1, 5
        addi  r13, r13, 256
        sw    r6, 0(r13)
        sw    r7, 4(r13)
        sw    r8, 8(r13)
        sw    r9, 12(r13)
        sw    r10, 16(r13)
        sw    r11, 20(r13)
        sw    r12, 24(r13)
        exit"""
    mem = {i: v for i, v in enumerate(EDGE)}
    res = both(cosim.job_from_source("muledge", src, mem=mem), cfg)
    for t in range(T):
        a, b = EDGE[t % n], EDGE[(t * 7 + 3) % n]
        m3 = (-3) & M32
        exp = [(a * b) & M32, (a * b) >> 32, ((s32(a) * s32(b)) >> 32) & M32,
               (a * m3) & M32, ((s32(a) * -3) >> 32) & M32, (a * m3) >> 32]
        exp.append(((a * b) & M32) * exp[2] & M32)
        got = [res["spm"][64 + t * 8 + k] for k in range(7)]
        assert got == exp, (t, hex(a), hex(b))


# ----------------------------------------------------------------------------- reset / relaunch (RTL)
@rtl
def test_relaunch_clears_state_but_keeps_scratchpad():
    cfg = config("tfx_small")
    j1 = cosim.job_from_source("first", """        s2r r1, global_tid
        movi r5, 123
        movi r6, 1
        if r6
          slli r2, r1, 2
          movi r7, 171
          sw r7, 400(r2)
        endif
        s2r r9, warp_id
        bany r9, boom
        exit
boom:   .word 0xFC000000""")
    j2 = cosim.job_from_source("second", """        s2r r1, global_tid
        slli r2, r1, 2
        sw r5, 0(r2)              ; r5 must read as zero after relaunch
        lw r7, 400(r2)            ; scratchpad persists across launches
        sw r7, 200(r2)
        exit""")
    c = [cosim.cmd(1, i, w) for i, w in enumerate(j1.words)]
    c += [cosim.cmd(5, 0, cfg["num_warps"])] + [cosim.cmd(7, w) for w in range(cfg["num_warps"])]
    c += [cosim.cmd(1, i, w) for i, w in enumerate(j2.words)] + [cosim.cmd(1, i, 0) for i in range(len(j2.words), len(j1.words))]
    c += [cosim.cmd(5, 0, cfg["num_warps"])] + [cosim.cmd(7, w) for w in range(cfg["num_warps"])]
    c += [cosim.cmd(6, i) for i in range(16)] + [cosim.cmd(3, a) for a in range(cfg["spm_words"])]
    raw = cosim.run_rtl_commands(c, cfg, seed=3)
    assert not raw["assertions"]
    resp = raw["resp"]
    faults1 = [((x >> 16) & 0xF) for x in resp[1:1 + cfg["num_warps"]]]
    assert faults1 == [0, 1]                                    # warp 1 faulted in launch 1
    second = cosim.parse_responses(resp[1 + cfg["num_warps"]:], cfg)
    assert second["faults"] == [(0, 0)] * cfg["num_warps"]     # fault state cleared by relaunch
    T = cfg["num_warps"] * cfg["num_lanes"]
    assert [second["spm"][t] for t in range(T)] == [0] * T
    assert [second["spm"][50 + t] for t in range(T)] == [171] * T
    ref = cosim.run_cpp(cosim.Job("second", j2.words, mem={100 + t: 171 for t in range(T)}), cfg, trace=False)
    assert second["perf"] == ref["perf"]                       # counters restart from zero
