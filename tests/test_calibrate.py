"""`ado calibrate` (P4-16): the plane 8 golden set, `emit` (rubric + unlabelled items) and `score` (agreement, false passes).

No model is called anywhere: a model's answer is just a string here."""
import json
import os
import shutil
from pathlib import Path

import pytest

from ado import calibrate
from ado.__main__ import main

ROOT = Path(__file__).resolve().parent.parent
LABELS = ("asserts", "partial", "does not assert")
ITEMS = calibrate.load_items()
TRUTH = {i["id"]: i["label"] for i in ITEMS}


def _answer(**changes):
    return json.dumps({**TRUTH, **changes})


def _rubric(md):
    start = md.index("**Plane 8 — AC fidelity**")
    return md[start:md.index("\nPlane 8 writes verdicts")].rstrip("\n")


def test_emit_copies_rubric_verbatim():
    md = (ROOT / "commands/review.md").read_bytes().decode("utf-8")
    out = calibrate.emit(md, ITEMS)
    assert _rubric(md) in out
    assert "- AC-<n>: asserts|partial|does not assert" in out
    odd = "intro\n\n**Plane 8 — AC fidelity** (x)\n\n> line  one é \r\n> two\t\n\n\ntext after\n"  # trailing blanks, tabs, CRLF, NBSP are kept
    assert "**Plane 8 — AC fidelity** (x)\n\n> line  one é \r\n> two\t\n" in calibrate.emit(odd, ITEMS)
    with pytest.raises(ValueError):
        calibrate.emit("no rubric here", ITEMS)


def test_emit_has_no_labels():
    md = (ROOT / "commands/review.md").read_text(encoding="utf-8")
    swapped = [{**i, "label": LABELS[(LABELS.index(i["label"]) + 1) % 3]} for i in ITEMS]
    out = calibrate.emit(md, ITEMS)
    assert out == calibrate.emit(md, swapped)  # the label never reaches the output
    body = out[out.index("## Items"):]
    assert '"label"' not in body and all(i["id"] in body and i["ac"] in body and i["test"] in body for i in ITEMS)
    assert "Strict mode is on." in out and "answer only with the JSON object" in out
    assert '{"G1": "...", "G2": "...", ' in out and '"G12": "..."}' in out
    for lab in LABELS:
        assert lab in out.split("## Items")[0]  # the answer format names the three values


def test_score_threshold_and_false_pass():
    r = calibrate.score(ITEMS, _answer())
    assert (r["agreement"], r["false_pass"], r["ok"]) == (12, 0, True)
    assert set(r) == {"agreement", "false_pass", "soft_pass", "confusion", "ok"} and r["soft_pass"] == 0
    assert r["confusion"]["asserts"]["asserts"] == 4 and sum(sum(v.values()) for v in r["confusion"].values()) == 12
    two = calibrate.score(ITEMS, _answer(G2="partial", G7="asserts"))  # 10 of 12, no false pass: the boundary passes
    assert (two["agreement"], two["false_pass"], two["ok"]) == (10, 0, True)
    three = calibrate.score(ITEMS, _answer(G2="partial", G7="asserts", G4="partial"))
    assert (three["agreement"], three["ok"]) == (9, False)
    fp = calibrate.score(ITEMS, _answer(G1="asserts"))  # 11 of 12 but a `does not assert` judged `asserts`
    assert (fp["agreement"], fp["false_pass"], fp["ok"]) == (11, 1, False)
    assert fp["confusion"]["does not assert"]["asserts"] == 1
    soft = calibrate.score(ITEMS, _answer(G1="partial"))  # a miss in the safe direction is not a false pass
    assert (soft["agreement"], soft["false_pass"], soft["soft_pass"], soft["ok"]) == (11, 0, 1, True)  # informational: ok is unchanged
    assert calibrate.score(ITEMS, _answer(G1="partial", G5="partial", G9="partial"))["soft_pass"] == 3
    assert calibrate.score(ITEMS, _answer(G2="partial"))["soft_pass"] == 0  # only a `does not assert` pair counts
    assert calibrate.score(ITEMS, "Here you go:\n```json\n" + _answer() + "\n```\nDone {not json}")["ok"] is True
    assert calibrate.score(ITEMS, "x" * 10 + " " + _answer() + " trailing")["ok"] is True  # the first `{` starts the object


