import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
BUILD.mkdir(exist_ok=True)

def run(cmd, cwd=ROOT):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run(list(map(str, cmd)), cwd=cwd, check=True)

def main():
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        print("SKIP: Icarus Verilog not installed; C++/Python tests can still run.")
        return
    common = [ROOT / "rtl/core/tfx_alu.sv"]
    cases = [
        ("tb_alu_diff", common + [ROOT / "sim/tb_alu_diff.sv"], [f"+VECTORS={BUILD / 'alu_vectors.txt'}"]),
        ("tb_register_file", [ROOT / "rtl/core/tfx_register_file.sv", ROOT / "sim/tb_register_file.sv"], []),
        ("tb_token_channel", [ROOT / "rtl/interconnect/tfx_token_channel.sv", ROOT / "sim/tb_token_channel.sv"], []),
        ("tb_fifo", [ROOT / "rtl/interconnect/tfx_fifo.sv", ROOT / "sim/tb_fifo.sv"], []),
        ("tb_compute_lane", common + [ROOT / "rtl/core/tfx_compute_lane.sv", ROOT / "sim/tb_compute_lane.sv"], []),
        ("tb_decoder_diff", [ROOT / "rtl/core/tfx_decoder.sv", ROOT / "sim/tb_decoder_diff.sv"],
         [f"+VECTORS={BUILD / 'decode_vectors.txt'}"]),
        ("tb_scheduler_diff", [ROOT / "rtl/scheduler/tfx_warp_scheduler.sv", ROOT / "sim/tb_scheduler_diff.sv"],
         [f"+VECTORS={BUILD / 'sched_vectors.txt'}"]),
    ]
    ref = BUILD / "tfx_ref"
    if ref.exists():
        run([ref, "--vectors", BUILD / "alu_vectors.txt", 5000, 20261001])
        run([ref, "--decode-vectors", BUILD / "decode_vectors.txt", 20000, 7])
        run([ref, "--sched-vectors", BUILD / "sched_vectors.txt", 4])
    for name, sources, plusargs in cases:
        exe = BUILD / name
        run(["iverilog", "-g2012", "-Wall", "-s", name, "-o", exe, *sources])
        run(["vvp", exe, *plusargs])

if __name__ == "__main__": main()
