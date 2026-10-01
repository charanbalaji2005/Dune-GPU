# Related work (orientation only)

These are well-known public projects and ideas that inspired parts of TFX-X. The
list is from memory and was **not** re-checked against the sources while writing;
verify names, links and details before citing any of them.

* **SIMT execution with per-warp reconvergence stacks** — the general model used by
  NVIDIA-style GPUs and described in GPU architecture textbooks and lectures
  (e.g. Aamodt, Fung and Rogers, *General-Purpose Graphics Processor Architectures*).
* **Greedy-then-oldest (GTO) warp scheduling** — studied in academic work on
  cache-conscious warp scheduling (Rogers et al.).
* **Open-source GPU research projects**: Vortex (RISC-V based GPGPU, Georgia Tech),
  MIAOW (AMD Southern-Islands-compatible compute unit), Nyuzi (Jeff Bush's GPGPU),
  and the GPGPU-Sim / Accel-Sim simulators.
* **Edge-function triangle rasterization** — Pineda (1988), the basis of the
  `examples/triangle_rasterization` kernel.

TFX-X does not reuse code from any of these projects.
