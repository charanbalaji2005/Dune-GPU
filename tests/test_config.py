import json
import pytest
import validate_config as vc
from conftest import ROOT

CFGS = sorted((ROOT / "architecture/configuration").glob("*.json"))


@pytest.mark.parametrize("path", CFGS, ids=[p.stem for p in CFGS])
def test_shipped_configs_valid(path):
    assert vc.validate(json.loads(path.read_text())) == []


@pytest.mark.parametrize("patch", [{"num_warps": 9}, {"spm_banks": 3}, {"mul_latency": 1}, {"extra": 1},
                                   {"sched_policy": "random"}, {"spm_words": 8, "spm_banks": 8}])
def test_invalid_configs_rejected(patch):
    cfg = json.loads(CFGS[0].read_text()) | patch
    assert vc.validate(cfg)
