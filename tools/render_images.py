#!/usr/bin/env python3
"""Run the image-producing kernels and write PNGs (pure-Python encoder).

  python3 tools/render_images.py [--config CFG] [--rtl] [--scale 8]

Each image is produced by the C++ cycle model (and, with --rtl, also by the
RTL, which must be identical) and compared with the independent Python golden
model in benchmarks/workloads.py before it is written.
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))
import workloads as W  # noqa: E402
cosim = W.cosim


def write_png(path: Path, w: int, h: int, rgb: list[tuple[int, int, int]], scale: int = 1):
    rows = []
    for y in range(h * scale):
        row = bytearray([0])
        for x in range(w * scale):
            row += bytes(rgb[(y // scale) * w + (x // scale)])
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w * scale, h * scale, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def palette(it, maxit):
    if it >= maxit:
        return (0, 0, 0)
    t = it / maxit
    return (int(9 * (1 - t) * t ** 3 * 255), int(15 * (1 - t) ** 2 * t ** 2 * 255), int(8.5 * (1 - t) ** 3 * t * 255))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "architecture/configuration/tfx_base.json"))
    ap.add_argument("--rtl", action="store_true")
    ap.add_argument("--scale", type=int, default=8)
    ap.add_argument("--out", default=str(ROOT / "results/images"))
    a = ap.parse_args(argv)
    cfg = cosim.load_config(a.config)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ok = True
    for name in ("mandelbrot", "raster", "blur"):
        inst = W.WORKLOADS[name](cfg)
        res = cosim.run_cpp(inst.job, cfg, trace=False)
        spm = res["spm"]
        errs = W.check_golden(inst, spm)
        if a.rtl:
            r = cosim.run_rtl(inst.job, cfg, trace=False, read_regs=False)
            if r["spm"] != spm:
                errs.append("RTL framebuffer differs from C++ model")
        if errs:
            ok = False
            print(name, "FAILED:", errs)
            continue
        Wd, Ht = inst.meta["W"], inst.meta["H"]
        if name == "mandelbrot":
            px = [palette(spm[p], inst.meta["maxit"]) for p in range(Wd * Ht)]
        elif name == "raster":
            px = [((spm[p] >> 16) & 255, (spm[p] >> 8) & 255, spm[p] & 255) for p in range(Wd * Ht)]
        else:
            base = Wd * Ht
            src = [inst.job.mem[p] for p in range(Wd * Ht)]
            dst = [spm[base + p] for p in range(Wd * Ht)]
            write_png(out / f"blur_input_{cfg['name']}.png", Wd, Ht, [(v, v, v) for v in src], a.scale)
            px = [(v, v, v) for v in dst]
        p = out / f"{name}_{cfg['name']}.png"
        write_png(p, Wd, Ht, px, a.scale)
        print(f"{name}: {Wd}x{Ht}, {res['perf'][0]} cycles (simulated), golden OK"
              f"{', RTL identical' if a.rtl else ''} -> {p.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