@pytest.mark.parametrize("text", [
    json.dumps({**TRUTH, "G13": "asserts"}),  # unknown id
    json.dumps({**TRUTH, "G1": "ASSERTS"}),  # not one of the three values
    json.dumps({**TRUTH, "G1": "asserts - looks fine"}),  # an extra value
    json.dumps({**TRUTH, "G1": ["asserts"]}),
    json.dumps({**TRUTH, "G1": None}),
    json.dumps({k: v for k, v in TRUTH.items() if k != "G5"}),  # a missing id is refused, not scored
    '{"G1": "asserts", "G1": "partial", ' + ", ".join(f'"{k}": "{v}"' for k, v in TRUTH.items() if k != "G1") + "}",  # a duplicate id
    "",
    "no json at all",
    "[1, 2] and no object",
    '{"a":' * 10000,  # ~50 KB: under the cap, so it reaches the recursion handler
    _answer()[:-1],  # truncated
    "x" * (64 * 1024 + 1),  # over 64 KiB
    " " * (64 * 1024) + _answer(),
])
def test_score_rejects_unknown_id_extra_value_oversize(text):
    with pytest.raises(ValueError) as e:
        calibrate.score(ITEMS, text)
    assert 0 < len(str(e.value)) < 120  # a short fixed text


def test_deep_nesting_under_the_cap_is_refused_by_the_recursion_handler():
    text = '{"a":' * 10000
    assert len(text) < 64 * 1024
    with pytest.raises(ValueError, match="nested too deep"):
        calibrate.parse(text)


def test_first_object_wins_and_a_stray_brace_in_prose_is_refused():
    good, bad = _answer(), _answer(G1="asserts", G2="partial", G3="asserts")
    assert calibrate.score(ITEMS, good + "\nand again: " + bad)["ok"] is True  # trailing objects are ignored
    assert calibrate.score(ITEMS, bad + "\n" + good)["ok"] is False  # the decoy first: only it is judged
    for prose in ('Format: {"G1": "...", "G2": "..."}\n', "I will answer in {JSON}:\n"):  # a `{` before the answer starts the (wrong) object
        with pytest.raises(ValueError):
            calibrate.score(ITEMS, prose + good)


def test_score_errors_never_echo_the_answer():
    for text in (json.dumps({**TRUTH, "G1": "ZZ-MODEL-TEXT", "ZZ-ID": "partial"}), "ZZ-MODEL-TEXT {oops", '{"G1": "asserts", "G1": "ZZ-MODEL-TEXT"}'):
        with pytest.raises(ValueError) as e:
            calibrate.score(ITEMS, text)
        assert "ZZ-" not in str(e.value)


def test_a_score_of_exactly_64_kib_is_read():
    pad = " " * (64 * 1024 - len(_answer().encode("utf-8")))
    assert calibrate.score(ITEMS, _answer() + pad)["ok"] is True


def test_the_golden_set_is_package_data_with_one_source_of_truth():
    assert calibrate.GOLDEN == ROOT / "src/ado/golden/fidelity.json" and calibrate.GOLDEN.is_file()
    assert not (ROOT / "tests/golden/fidelity.json").exists()  # one copy only
    import importlib.resources as res
    assert res.files("ado").joinpath("golden/fidelity.json").is_file()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"golden/*.json"' in pyproject.split("[tool.setuptools.package-data]")[1]


