import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "build" / "tfx_ref"

def run(op, a, b):
    out = subprocess.check_output([str(BIN), str(op), hex(a), hex(b)], text=True)
    fields = dict(part.split("=", 1) for part in out.strip().split())
    return int(fields["result"], 16), int(fields["zero"]), int(fields["illegal"])

def test_operations():
    assert run(0, 19, 23) == (42, 0, 0)
    assert run(0, 0xffffffff, 1) == (0, 1, 0)
    assert run(1, 0, 1) == (0xffffffff, 0, 0)
    assert run(2, 0xffffffff, 0xffffffff) == (1, 0, 0)
    assert run(3, 0xf0, 0x0f) == (0, 1, 0)
    assert run(4, 0xf0, 0x0f) == (0xff, 0, 0)
    assert run(5, 0xaa, 0xff) == (0x55, 0, 0)
    assert run(6, 0x12345678, 0xffffffff) == (0x12345678, 0, 0)
    assert run(7, 1, 2) == (0, 1, 1)
