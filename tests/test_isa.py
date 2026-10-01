"""ISA single-source-of-truth checks: the JSON spec, the C++ decoder and the assembler must agree."""
import json
import subprocess

from conftest import ROOT

ISA = json.loads((ROOT / "architecture/isa/tfx_isa_v1.json").read_text())
CLS = {"alu": 0, "mul": 1, "lsu": 2, "ctrl": 3}


def test_json_well_formed():
    ops = [i["opcode"] for i in ISA["instructions"]]
    assert len(ops) == len(set(ops)) == 43
    assert all(0 <= o < 64 for o in ops)
    assert len({i["mnemonic"] for i in ISA["instructions"]}) == 43


def test_cpp_decoder_matches_json(tfx_ref):
    out = subprocess.check_output([str(tfx_ref), "--opcode-table"], text=True)
    table = {int(l.split()[0]): [int(x) for x in l.split()[1:]] for l in out.strip().splitlines()}
    assert len(table) == 64
    spec = {i["opcode"]: i for i in ISA["instructions"]}
    for opc, (legal, cls, *_rest) in table.items():
        if opc in spec:
            assert legal == 1, f"opcode {opc} ({spec[opc]['mnemonic']}) should be legal"
            assert cls == CLS[spec[opc]["class"]], f"class mismatch for {spec[opc]['mnemonic']}"
        else:
            assert legal == 0, f"opcode {opc} is not in the ISA but decodes as legal"