def test_golden_set_balanced():
    raw = json.loads((ROOT / "src/ado/golden/fidelity.json").read_text(encoding="utf-8"))
    assert [i["id"] for i in raw] == [f"G{n}" for n in range(1, 13)]
    assert all(set(i) == {"id", "ac", "test", "lang", "label"} for i in raw)
    assert sorted(i["label"] for i in raw) == sorted(LABELS * 4) and {i["label"] for i in raw} == set(LABELS)
    assert {i["lang"] for i in raw} == {"Python", "JavaScript", "Apex"}
    for lab in LABELS:  # every language appears under every label
        assert {i["lang"] for i in raw if i["label"] == lab} == {"Python", "JavaScript", "Apex"}
    assert all(i["ac"].strip() and i["test"].strip() and len(i["test"]) < 800 for i in raw)
    text = json.dumps(raw)
    assert "contoso" in text.lower() or "fabrikam" in text.lower()
    assert calibrate.load_items() == raw


def test_load_items_refuses_a_bad_set(tmp_path):
    good = json.loads((ROOT / "src/ado/golden/fidelity.json").read_text(encoding="utf-8"))
    for bad in (good[:11], good + [good[0]], [{**good[0], "label": "maybe"}] + good[1:], [{**good[0], "extra": 1}] + good[1:], {"a": 1}):
        p = tmp_path / "g.json"
        p.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValueError):
            calibrate.load_items(p)
    with pytest.raises(ValueError):
        calibrate.load_items(tmp_path / "missing.json")


# ---- the CLI ----
@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _source_checkout(proj):
    (proj / "commands").mkdir()
    shutil.copy(ROOT / "commands/review.md", proj / "commands/review.md")
    (proj / "extension.yml").write_text('extension:\n  id: "ado"\n', encoding="utf-8")


def test_emit_from_a_source_checkout(proj, capsys):
    _source_checkout(proj)
    assert main(["calibrate", "--emit", ".ado/calibrate.md"]) == 0
    got = (proj / ".ado/calibrate.md").read_text(encoding="utf-8")
    assert got == calibrate.emit((ROOT / "commands/review.md").read_text(encoding="utf-8"), ITEMS)
    assert json.loads(capsys.readouterr().out)["items"] == 12
    assert [p.name for p in (proj / ".ado").iterdir()] == ["calibrate.md"]  # no temp file left behind


def test_emit_from_the_installed_extension(proj):
    inst = proj / ".specify/extensions/ado/commands"
    inst.mkdir(parents=True)
    (inst / "review.md").write_bytes((ROOT / "commands/review.md").read_bytes())
    assert main(["calibrate", "--emit", ".ado/c.md"]) == 0
    assert _rubric((ROOT / "commands/review.md").read_text(encoding="utf-8")) in (proj / ".ado/c.md").read_text(encoding="utf-8")


def test_emit_without_a_review_file_is_refused(proj, capsys):
    assert main(["calibrate", "--emit", ".ado/c.md"]) == 2
    err = capsys.readouterr().err
    assert "commands/review.md not found" in err and ".specify/extensions/ado/commands/review.md" in err and "root" in err and not (proj / ".ado").exists()


def test_emit_refuses_a_path_outside_ado_and_symlinks(proj, capsys):
    _source_checkout(proj)
    for bad in ("calibrate.md", "../x.md", ".ado/../calibrate.md", str(proj / "out.md"), ".ado/calibrate.txt"):
        assert main(["calibrate", "--emit", bad]) == 2, bad
    assert not (proj / "calibrate.md").exists() and not (proj / "out.md").exists()
    (proj / ".ado").mkdir()
    (proj / "target.md").write_text("keep", encoding="utf-8")
    os.symlink(proj / "target.md", proj / ".ado/link.md")
    os.symlink(proj, proj / ".ado/dir")
    assert main(["calibrate", "--emit", ".ado/link.md"]) == 2
    assert main(["calibrate", "--emit", ".ado/dir/x.md"]) == 2
    assert (proj / "target.md").read_text(encoding="utf-8") == "keep"
    capsys.readouterr()


