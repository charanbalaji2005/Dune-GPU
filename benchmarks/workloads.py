"""Workload definitions: kernel source + input generator + independent Python golden model.

Each workload's ``build(cfg, seed)`` returns a :class:`Instance` with the job
(program, memory image, kernel args) and the expected values of every output
word, computed here in plain Python without using any CharanGPU model. Inputs
scale with the configuration so the same workload runs on every cluster size.
"""
from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "cosim"))
import cosim  # noqa: E402

M32 = 0xFFFFFFFF


def s32(v: int) -> int:
    v &= M32
    return v - (1 << 32) if v & 0x80000000 else v


def mul32(a, b):
    return (a * b) & M32


def sra(v, n):
    return (s32(v) >> n) & M32


@dataclass
class Instance:
    job: cosim.Job
    expected: dict[int, int]                 # word address -> expected value
    func_regs: bool = True                   # functional/timing register compare valid (no order-dependent atomics)
    meta: dict = field(default_factory=dict)


def threads(cfg):
    return cfg["num_warps"] * cfg["num_lanes"]


def _job(path, mem, args, policy="lrr", warps=None):
    return cosim.job_from_file(ROOT / path, mem=mem, args=args, policy=policy, warps=warps)


# ----------------------------------------------------------------------------- kernels
def vector_add(cfg, seed=1, policy="lrr"):
    r = random.Random(seed)
    third = cfg["spm_words"] // 4
    n = min(third, threads(cfg) * 3 + 5)
    A = [r.getrandbits(32) for _ in range(n)]
    B = [r.getrandbits(32) for _ in range(n)]
    mem = {i: A[i] for i in range(n)} | {third + i: B[i] for i in range(n)} | {2 * third + i: 0 for i in range(n)}
    exp = {2 * third + i: (A[i] + B[i]) & M32 for i in range(n)}
    job = _job("examples/vector_add/vector_add.tfx", mem, {0: 0, 1: third * 4, 2: 2 * third * 4, 3: n}, policy)
    return Instance(job, exp, meta={"elements": n})


def _chain(x, tid, iters):
    for _ in range(iters):
        x = mul32(x, x)
        x = (x + 7) & M32
        x ^= tid
        x = (x + (x >> 3)) & M32
    return x


def dep_chain(cfg, seed=1, policy="lrr", iters=24):
    T = threads(cfg)
    exp = {t: _chain(t + 1, t, iters) for t in range(T)}
    job = _job("examples/dependency/dep_chain.tfx", {t: 0 for t in range(T)}, {0: 0, 1: iters}, policy)
    return Instance(job, exp, meta={"iterations": iters})


def ilp(cfg, seed=1, policy="lrr", iters=24):
    T = threads(cfg)
    exp = {}
    for t in range(T):
        xs = [t + 1, t + 2, t + 3, t + 4]
        for _ in range(iters):
            xs = [(mul32(x, x) + 7) & M32 for x in xs]
        exp[t] = xs[0] ^ xs[1] ^ xs[2] ^ xs[3]
    job = _job("examples/dependency/ilp.tfx", {t: 0 for t in range(T)}, {0: 0, 1: iters}, policy)
    return Instance(job, exp, meta={"iterations": iters})


def divergence(cfg, seed=1, policy="lrr"):
    T = threads(cfg)
    exp = {}
    for t in range(T):
        if t & 1:
            v = (t * 3) & M32
            v = (v + 100) & M32 if t & 2 else (v - 50) & M32
        else:
            v = (t << 4) & M32
        for i in range((t & 7) + 1):
            v = (mul32(v, 5) + i) & M32
        exp[t] = v
    job = _job("examples/divergence/divergence.tfx", {t: 0 for t in range(T)}, {0: 0}, policy)
    return Instance(job, exp)


