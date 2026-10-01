import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in ("compiler", "tools/cosim", "benchmarks", "tools/configuration"):
    sys.path.insert(0, str(ROOT / p))

HAVE_IVERILOG = shutil.which("iverilog") is not None and shutil.which("vvp") is not None
rtl = pytest.mark.skipif(not HAVE_IVERILOG, reason="Icarus Verilog not installed")


@pytest.fixture(scope="session")
def tfx_ref():
    exe = ROOT / "build" / "tfx_ref"
    exe.parent.mkdir(exist_ok=True)
    subprocess.run(["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Wpedantic", "-Werror", "-Isoftware/reference",
                    "software/reference/tfx_ref.cpp", "-o", str(exe)], cwd=ROOT, check=True)
    return exe


def config(name):
    import cosim
    return cosim.load_config(ROOT / "architecture" / "configuration" / f"{name}.json")


def derived(base, **kw):
    """A configuration derived from a shipped one with some fields overridden (written to build/)."""
    import json
    import tempfile
    import cosim
    cfg = {k: v for k, v in config(base).items() if not k.startswith("_")} | kw
    cfg["name"] = base + "".join(f"_{k}{v}" for k, v in kw.items())
    cosim.BUILD.mkdir(exist_ok=True)
    f = tempfile.NamedTemporaryFile("w", suffix=".json", dir=cosim.BUILD, delete=False)
    json.dump(cfg, f)
    f.close()
    cfg["_path"] = f.name
    return cfg