def test_emit_and_score_refuse_a_symlink_that_points_back_inside_ado(proj, capsys):
    _source_checkout(proj)
    (proj / ".ado/real").mkdir(parents=True)
    (proj / ".ado/real/t.md").write_text("keep", encoding="utf-8")
    (proj / ".ado/real/a.json").write_text(_answer(), encoding="utf-8")
    os.symlink(proj / ".ado/real/t.md", proj / ".ado/link.md")
    os.symlink(proj / ".ado/real/a.json", proj / ".ado/link.json")
    os.symlink(proj / ".ado/real", proj / ".ado/linkdir")
    assert main(["calibrate", "--emit", ".ado/link.md"]) == 2
    assert main(["calibrate", "--emit", ".ado/linkdir/t.md"]) == 2
    assert main(["calibrate", "--score", ".ado/link.json"]) == 2
    assert main(["calibrate", "--score", ".ado/linkdir/a.json"]) == 2
    assert (proj / ".ado/real/t.md").read_text(encoding="utf-8") == "keep"
    assert main(["calibrate", "--score", ".ado/real/a.json"]) == 0  # the real path is fine
    capsys.readouterr()


def test_a_failed_emit_leaves_the_old_file_and_no_temp_file(proj, monkeypatch, capsys):
    _source_checkout(proj)
    (proj / ".ado").mkdir()
    (proj / ".ado/c.md").write_text("old", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(os, "replace", boom)
    assert main(["calibrate", "--emit", ".ado/c.md"]) == 2
    assert (proj / ".ado/c.md").read_text(encoding="utf-8") == "old" and [p.name for p in (proj / ".ado").iterdir()] == ["c.md"]
    assert "cannot be written" in capsys.readouterr().err


def test_emit_overwrites_atomically(proj):
    _source_checkout(proj)
    (proj / ".ado").mkdir()
    (proj / ".ado/c.md").write_text("old", encoding="utf-8")
    assert main(["calibrate", "--emit", ".ado/c.md"]) == 0
    assert (proj / ".ado/c.md").read_text(encoding="utf-8").startswith("# ")


def _write(proj, name, text, mode="w"):
    (proj / ".ado").mkdir(exist_ok=True)
    (proj / ".ado" / name).write_text(text, encoding="utf-8") if mode == "w" else (proj / ".ado" / name).write_bytes(text)
    return f".ado/{name}"


def test_score_exit_codes(proj, capsys):
    assert main(["calibrate", "--score", _write(proj, "a.json", _answer())]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["calibrate", "--score", _write(proj, "b.json", _answer(G1="asserts"))]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["false_pass"] == 1 and out["ok"] is False
    assert main(["calibrate", "--score", _write(proj, "c.json", _answer(G1="partial", G2="partial", G3="asserts"))]) == 1
    capsys.readouterr()


def test_score_refusals_are_exit_2_with_fixed_text(proj, capsys):
    bad = {"unknown": _write(proj, "u.json", json.dumps({**TRUTH, "ZZ-X": "asserts"})), "junk": _write(proj, "j.json", "ZZ-MODEL {nope"),
           "big": _write(proj, "big.json", "x" * (64 * 1024 + 1)), "binary": _write(proj, "bin.json", b"\xff\xfe{", mode="wb")}
    for name, path in bad.items():
        assert main(["calibrate", "--score", path]) == 2, name
        assert "ZZ-" not in capsys.readouterr().err
    assert main(["calibrate", "--score", ".ado/missing.json"]) == 2
    assert main(["calibrate", "--score", "answer.json"]) == 2  # not under .ado/
    (proj / "answer.json").write_text(_answer(), encoding="utf-8")
    assert main(["calibrate", "--score", "answer.json"]) == 2
    os.symlink(proj / "answer.json", proj / ".ado/link.json")
    assert main(["calibrate", "--score", ".ado/link.json"]) == 2
    capsys.readouterr()


def test_exactly_one_of_emit_and_score(capsys):
    for argv in (["calibrate"], ["calibrate", "--emit", ".ado/a.md", "--score", ".ado/a.json"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2
