import pytest
import tfxasm


def asm(src):
    return tfxasm.assemble(src)[0]


def test_encodings():
    w = asm("add r1, r2, r3\naddi r4, r5, -1\nlw r6, 8(r7)\nsw r8, -4(r9)\nlui r1, 0xBEEF\ns2r r2, global_tid\nexit")
    assert w[0] == (0 << 26) | (1 << 21) | (2 << 16) | (3 << 11)
    assert w[1] == (0x10 << 26) | (4 << 21) | (5 << 16) | 0xFFFF
    assert w[2] == (0x21 << 26) | (6 << 21) | (7 << 16) | 8
    assert w[3] == (0x22 << 26) | (8 << 21) | (9 << 16) | 0xFFFC
    assert w[4] == (0x20 << 26) | (1 << 21) | 0xBEEF
    assert w[5] == (0x24 << 26) | (2 << 21) | 2
    assert w[6] == 0x39 << 26


def test_structured_if_else_targets():
    w = asm("if r1\nnop\nelse\nnop\nendif\nexit")
    assert w[0] & 0xFFFF == 2          # IF -> ELSE
    assert w[2] & 0xFFFF == 2          # ELSE -> ENDIF


def test_labels_and_branches():
    w = asm("top:\nnop\nbany r1, top\njmp end\nnop\nend: exit")
    assert w[1] & 0xFFFF == 0xFFFF     # -1
    assert w[2] & 0xFFFF == 2


@pytest.mark.parametrize("v,n", [(5, 1), (-7, 1), (0x10000, 1), (0x12345678, 2), (0x7FFFFFFF, 2), (0x8000, 2)])
def test_li_sizes(v, n):
    assert len(asm(f"li r1, {v}")) == n


def test_word_directive():
    assert asm(".word 0xFC000000") == [0xFC000000]


@pytest.mark.parametrize("bad", ["foo r1", "add r1, r2", "addi r1, r2, 70000", "lw r1, r2", "endif", "if r1\nnop",
                                 "add r32, r1, r1", "s2r r1, nonsense", "x:\nx:\nnop"])
def test_errors(bad):
    with pytest.raises(tfxasm.AsmError):
        asm(bad)


def test_disassembler_roundtrip():
    src = "add r1, r2, r3\nmuli r4, r5, -9\nsw r1, 12(r2)\natom.add r3, r4, r5\nbar\nexit"
    for w in asm(src):
        text = tfxasm.disassemble(w)
        assert asm(text.split(";")[0]) == [w] or "if" in text or "else" in text
