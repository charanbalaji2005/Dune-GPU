# ASIC / FPGA roadmap (not started)

Nothing here has been done yet; this is the intended order of work.

1. **Micro-architectural fixes for physical design**: pipeline the multiplier
   (2-3 stages), registered-read register files (SRAM-friendly), split
   the eligibility/scheduler path (currently ~70 generic cells deep in the tiny cluster).
2. **FPGA prototype** (e.g. a mid-range Xilinx/Lattice part with Yosys/nextpnr or vendor
   tools): replace host command port with UART/PCIe bridge, map scratchpad and imem to
   block RAM, measure Fmax and resources, run the existing kernels on hardware.
3. **Open PDK trial** (SkyWater 130 / GF180 via OpenLane): liberty-based synthesis, STA,
   P&R of `tfx_base`; replace behavioural memories with macros.
4. **Sign-off flow items** that do not exist today: DFT/scan, clock-domain crossing
   for the host interface, reset strategy review, power intent, formal property checks.

No area, frequency or power number should be quoted for CharanGPU until step 2 or 3
produces one.