def reduction(cfg, seed=1, policy="lrr"):
    r = random.Random(seed)
    T = threads(cfg)
    assert T & (T - 1) == 0, "reduction needs a power-of-two thread count"
    n = min(cfg["spm_words"] // 2, 8 * T + 3)
    data = [r.randrange(0, 1 << 20) for _ in range(n)]
    buf, res = n, n + T
    mem = {i: data[i] for i in range(n)} | {buf + t: 0 for t in range(T)} | {res: 0}
    exp = {res: sum(data) & M32}
    job = _job("examples/reduction/reduction.tfx", mem, {0: 0, 1: n, 2: buf * 4, 3: res * 4}, policy)
    return Instance(job, exp, meta={"elements": n})


def histogram(cfg, seed=1, policy="lrr"):
    r = random.Random(seed)
    T = threads(cfg)
    n = min(cfg["spm_words"] // 2, 6 * T)
    data = [r.getrandbits(32) if r.random() < 0.5 else r.choice([3, 3, 3, 7]) for _ in range(n)]  # skewed
    bins = n
    mem = {i: data[i] for i in range(n)} | {bins + b: 0 for b in range(16)}
    exp = {bins + b: sum(1 for d in data if d & 15 == b) for b in range(16)}
    job = _job("examples/histogram/histogram.tfx", mem, {0: 0, 1: n, 2: bins * 4}, policy)
    return Instance(job, exp, func_regs=False, meta={"elements": n})


def matmul(cfg, seed=1, policy="lrr"):
    r = random.Random(seed)
    N = 16 if cfg["spm_words"] >= 1024 else 8
    while 3 * N * N > cfg["spm_words"]:
        N //= 2
    lg = N.bit_length() - 1
    A = [[r.randrange(-64, 64) for _ in range(N)] for _ in range(N)]
    B = [[r.randrange(-64, 64) for _ in range(N)] for _ in range(N)]
    a0, b0, c0 = 0, N * N, 2 * N * N
    mem = {}
    for i in range(N):
        for j in range(N):
            mem[a0 + i * N + j] = A[i][j] & M32
            mem[b0 + i * N + j] = B[i][j] & M32
            mem[c0 + i * N + j] = 0
    exp = {c0 + i * N + j: sum(A[i][k] * B[k][j] for k in range(N)) & M32 for i in range(N) for j in range(N)}
    job = _job("examples/matrix_multiply/matmul.tfx", mem, {0: a0 * 4, 1: b0 * 4, 2: c0 * 4, 3: N, 4: lg}, policy)
    return Instance(job, exp, meta={"N": N})


def stride(cfg, seed=1, policy="lrr", s=1, repeats=8):
    T, L = threads(cfg), cfg["num_lanes"]
    base, out = 0, cfg["spm_words"] // 2
    span = (L - 1) * s + 1
    assert span <= out
    vals = {i: (i * 2654435761) & 0xFFFF for i in range(span)}
    mem = dict(vals) | {out + t: 0 for t in range(T)}
    exp = {out + t: (vals[(t % L) * s] * repeats) & M32 for t in range(T)}
    job = _job("examples/bank_conflicts/stride.tfx", mem, {0: base * 4, 1: s, 2: repeats, 3: out * 4}, policy)
    return Instance(job, exp, meta={"stride": s, "repeats": repeats})


def mandel_params(cfg):
    W, H = (32, 24) if cfg["spm_words"] >= 1024 else (16, 12)
    return W, H, W.bit_length() - 1, -2 * 4096, -4608, (3 * 4096) // W, (9 * 4096 // 4) // H, 24


def mandelbrot_golden(W, H, x0, y0, dx, dy, maxit):
    out = []
    for p in range(W * H):
        x, y = p & (W - 1), p // W
        cr, ci = (x * dx + x0) & M32, (y * dy + y0) & M32
        zr = zi = it = 0
        active = 1
        while active:
            zr2 = sra(mul32(zr, zr), 12)
            zi2 = sra(mul32(zi, zi), 12)
            mag = (zr2 + zi2) & M32
            if not (s32(16384) < s32(mag)):
                t = sra(mul32(zr, zi), 11)
                zi = (t + ci) & M32
                zr = (zr2 - zi2 + cr) & M32
                it += 1
                active = 1 if it < maxit else 0
            else:
                active = 0
        out.append(it)
    return out


def mandelbrot(cfg, seed=1, policy="lrr"):
    W, H, lg, x0, y0, dx, dy, maxit = mandel_params(cfg)
    gold = mandelbrot_golden(W, H, x0, y0, dx, dy, maxit)
    mem = {p: 0 for p in range(W * H)}
    args = {0: 0, 1: W * H, 2: lg, 3: x0 & M32, 4: y0 & M32, 5: dx, 6: dy, 7: maxit}
    job = _job("examples/mandelbrot/mandelbrot.tfx", mem, args, policy)
    return Instance(job, dict(enumerate(gold)), meta={"W": W, "H": H, "maxit": maxit})


def raster_scene(W, H):
    """Five overlapping triangles in Q4 sub-pixel units, scaled to the framebuffer."""
    S = 16 * W // 32
    tris = [
        ((2, 2), (30, 6), (8, 28), 0xE04040, 50),
        ((16, 1), (31, 30), (4, 18), 0x40C040, 30),
        ((6, 10), (26, 12), (14, 31), 0x4060F0, 40),
        ((0, 31), (10, 20), (20, 31), 0xF0D020, 10),
        ((20, 2), (31, 2), (31, 14), 0xC040C0, 60),
    ]
    out = []
    for (a, b, c, col, z) in tris:
        v = [(a[0] * S, a[1] * S * H // W), (b[0] * S, b[1] * S * H // W), (c[0] * S, c[1] * S * H // W)]
        # normalise winding so that inside edge functions are >= 0
        area = (v[1][0] - v[0][0]) * (v[2][1] - v[0][1]) - (v[2][0] - v[0][0]) * (v[1][1] - v[0][1])
        e01_at_v2 = (v[2][0] - v[0][0]) * (v[1][1] - v[0][1]) - (v[2][1] - v[0][1]) * (v[1][0] - v[0][0])
        if e01_at_v2 < 0:
            v[1], v[2] = v[2], v[1]
        del area
        out.append((v, col, z))
    return out


def raster_golden(W, H, tris, bg):
    def edge(ax, ay, bx, by, px, py):
        return (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    fb = []
    for p in range(W * H):
        x, y = p & (W - 1), p // W
        px, py = x * 16 + 8, y * 16 + 8
        best, col = 0x7FFFFFFF, bg
        for (v, c, z) in tris:
            (x0, y0), (x1, y1), (x2, y2) = v
            e = [edge(x0, y0, x1, y1, px, py), edge(x1, y1, x2, y2, px, py), edge(x2, y2, x0, y0, px, py)]
            if min(e) >= 0 and z < best:
                best, col = z, c
        if best != 0x7FFFFFFF and ((x >> 2) ^ (y >> 2)) & 1:
            col = (col >> 1) & 0x7F7F7F
        fb.append(col)
    return fb


def raster(cfg, seed=1, policy="lrr"):
    W, H = (32, 32) if cfg["spm_words"] >= 2048 else (16, 16)
    bg = 0x101828
    tris = raster_scene(W, H)
    fb0, t0 = 0, W * H
    mem = {p: 0 for p in range(W * H)}
    for i, (v, c, z) in enumerate(tris):
        rec = [v[0][0], v[0][1], v[1][0], v[1][1], v[2][0], v[2][1], c, z]
        for k, val in enumerate(rec):
            mem[t0 + 8 * i + k] = val & M32
    args = {0: fb0 * 4, 1: W * H, 2: W.bit_length() - 1, 3: t0 * 4, 4: len(tris), 5: bg}
    job = _job("examples/triangle_rasterization/raster.tfx", mem, args, policy)
    return Instance(job, dict(enumerate(raster_golden(W, H, tris, bg))), meta={"W": W, "H": H, "triangles": len(tris)})


def blur(cfg, seed=1, policy="lrr"):
    r = random.Random(seed)
    W, H = (32, 16) if cfg["spm_words"] >= 1024 else (16, 8)
    img = [((x * 7 + y * 13) & 0xFF) if (x // 4 + y // 4) % 2 else r.randrange(256) for y in range(H) for x in range(W)]
    i0, o0 = 0, W * H
    exp = {}
    for y in range(H):
        for x in range(W):
            acc = 0
            for dy, wy in ((-1, 1), (0, 2), (1, 1)):
                for dx, wx in ((-1, 1), (0, 2), (1, 1)):
                    xx, yy = min(max(x + dx, 0), W - 1), min(max(y + dy, 0), H - 1)
                    acc += wy * wx * img[yy * W + xx]
            exp[o0 + y * W + x] = acc >> 4
    mem = dict(enumerate(img)) | {o0 + p: 0 for p in range(W * H)}
    args = {0: i0 * 4, 1: o0 * 4, 2: W * H, 3: W.bit_length() - 1, 4: H - 1}
    job = _job("examples/image_filter/blur.tfx", mem, args, policy)
    return Instance(job, exp, meta={"W": W, "H": H})


WORKLOADS = {
    "vector_add": vector_add, "dep_chain": dep_chain, "ilp": ilp, "divergence": divergence,
    "reduction": reduction, "histogram": histogram, "matmul": matmul, "stride1": lambda c, **k: stride(c, s=1, **k),
    "stride_bank": lambda c, **k: stride(c, s=c["spm_banks"], **k), "broadcast": lambda c, **k: stride(c, s=0, **k),
    "mandelbrot": mandelbrot, "raster": raster, "blur": blur,
}


def check_golden(inst: Instance, spm: list[int]) -> list[str]:
    bad = [a for a, v in inst.expected.items() if spm[a] != (v & M32)]
    if not bad:
        return []
    return [f"golden mismatch at {len(bad)} words, first {bad[:4]}: got "
            f"{[hex(spm[a]) for a in bad[:3]]} expected {[hex(inst.expected[a] & M32) for a in bad[:3]]}"]
