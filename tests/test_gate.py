import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ado.config import Config
from ado.dod import SECTION2, ac_hash, render_dod
from ado.gate import (DEFAULT_TESTS, check_commits, check_drift, check_traceability, discover_tests,
                      missing_evidence, parse_section3, render_summary, run_gate, scan_assertions,
                      test_present)
from tests.conftest import FakeClient

HDR = "| AC | acceptance criterion | tests | commit/PR | status |\n|---|---|---|---|---|\n"
LEDGER = f"""# DoD — US1001
{SECTION2}
## 3. AC → tests (traceability)
{HDR}| AC-1 | does X | FooTest.testX | abc123 | green |
| AC-2 | does Y | BarTest.testY, BarTest.testY2 |  | red |
## 4. Gate log
"""
APEX = "@IsTest private class FooTest { @IsTest static void testX(){ System.assert(true); } }"
SOURCES = {"FooTest.cls": APEX}


def _wi(ac):
    return {"id": 1001, "fields": {"Microsoft.VSTS.Common.AcceptanceCriteria": ac}}


def _ledger_for(ac):
    return render_dod(_wi(ac), "contoso", "Fabrikam", synced="2026-07-14")


# ---- ported graceful-degradation cases ----
def test_parse_section3_extracts_rows():
    assert parse_section3(LEDGER) == [
        {"ac": "AC-1", "tests": ["FooTest.testX"], "evidence": "abc123", "status": "green"},
        {"ac": "AC-2", "tests": ["BarTest.testY", "BarTest.testY2"], "evidence": "", "status": "red"},
    ]


def test_parse_section3_graceful_on_missing_or_placeholder():
    assert parse_section3("no section 3 here") == []
    row = f"{SECTION2}\n## 3. AC\n| AC-1 | x | _(test IDs, …)_ | _(commit / PR)_ | _(red→green)_ |"
    assert parse_section3(row) == [{"ac": "AC-1", "tests": [], "evidence": "", "status": ""}]


def test_check_traceability_maps_and_flags():
    r = check_traceability(parse_section3(LEDGER), SOURCES)
    assert r["evaluated"] is True
    assert r["mapped"] == ["AC-1"]
    assert r["unmapped"] == ["AC-2"]


def test_check_traceability_graceful_when_no_rows():
    r = check_traceability([], SOURCES)
    assert r["evaluated"] is False
    assert "insufficient" in r["note"].lower()
    assert r["unmapped"] == []


def test_run_gate_advisory_by_default_even_with_gaps():
    r = run_gate(LEDGER, SOURCES, {"BTest.cls": "@IsTest class BTest{ @IsTest static void m(){} }"})
    assert r["traceability"]["unmapped"] == ["AC-2"]
    assert r["assertion_gaps"] == [{"file": "BTest.cls", "issue": "no assertions"}]
    assert r["blocking"] is False


def test_run_gate_blocks_only_opted_in_present_failure():
    assert run_gate(LEDGER, SOURCES, {}, block=["ac_traceability"])["blocking"] is True


def test_run_gate_untouched_template_blocks_only_on_traceability():
    ledger = _ledger_for("x")
    assert run_gate(ledger, {}, {}, block=["ac_traceability"])["blocking"] is True  # AC-1 has no test
    r = run_gate(ledger, {}, {}, block=["assertion", "ac_drift"])
    assert r["blocking"] is False
    assert r["missing_evidence"] == []      # placeholder status "_(red→green)_" is not green


def test_run_gate_blocks_only_named_missing_test():
    ledger = f"{SECTION2}\n## 3. AC\n{HDR}| AC-1 | x | Gone.testX | | red |\n"
    assert run_gate(ledger, {}, {}, block=["ac_traceability"])["blocking"] is True
    assert run_gate(ledger, {"Gone.cls": "class Gone { testX }"}, {}, block=["ac_traceability"])["blocking"] is False


# ---- test_present ----
def test_trace_pytest_id():
    assert test_present("tests/test_calc.py::test_add", {"tests/test_calc.py": "def test_add(): assert 1"})
    assert not test_present("tests/test_calc.py::test_sub", {"tests/test_calc.py": "def test_add(): assert 1"})
    assert not test_present("tests/test_calc.py::test_add", {"tests/other.py": "def test_add(): assert 1"})


def test_trace_jest_describe_it():
    src = {"cart.test.ts": "describe('Cart', () => { it('adds item', () => { expect(1) }) })"}
    assert test_present("Cart > adds item", src)
    assert not test_present("Cart > removes item", src)


def test_trace_apex_class_method():
    assert test_present("FooTest.testX", SOURCES)
    assert not test_present("FooTest.testZ", SOURCES)


def test_segments_must_share_a_file():
    assert not test_present("Foo.testX", {"a.txt": "Foo", "b.txt": "testX"})
    assert not test_present("", SOURCES)


# ---- scan_assertions ----
def test_unknown_language_changed_file_ignored():
    assert scan_assertions({"notes.txt": "nothing", "src/app.py": "x = 1"}, DEFAULT_TESTS) == []


@pytest.mark.parametrize("path", [
    "tests/test_a.py", "web/cart.test.ts", "src/CartTest.java",
    "src/CartTests.cs", "pkg/cart_test.go", "classes/CartTest.cls",
])
def test_no_assertions_flagged_per_language(path):
    assert scan_assertions({path: "// nothing checked here\n"}, DEFAULT_TESTS) == [
        {"file": path, "issue": "no assertions"}]


@pytest.mark.parametrize("path,text", [
    ("tests/test_a.py", "assert 1"), ("web/cart.test.ts", "expect(1)"),
    ("src/CartTest.java", "assertEquals(1,1)"), ("src/CartTests.cs", "Assert.Equal(1,1)"),
    ("pkg/cart_test.go", "t.Error(1)"), ("classes/CartTest.cls", "System.assert(true)"),
])
def test_assertions_present_not_flagged(path, text):
    assert scan_assertions({path: text}, DEFAULT_TESTS) == []


def test_discover_tests_globs_and_skips_unreadable(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("assert 1")
    (tmp_path / "test_top.py").write_text("assert 2")
    (tmp_path / "tests" / "test_bad.py").write_bytes(b"\xff\xfe\x00bad")
    (tmp_path / "app.py").write_text("x")
    found = discover_tests(str(tmp_path), DEFAULT_TESTS)
    assert sorted(p.rsplit("/", 1)[-1] for p in found) == ["test_a.py", "test_top.py"]


# ---- SEC3: file confinement (a PR must not use gate.tests regexes as a content oracle) ----
def _sandbox(tmp_path):
    src, outside = tmp_path / "src", tmp_path / "outside"
    src.mkdir()
    outside.mkdir()
    return src, outside


def test_discover_tests_never_reads_symlinks_or_outside_files(tmp_path):
    src, outside = _sandbox(tmp_path)
    (outside / "secret.txt").write_text("ALPHA")
    (src / "real.py").write_text("assert 1")
    (src / "inside_target.txt").write_text("assert 2")
    (src / "test_out.py").symlink_to(outside / "secret.txt")  # leaves --src
    (src / "test_in.py").symlink_to(src / "inside_target.txt")  # stays inside, still skipped
    (src / "linkdir").symlink_to(outside, target_is_directory=True)
    (outside / "test_viadir.py").write_text("assert 3")
    skipped = set()
    found = discover_tests(str(src), {"**/*.py": "assert"}, skipped)
    assert [p.rsplit("/", 1)[-1] for p in found] == ["real.py"]
    assert len(skipped) == 2  # linkdir is never descended, so it is not even a candidate


def test_discover_tests_skips_fifo_and_oversize_without_blocking(tmp_path):
    import os
    from ado.gate import MAX_READ
    src, _ = _sandbox(tmp_path)
    os.mkfifo(src / "test_fifo.py")  # open() on it would hang without O_NONBLOCK
    (src / "test_big.py").write_text("assert 1\n" + "#" * MAX_READ)
    (src / "test_ok.py").write_text("assert 1")
    skipped = set()
    found = discover_tests(str(src), {"**/*.py": "assert"}, skipped)
    assert [p.rsplit("/", 1)[-1] for p in found] == ["test_ok.py"] and len(skipped) == 2


def test_read_returns_a_file_of_exactly_the_cap_and_rejects_one_byte_more(tmp_path):
    from ado.gate import MAX_READ, _read
    (tmp_path / "ok.py").write_text("a" * MAX_READ)
    (tmp_path / "big.py").write_text("a" * (MAX_READ + 1))
    skipped = set()
    assert _read(tmp_path / "ok.py", str(tmp_path), skipped) == "a" * MAX_READ and not skipped
    assert _read(tmp_path / "big.py", str(tmp_path), skipped) is None and len(skipped) == 1


def test_read_skips_a_path_through_a_symlinked_directory_that_leaves_src(tmp_path):
    from ado.gate import _read
    src, outside = _sandbox(tmp_path)
    (outside / "x.py").write_text("assert 1")
    (src / "d").symlink_to(outside, target_is_directory=True)
    skipped = set()
    assert _read(src / "d" / "x.py", str(src), skipped) is None and len(skipped) == 1


def test_read_tolerates_a_swapped_in_symlink_via_o_nofollow(tmp_path, monkeypatch):
    from ado import gate
    src, outside = _sandbox(tmp_path)
    (outside / "x.txt").write_text("ALPHA")
    (src / "t.py").symlink_to(outside / "x.txt")
    monkeypatch.setattr(gate.os.path, "islink", lambda p: False)  # the check lost the race: O_NOFOLLOW must still refuse
    skipped = set()
    assert gate._read(src / "t.py", str(src), skipped) is None and len(skipped) == 1


def test_oracle_probe_leaks_nothing(cfg, tmp_path, monkeypatch):
    """Two outside files starting A-M and N-Z, symlinked into the repo; regexes that would tell them apart."""
    from ado.__main__ import dispatch
    src, outside = _sandbox(tmp_path)
    (outside / "one").write_text("ALPHA")
    (outside / "two").write_text("ZULU")
    for n in ("one", "two"):
        (src / f"test_{n}.py").symlink_to(outside / n)
    (src / "DoD_7.md").write_text(_ledger_for("x"))
    cfg.dod_path = str(src)
    cfg.gate["tests"] = {"**/test_*.py": "[A-M]", "**/test_two.py": "[N-Z]"}
    monkeypatch.chdir(src)
    out = dispatch(FakeClient(cfg), _args(src=".", changed=["./test_one.py", "./test_two.py"], offline=True))
    assert out["assertion_gaps"] == [] and out["assertion_notes"] == []
    assert out["skipped_note"] == "2 file(s) skipped: symlink, outside --src, not a regular file, or over 1 MiB"
    text = render_summary(out)
    assert "Files skipped:" in text and not any(w in text for w in ("test_one.py", "test_two.py", str(outside), "ALPHA", "ZULU"))
    # a regular file with the same content is still judged
    (src / "test_real.py").write_text("ZZZ")
    out = dispatch(FakeClient(cfg), _args(src=".", changed=["./test_real.py"], offline=True))
    assert out["assertion_gaps"] == [{"file": "./test_real.py", "issue": "no assertions"}]


def test_changed_path_outside_src_is_not_read(cfg, tmp_path):
    from ado.__main__ import dispatch
    src, outside = _sandbox(tmp_path)
    (outside / "test_x.py").write_text("no asserts here")
    (src / "DoD_7.md").write_text(_ledger_for("x"))
    cfg.dod_path = str(src)
    out = dispatch(FakeClient(cfg), _args(src=str(src), changed=[str(outside / "test_x.py")], offline=True))
    assert out["assertion_gaps"] == [] and "1 file(s) skipped" in out["skipped_note"]


def test_symlinked_parent_counts_a_missing_and_an_existing_target_alike(tmp_path):
    from ado.gate import _read
    src, outside = _sandbox(tmp_path)
    (outside / "secret").write_text("ALPHA")
    (src / "dlink").symlink_to(outside, target_is_directory=True)
    a, b = set(), set()
    assert _read(src / "dlink" / "secret", str(src), a) is None
    assert _read(src / "dlink" / "nope", str(src), b) is None
    assert len(a) == len(b) == 1
    c = set()  # a missing file that really is under --src stays uncounted, as before
    assert _read(src / "gone.py", str(src), c) is None and not c


def test_changed_through_a_symlinked_parent_gives_the_same_note_for_existing_and_missing(cfg, tmp_path):
    from ado.__main__ import dispatch
    src, outside = _sandbox(tmp_path)
    (outside / "secret").write_text("ALPHA")
    (src / "dlink").symlink_to(outside, target_is_directory=True)
    (src / "DoD_7.md").write_text(_ledger_for("x"))
    cfg.dod_path = str(src)
    notes = [dispatch(FakeClient(cfg), _args(src=str(src), changed=[str(src / "dlink" / n)], offline=True))["skipped_note"]
             for n in ("secret", "nope")]
    assert notes[0] == notes[1] and notes[0].startswith("1 file(s) skipped")


def _ledger_cases(tmp_path):
    import os
    src, outside = _sandbox(tmp_path)
    (outside / "ledger.md").write_text(LEDGER)  # ledger-shaped content outside --src
    os.mkfifo(src / "fifo.md")
    return src, {"outside-symlink": outside / "ledger.md", "dev-zero": Path("/dev/zero"), "fifo": src / "fifo.md"}


@pytest.mark.parametrize("kind", ["outside-symlink", "dev-zero", "fifo"])
def test_gate_ledger_is_read_confined(cfg, tmp_path, kind):
    from ado.__main__ import dispatch
    src, targets = _ledger_cases(tmp_path)
    if kind != "fifo":
        (src / "DoD_7.md").symlink_to(targets[kind])
    else:
        (src / "DoD_7.md").unlink(missing_ok=True)
        (src / "fifo.md").rename(src / "DoD_7.md")
    cfg.dod_path = str(src)
    out = dispatch(FakeClient(cfg), _args(src=str(src), offline=True))  # a hang here would be the bug
    assert out["ledger"] == "unreadable" and out["blocking"] is False
    assert "unreadable" in out["note"] and "FooTest" not in str(out) and "AC-1" not in str(out)
    assert str(targets[kind]) not in out["note"] or kind == "fifo"  # the configured path may be named; a symlink target never


def test_gate_ledger_inside_src_still_reads(cfg, tmp_path):
    from ado.__main__ import dispatch
    (tmp_path / "DoD_7.md").write_text(LEDGER)
    cfg.dod_path = str(tmp_path)
    out = dispatch(FakeClient(cfg), _args(src=str(tmp_path), offline=True))
    assert "traceability" in out


# ---- drift / evidence ----
def test_drift_detected():
    ledger = _ledger_for("old criteria")
    assert check_drift(ledger, _wi("new criteria")) == {"evaluated": True, "drift": True}
    assert check_drift(ledger, _wi("old criteria"))["drift"] is False


def test_drift_not_evaluated_without_hash():
    assert check_drift("no header", _wi("x"))["evaluated"] is False
    assert check_drift(_ledger_for("x"), None)["evaluated"] is False


def test_missing_evidence():
    assert missing_evidence(parse_section3(f"{SECTION2}\n## 3.\n{HDR}| AC-1 | a | T.x |  | green |\n"
                                           "| AC-2 | b | T.y | pr-1 | green |\n"
                                           "| AC-3 | c | T.z |  | red |\n")) == ["AC-1"]


def test_block_ac_drift_sets_blocking():
    ledger = _ledger_for("old")
    assert run_gate(ledger, {}, {}, _wi("new"), block=["ac_drift"])["blocking"] is True
    assert run_gate(ledger, {}, {}, _wi("new"), block=[])["blocking"] is False
    assert run_gate(ledger, {}, {}, _wi("old"), block=["ac_drift"])["blocking"] is False


def test_missing_info_never_blocks():
    r = run_gate("", {}, {}, None, block=["ac_traceability", "assertion", "ac_drift"])
    assert r["blocking"] is False
    assert r["drift"]["evaluated"] is False


# ---- CLI ----
def _args(**kw):
    class Args:
        command, id, src, changed, offline = "gate", 7, ".", [], False
    for k, v in kw.items():
        setattr(Args, k, v)
    return Args()


def test_config_tests_override_glob(cfg, tmp_path):
    from ado.__main__ import dispatch
    (tmp_path / "DoD_7.md").write_text("## 3\n")
    f = tmp_path / "test_a.py"
    f.write_text("assert 1")
    cfg.dod_path = str(tmp_path)
    cfg.gate["tests"] = {"**/test_*.py": "MAGIC"}
    out = dispatch(FakeClient(cfg), _args(src=str(tmp_path), changed=[str(f)], offline=True))
    assert out["assertion_gaps"] == [{"file": str(f), "issue": "no assertions"}]


def test_gate_cli_offline_does_not_fetch(cfg, tmp_path):
    from ado.__main__ import dispatch
    (tmp_path / "DoD_7.md").write_text(_ledger_for("x"))
    cfg.dod_path = str(tmp_path)
    client = FakeClient(cfg)
    out = dispatch(client, _args(src=str(tmp_path), offline=True))
    assert client.calls == [] and out["drift"]["evaluated"] is False


def test_gate_cli_fetch_failure_degrades(cfg, tmp_path, monkeypatch):
    from ado import __main__ as m
    from ado.client import AdoError
    (tmp_path / "DoD_7.md").write_text(_ledger_for("x"))
    cfg.dod_path = str(tmp_path)
    cfg.gate["block"] = ["ac_drift"]

    def boom(client, wid):
        raise AdoError("down")
    monkeypatch.setattr(m.workitems, "get", boom)
    out = m.dispatch(FakeClient(cfg), _args(src=str(tmp_path)))
    assert out["drift"]["evaluated"] is False and out["blocking"] is False


def test_gate_cli_drift_blocks(cfg, tmp_path, monkeypatch):
    from ado import __main__ as m
    (tmp_path / "DoD_7.md").write_text(_ledger_for("old"))
    cfg.dod_path = str(tmp_path)
    cfg.gate["block"] = ["ac_drift"]
    monkeypatch.setattr(m.workitems, "get", lambda client, wid: _wi("new"))
    assert m.dispatch(FakeClient(cfg), _args(src=str(tmp_path)))["blocking"] is True


# ---- fix round 1 ----
def test_discover_tests_prunes_vendored_dirs(tmp_path):
    for d in ("tests", "node_modules/x", ".venv/lib/site", "a/.git", "build"):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "test_calc.py").write_text("def test_add(): assert 1")
    found = discover_tests(str(tmp_path), DEFAULT_TESTS)
    assert [p.replace(str(tmp_path), "") for p in found] == ["/tests/test_calc.py"]


def test_path_id_requires_path_boundary():
    src = {"tests/my_test_calc.py": "def test_add(): assert 1"}
    assert not test_present("test_calc.py::test_add", src)
    assert test_present("my_test_calc.py::test_add", src)
    assert test_present("tests/my_test_calc.py::test_add", src)
    assert test_present("test_calc.py::test_add", {"/abs/tests/test_calc.py": "def test_add(): pass"})
    assert test_present("test_calc.py::test_add", {"test_calc.py": "def test_add(): pass"})


def test_run_gate_graceful_never_blocks_info_poor():
    r = run_gate("no section 3", {}, {}, block=["ac_traceability", "assertion", "ac_drift"])
    assert r["traceability"]["evaluated"] is False
    assert r["blocking"] is False
    assert scan_assertions({}, DEFAULT_TESTS) == []


# ---- fix round 2 ----
@pytest.mark.parametrize("pattern,path,ok", [
    ("tests/**/test_*.py", "tests/test_x.py", True),
    ("tests/**/test_*.py", "tests/a/b/test_x.py", True),
    ("tests/**/test_*.py", "tests/a/b/x.py", False),
    ("src/**/*.spec.ts", "src/a/b/x.spec.ts", True),
    ("src/**/*.spec.ts", "lib/x.spec.ts", False),
    ("**/*.test.[jt]s", "a.test.js", True),
    ("**/*.test.[jt]s", "d/a.test.ts", True),
    ("**/*.test.[jt]s", "d/a.test.py", False),
    ("**/test_*.py", "/abs/proj/tests/test_x.py", True),
    ("tests/**", "tests/a/b.py", True),
    ("**/*Test.java", "src/AppTest.java.bak", False),
])
def test_glob_match(pattern, path, ok):
    from ado.gate import _glob_match
    assert _glob_match(path, pattern) is ok


def test_discover_tests_interior_doublestar(tmp_path):
    for d in ("tests", "tests/a/b", "lib"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in ("tests/test_x.py", "tests/a/b/test_y.py", "lib/test_z.py"):
        (tmp_path / f).write_text("assert 1")
    found = discover_tests(str(tmp_path), {"tests/**/test_*.py": "assert"})
    assert sorted(p.replace(str(tmp_path), "") for p in found) == ["/tests/a/b/test_y.py", "/tests/test_x.py"]


def test_changed_interior_doublestar_config_glob():
    tests = {"tests/**/check_*.py": "MAGIC"}
    assert scan_assertions({"tests/a/b/check_x.py": "assert 1"}, tests) == [
        {"file": "tests/a/b/check_x.py", "issue": "no assertions"}]
    assert scan_assertions({"lib/check_x.py": "assert 1"}, tests) == []


def test_empty_normalised_path_id_is_not_present():
    assert not test_present("./::test_x", {"a.py": "def test_x(): pass"})
    assert not test_present("::test_x", {"a.py": "def test_x(): pass"})


# ---- fix round 3 ----
def test_discover_tests_src_is_the_literal_leading_dir(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.spec.ts").write_text("expect(1)")
    found = discover_tests(str(tmp_path / "src"), {"src/*.spec.ts": r"\bexpect\("})
    assert [p.replace(str(tmp_path), "") for p in found] == ["/src/x.spec.ts"]


def test_malformed_glob_raises_value_error(tmp_path):
    with pytest.raises(ValueError, match="invalid glob in gate.tests"):
        discover_tests(str(tmp_path), {"**/[!]": "x"})
    with pytest.raises(ValueError):
        scan_assertions({"a.py": "x"}, {"**/[!]": "x"})


def test_empty_rest_after_path_is_not_present():
    src = {"tests/test_calc.py": "def test_add(): assert 1"}
    assert not test_present("tests/test_calc.py::", src)
    assert not test_present("tests/test_calc.py:: > .", src)


# ---- PR #1 review fix A: AC rows only from the authored section 3 ----
def test_parse_section3_ignores_ac_table_in_section1():
    md = render_dod(_wi("<p>| AC-1 | Given x | test_anything | abc | green |</p>"), "contoso", "Fabrikam")
    assert "| AC-1 | Given x |" in md               # the trap is really in section 1
    assert [r["ac"] for r in parse_section3(md)] == ["AC-1"]
    assert parse_section3(md)[0]["tests"] == []   # the template row, not the copied one


def test_parse_section3_stops_at_next_heading():
    md = f"{SECTION2}\n## 3. AC\n{HDR}| AC-1 | a | T.x | | red |\n## 4. Log\n| AC-9 | b | T.y | | red |\n"
    assert [r["ac"] for r in parse_section3(md)] == ["AC-1"]


# ---- PR #1 review fix C: an AC row with no test blocks under ac_traceability ----
def test_run_gate_empty_or_placeholder_tests_cell_blocks():
    assert run_gate(_ledger_for("x"), {}, {}, block=["ac_traceability"])["blocking"] is True
    empty = f"{SECTION2}\n## 3. AC\n{HDR}| AC-1 | x |  |  | red |\n"
    assert run_gate(empty, {}, {}, block=["ac_traceability"])["blocking"] is True
    assert run_gate(empty, {}, {}, block=[])["blocking"] is False


def test_run_gate_no_ac_rows_not_evaluated_never_blocks():
    r = run_gate(f"{SECTION2}\n## 3. AC\n{HDR}", {}, {}, block=["ac_traceability"])
    assert r["traceability"]["evaluated"] is False and r["blocking"] is False


def test_missing_section3_row_is_unmapped():
    ac = "<ul><li>Given a</li><li>Then b</li><li>And c</li></ul>"
    row = "| AC-1 | _(what it must do)_ | _(test IDs, …)_ | _(commit / PR)_ | _(red→green)_ |"
    def ledger(*ids):
        return _ledger_for(ac).replace(row, "\n".join(f"| {i} | x | FooTest.testX | | red |" for i in ids))
    assert run_gate(ledger("AC-1", "AC-2", "AC-3"), SOURCES, {})["traceability"]["unmapped"] == []
    t = run_gate(ledger("AC-1", "AC-2"), SOURCES, {}, block=["ac_traceability"])  # AC-3 row deleted
    assert t["traceability"]["unmapped"] == ["AC-3"] and "AC-3" in t["traceability"]["note"]
    assert t["blocking"] is True
    assert check_traceability([], SOURCES, ac_count=2)["unmapped"] == ["AC-1", "AC-2"]
    # a row beyond n is evaluated normally
    assert run_gate(ledger("AC-1", "AC-2", "AC-3", "AC-9"), SOURCES, {})["traceability"]["unmapped"] == []
    assert run_gate(ledger("AC-1", "AC-2", "AC-3").replace("AC-3 | x | FooTest.testX", "AC-9 | x | Gone.t"),
                    SOURCES, {})["traceability"]["unmapped"] == ["AC-9", "AC-3"]


def test_nested_block_html_ac_rows_pass_blocking_traceability():
    row = "| AC-1 | _(what it must do)_ | _(test IDs, …)_ | _(commit / PR)_ | _(red→green)_ |"
    for ac in ("<ul><li><div>Given a</div></li><li><div>Then b</div></li></ul>",
               "<ol><li><p>Given a</p></li><li><p>Then b</p></li></ol>"):
        md = _ledger_for(ac).replace(row, "| AC-1 | a | FooTest.testX | | red |\n| AC-2 | b | FooTest.testX | | red |")
        r = run_gate(md, SOURCES, {}, block=["ac_traceability"])
        assert r["traceability"]["unmapped"] == [] and r["blocking"] is False


# ---- PR #1 review fix B: test ids match whole identifiers, not substrings ----
def test_test_id_segments_match_whole_identifiers():
    src = {"tests/test_cart.py": "def test_add_many(): assert 1\nclass LoginTest:\n def testLoginFails(): pass"}
    assert not test_present("tests/test_cart.py::test_add", src)
    assert not test_present("test_add", src)
    assert not test_present("LoginTest.testLogin", src)
    assert test_present("tests/test_cart.py::test_add_many", src)
    assert test_present("LoginTest.testLoginFails", src)


# ---- fix round 2: an inline marker mention in section 4 must not hide section 3 ----
def test_inline_marker_in_section4_does_not_hide_section3():
    md = (f"{SECTION2}\n## 3. AC\n{HDR}| AC-1 | x | Gone.testX | | red |\n"
          f"## 4. Gate & review log\n- reviewer: re-read `{SECTION2}` before merging\n")
    assert [r["ac"] for r in parse_section3(md)] == ["AC-1"]
    assert run_gate(md, {}, {}, block=["ac_traceability"])["blocking"] is True


# ---- H5: render_summary / render_error drive the CI comment ----
def test_summary_blocking_lists_blocking_findings():
    from ado.gate import render_summary
    md = render_summary(run_gate(LEDGER, SOURCES, {}, block=["ac_traceability"]))
    blocking, _, advisory = md.partition("**Advisory")
    assert "**Blocking" in blocking and "`AC-2`" in blocking
    assert "`AC-2`" not in advisory


def test_summary_advisory_only():
    from ado.gate import render_summary
    r = run_gate(LEDGER.replace("abc123", ""), SOURCES, {"BTest.cls": "@IsTest class BTest{}"})
    md = render_summary(r)
    assert "**Blocking" not in md
    assert "`AC-2`" in md and "`BTest.cls`" in md and "`AC-1`" in md  # unmapped, gap, no evidence
    assert "drift not checked" in md


def test_summary_all_clean():
    from ado.gate import render_summary
    clean = f"{SECTION2}\n## 3. AC\n{HDR}| AC-1 | x | FooTest.testX | abc | green |\n"
    md = render_summary(run_gate(clean, SOURCES, {}))
    assert "**Blocking" not in md and "**Advisory" not in md and "No findings" in md


@pytest.mark.parametrize("now,block,line", [
    ("new", [], "AC changed in ADO since the ledger was seeded"),
    ("old", [], "no drift"),
    (None, [], "drift not checked"),
])
def test_summary_drift_line(now, block, line):
    from ado.gate import render_summary
    md = render_summary(run_gate(_ledger_for("old"), {}, {}, now and _wi(now), block=block))
    assert line in md


def test_summary_drift_blocking_is_listed_as_blocking():
    from ado.gate import render_summary
    md = render_summary(run_gate(_ledger_for("old"), {}, {}, _wi("new"), block=["ac_drift"]))
    assert "AC changed in ADO" in md.partition("**Advisory")[0].partition("**Blocking")[2]


def test_summary_missing_ledger_is_advisory_note():
    from ado.gate import render_summary
    md = render_summary({"ledger": "missing", "blocking": False, "note": "no ledger at dod/DoD_7.md"})
    assert "no ledger" in md and "**Blocking" not in md


def test_summary_untrusted_text_is_one_capped_code_span():
    from ado.gate import render_summary
    evil = "x`y\n## pwn | z" + "a" * 500
    r = run_gate("", {}, {}) | {"assertion_gaps": [{"file": evil, "issue": "no assertions"}]}
    md = render_summary(r)
    (line,) = [ln for ln in md.splitlines() if "pwn" in ln]
    assert line.count("`") == 2 and "## pwn | z" in line
    assert "\n## pwn" not in md and len(line) < 300


def test_render_error_redacts_token(monkeypatch):
    from ado.gate import render_error
    monkeypatch.setenv("ADO_TOKEN", "tok-1001-secret")
    md = render_error("401 for tok-1001-secret\nat https://dev.azure.com/contoso")
    assert "tok-1001-secret" not in md and "could not run" in md and "***" in md


# ---- F14: per-test assertion check (Python via ast) ----
def _ledger_tests(*ids):
    return f"{SECTION2}\n## 3. AC → tests (traceability)\n{HDR}| AC-1 | a | {', '.join(ids)} |  | red |\n"


def _gate_py(src, *ids, path="tests/test_x.py"):
    return run_gate(_ledger_tests(*ids), {path: src}, {})


def test_assertion_free_mapped_test_flagged_in_asserting_file():
    src = "def test_x(): pass\n\ndef test_y():\n    assert 1\n"
    r = _gate_py(src, "tests/test_x.py::test_x", "tests/test_x.py::test_y")
    assert r["assertion_gaps"] == [{"test": "tests/test_x.py::test_x", "issue": "no assertions"}]


def test_class_method_form_checked_per_test():
    src = ("class TestA:\n    def test_m(self): pass\n"
           "class TestB:\n    def test_m(self): assert 1\n")
    r = _gate_py(src, "TestA.test_m", "tests/test_x.py::TestB::test_m")
    assert r["assertion_gaps"] == [{"test": "TestA.test_m", "issue": "no assertions"}]


@pytest.mark.parametrize("body", [
    "with pytest.raises(ValueError):\n        int('x')",
    "pytest.fail('no')",
    "self.assertEqual(1, 1)",
    "m.assert_called_once()",
    "if True:\n        assert 1",
    "with raises(ValueError):\n        int('x')",
    "assert_allclose(1, 1)",
    "assert_that(1).is_equal_to(1)",
    "with pt.raises(ValueError):\n        int('x')",
    "with pytest.warns(UserWarning):\n        pass",
    "self.fail('no')",
    "raise AssertionError('no')",
    "raise AssertionError",
    "self._chk()",
    "check(1)",
])
def test_assertion_forms_count(body):
    src = ("import pytest\nimport pytest as pt\nfrom pytest import raises\n"
           "def check(x):\n    assert x\n"
           f"class T:\n    def _chk(self):\n        self.assertTrue(1)\n\n    def test_a(self):\n        {body}\n")
    assert _gate_py(src, "T.test_a")["assertion_gaps"] == []


def test_helper_without_assertion_does_not_count():
    src = "def check(x): return x\ndef test_a(): check(1)\n"
    assert _gate_py(src, "test_a")["assertion_gaps"] == [{"test": "test_a", "issue": "no assertions"}]


def test_deeply_nested_source_notes_not_crash():
    r = _gate_py("def test_a(): x = " + "(" * 5000 + "1" + ")" * 5000, "test_a")
    assert r["assertion_gaps"] == [] and any("parse" in n for n in r["assertion_notes"])


def test_unparseable_python_notes_not_crash_or_block():
    r = run_gate(_ledger_tests("tests/test_x.py::test_a"), {"tests/test_x.py": "def test_a(:\n"}, {}, block=["assertion"])
    assert r["assertion_gaps"] == [] and r["blocking"] is False
    assert any("test_x.py" in n and "parse" in n for n in r["assertion_notes"])


def test_jest_mapped_test_is_note_only():
    src = "describe('Cart', () => { it('adds', () => { }); });"
    r = run_gate(_ledger_tests("Cart > adds"), {"web/cart.test.ts": src}, {}, block=["assertion"])
    assert r["assertion_gaps"] == [] and r["blocking"] is False
    assert any("Python only" in n for n in r["assertion_notes"])


def test_python_only_mapping_has_no_note():
    assert _gate_py("def test_x(): assert 1", "test_x")["assertion_notes"] == []


def test_summary_shows_per_test_gap_and_note():
    from ado.gate import render_summary
    r = _gate_py("def test_x(): pass", "test_x") | {"assertion_notes": ["per-test checking covers Python only"]}
    md = render_summary(r)
    assert "`test_x`" in md and "Python only" in md


def test_offline_drift_note():
    from ado.gate import render_summary
    led = _ledger_for("x")
    assert check_drift(led, None, "offline — drift not checked")["note"] == "offline — drift not checked"
    assert check_drift(led, None)["note"] == "ADO not reachable or no token — drift not checked"
    assert check_drift("no header", _wi("x"))["note"] == "no AC hash in the ledger — drift not checked"
    md = render_summary(run_gate(led, {}, {}, None, reason="offline — drift not checked"))
    assert "AC drift: offline — drift not checked" in md


# ---- commits_without_story (N5) ----
ROOT = Path(__file__).resolve().parent.parent


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo, msg, name="f"):
    (repo / name).write_text(msg)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "--short", "HEAD")


@pytest.fixture
def crepo(tmp_path, git_env):
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, "init")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(tmp_path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(tmp_path, "switch", "-q", "-c", "feature/ab2-x")
    return tmp_path


def test_commits_with_and_without_story_ref(crepo):
    ok = _commit(crepo, "feat: a\n\nbody AB#2", "a")
    bad = _commit(crepo, "feat: b", "b")
    other = _commit(crepo, "feat: c AB#3", "c")
    inword = _commit(crepo, "feat: d xAB#2", "d")
    longer = _commit(crepo, "feat: e AB#22", "e")
    subj = _commit(crepo, "feat: f AB#2", "f")
    got, note = check_commits(str(crepo), 2)
    assert note is None and ok not in got and subj not in got
    assert set(got) == {bad, other, inword, longer}


def test_merge_commits_ignored(crepo):
    _git(crepo, "switch", "-q", "-c", "side")
    _commit(crepo, "side work AB#2", "s")
    _git(crepo, "switch", "-q", "feature/ab2-x")
    _commit(crepo, "main work AB#2", "m")
    _git(crepo, "merge", "-q", "--no-ff", "-m", "Merge branch side", "side")
    assert check_commits(str(crepo), 2) == ([], None)


def test_no_base_is_a_note_not_a_block(tmp_path, git_env):
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, "no ref here")
    got, note = check_commits(str(tmp_path), 2)
    r = run_gate(LEDGER, SOURCES, {}, None, block=["commits_without_story"], commits=(got, note))
    assert not r["blocking"] and r["commits_note"] == note
    assert "Commit check: " in render_summary(r)
    assert got == [] and note == "no default branch (origin/HEAD) — commits not checked"
    assert check_commits(str(tmp_path / "nope"), 2)[1].startswith("git failed")


def test_commits_blocking_only_when_configured(crepo):
    bad = _commit(crepo, "feat: b")
    commits = check_commits(str(crepo), 2)
    assert commits == ([bad], None)
    adv = run_gate(LEDGER, SOURCES, {}, None, commits=commits)
    assert adv["commits_without_story"] == [bad] and not adv["blocking"]
    blk = run_gate(LEDGER, SOURCES, {}, None, block=["commits_without_story"], commits=commits)
    assert blk["blocking"] and blk["blocked_by"] == ["commits_without_story"]
    assert "Blocking" in render_summary(blk) and f"`{bad}`" in render_summary(blk)
    assert run_gate(LEDGER, SOURCES, {}, None)["commits_without_story"] == []


def test_explicit_base_gives_the_right_range(crepo):
    # PR targets a non-default branch `dev`: only commits after dev count, not those dev already holds
    old = _commit(crepo, "dev-only work, no ref", "o")
    _git(crepo, "branch", "dev")
    _git(crepo, "update-ref", "refs/remotes/origin/dev", "dev")
    bad = _commit(crepo, "feat: after dev", "p")
    assert check_commits(str(crepo), 2, "origin/dev") == ([bad], None)
    assert set(check_commits(str(crepo), 2)[0]) == {old, bad}  # default (origin/HEAD) range is wider


def test_bad_base_is_a_note_never_a_block(crepo):
    _commit(crepo, "feat: b")
    for base in ("--output=x", "-x"):
        got, note = check_commits(str(crepo), 2, base)
        assert got == [] and "not a ref" in note and note.endswith("commits not checked")
    assert not (crepo / "x").exists()
    assert check_commits(str(crepo), 2, "origin/nope") == ([], "base origin/nope not found — commits not checked")


def test_summary_caps_commit_list_at_20():
    shas = [f"{i:07x}" for i in range(25)]
    md = render_summary(run_gate(LEDGER, SOURCES, {}, None, commits=(shas, None)))
    assert "`0000013`" in md and "`0000014`" not in md and "…and 5 more" in md


@pytest.mark.parametrize("cmd", ["implement", "pr", "review", "gate"])
def test_command_commit_examples_carry_story_ref(cmd):
    text = (ROOT / "commands" / f"{cmd}.md").read_text(encoding="utf-8")
    assert re.search(r"git commit -m \"[^\"]*AB#<id>[^\"]*\"", text)


# ---- LEDGER5: gate inputs ignore hidden headings and fail closed on an open fence ----
def test_gate_inputs_ignore_a_fenced_section3_and_marker():
    led = (f"# DoD\n## 1. S\n### Acceptance Criteria\nreal AC\n\n{SECTION2}\n```\n## 3. AC\n{HDR}| AC-1 | x | FooTest.testX | | green |\n```\n"
           "## 3. AC\n" + HDR + "| AC-2 | y |  |  | red |\n")
    assert [r["ac"] for r in parse_section3(led)] == ["AC-2"]
    r = run_gate(led, SOURCES, {})
    assert "AC-1" in r["traceability"]["unmapped"]  # the fenced AC-1 row maps nothing


@pytest.mark.parametrize("opener", ["```", "~~~", "<!--"])
def test_gate_unclosed_opener_in_the_ledger_is_a_blocking_ledger_note(opener):
    from ado.gate import LEDGER_UNCLOSED_NOTE
    led = LEDGER + f"{opener}\n## 5. Definition of Done\n- [x] a\n"
    r = run_gate(led, SOURCES, {}, block=["open_questions"])
    assert r["ledger_unclosed"] == LEDGER_UNCLOSED_NOTE and r["open_questions"] == [] and "open_questions_note" not in r
    assert r["blocking"] and r["blocked_by"] == ["open_questions"]
    assert LEDGER_UNCLOSED_NOTE in render_summary(r) and "Blocking" in render_summary(r)
    advisory = run_gate(led, SOURCES, {})  # advises unless listed in gate.block
    assert not advisory["blocking"] and "Advisory" in render_summary(advisory)
    assert "ledger_unclosed" not in run_gate(LEDGER, SOURCES, {})


# ---- V3-5 / V3-5b: open_decisions, read from <dod_path>/Decisions_<id>.md ----
_D_OPEN = "- [ ] D1: Use cursor paging → _(open)_ · kind: decision · why: the story names no page size"
_D_VAL = "- [x] D2: Cap at 50 rows → validated · by: PO · note: agreed"
_D_REJ = "- [x] D3: Skip audit log → rejected · by: PO · note: not acceptable"
_D_OPEN10 = "- [ ] D10: Retry twice → _(open)_ · kind: assumption · why: unspecified"
V02_KEYS = {"traceability", "assertion_gaps", "assertion_notes", "drift", "missing_evidence", "open_questions",
            "commits_without_story", "blocked_by", "blocking"}  # the literal v0.2 advisory key set


def _dfile(*lines):
    from ado.decisions import render_file
    return render_file(1001) + "\n".join(lines) + "\n"


def _gd(*lines, **kw):
    return run_gate(LEDGER, SOURCES, {}, decisions_md=_dfile(*lines), **kw)


def test_open_decisions_absent_without_a_file_or_decision_lines():
    assert set(run_gate(LEDGER, SOURCES, {})) == V02_KEYS
    assert set(run_gate(_ledger_for("x"), {}, {})) == V02_KEYS
    assert set(_gd()) == V02_KEYS  # a fresh file: title and format note only
    assert "open_decisions" not in render_summary(_gd())
    # a D line inside the LEDGER is not read any more (V3-2..V3-5 unreleased form)
    old = LEDGER.replace("## 3. AC", _D_OPEN + "\n## 3. AC", 1)
    assert set(run_gate(old, SOURCES, {}, block=["open_decisions"])) == V02_KEYS


def test_open_decisions_lists_ids_and_rejected_counts_as_open():
    r = _gd(_D_OPEN10, _D_VAL, _D_REJ, _D_OPEN)
    assert r["open_decisions"] == ["D1", "D3", "D10"]  # numeric order; validated is resolved
    assert set(r) == V02_KEYS | {"open_decisions"}
    assert _gd(_D_VAL)["open_decisions"] == []  # present, nothing open
    assert _gd("- [ ] D4 not in the grammar")["open_decisions"] == ["?"]  # unparsed D line: fail closed


def test_open_decisions_blocks_only_when_listed():
    assert _gd(_D_OPEN)["blocking"] is False
    r = _gd(_D_OPEN, block=["open_decisions"])
    assert r["blocked_by"] == ["open_decisions"] and r["blocking"] is True
    assert _gd(_D_VAL, block=["open_decisions"])["blocking"] is False
    assert run_gate(LEDGER, SOURCES, {}, block=["open_decisions"])["blocking"] is False  # no file


def test_summary_line():
    open_md = render_summary(_gd(_D_OPEN, _D_REJ))
    assert "- Open decisions in the decisions file: `D1`, `D3`" in open_md.partition("**Advisory:**")[2]
    blocked = render_summary(_gd(_D_OPEN, block=["open_decisions"]))
    assert "`D1`" in blocked.partition("**Blocking")[2].partition("**Advisory")[0]
    assert "Open decisions: none" in render_summary(_gd(_D_VAL))


def test_open_decisions_fail_closed_on_malformed_hidden_or_unreadable():
    r = _gd("- [ ] D4 not in the grammar", block=["open_decisions"])
    assert r["open_decisions"] == ["?"] and r["blocked_by"] == ["open_decisions"] and r["blocking"] is True
    assert _gd("- [ ] D4 not in the grammar")["blocking"] is False  # advisory unless listed
    for hidden in (("```", _D_OPEN), ("<!--", _D_OPEN)):  # an unclosed fence or comment hides the D line
        r = _gd(*hidden, block=["open_decisions"])
        assert r["open_decisions"] == ["?"] and r["blocked_by"] == ["open_decisions"] and r["blocking"] is True
    r = run_gate(LEDGER, SOURCES, {}, decisions_unreadable=True, block=["open_decisions"])
    assert r["open_decisions"] == ["?"] and r["blocking"] is True
    assert run_gate(LEDGER, SOURCES, {}, decisions_unreadable=True)["blocking"] is False


def test_summary_open_decisions_fail_closed_lines():
    for lines in (("- [ ] D4 not in the grammar",), ("```", _D_OPEN)):
        md = render_summary(_gd(*lines, block=["open_decisions"]))
        blocking = md.partition("**Blocking")[2].partition("**Advisory")[0]
        assert "- Open decisions in the decisions file: `?`" in blocking
        assert "AC changed" not in blocking and "no drift" not in blocking and "drift not checked" not in blocking


def test_gate_reads_decisions_file_confined_and_key_absent_without_file(cfg, tmp_path):
    from ado.__main__ import dispatch
    dod = tmp_path / "dod"
    dod.mkdir()
    (dod / "DoD_7.md").write_text(_ledger_for("x"))
    cfg.dod_path = str(dod)
    cfg.gate["block"] = ["open_decisions"]
    run = lambda: dispatch(FakeClient(cfg), _args(src=str(tmp_path), offline=True))  # noqa: E731
    assert "open_decisions" not in run() and run()["blocking"] is False  # no file
    f = dod / "Decisions_7.md"
    f.write_text(_dfile(_D_OPEN, _D_VAL))
    out = run()
    assert out["open_decisions"] == ["D1"] and out["blocking"] is True and out["blocked_by"] == ["open_decisions"]
    f.write_text(_dfile(_D_VAL))
    out = run()
    assert out["open_decisions"] == [] and out["blocking"] is False
    f.write_bytes(b"\xff\xfe")  # unreadable text: fail closed
    assert run()["open_decisions"] == ["?"]
    f.unlink()
    outside = tmp_path.parent / (tmp_path.name + "-secret.md")
    outside.write_text(_dfile(_D_VAL))
    f.symlink_to(outside)  # a symlink is never followed, even to a harmless file: counted as unreadable
    out = run()
    assert out["open_decisions"] == ["?"] and out["blocking"] is True
    f.unlink()
    f.write_text(_dfile(_D_VAL) + "x" * (1 << 20))  # over MAX_READ
    assert run()["open_decisions"] == ["?"]
    f.write_text(_dfile(_D_VAL))
    assert run()["open_decisions"] == []


def test_open_decisions_use_the_effective_set_and_fail_closed_on_a_bad_supersedes():
    rej, answer = _D_REJ, "- [ ] D5: supersedes D3: answered → _(open)_ · kind: decision · why: w"
    assert _gd(rej)["open_decisions"] == ["D3"]
    assert _gd(rej, answer)["open_decisions"] == ["D5"]  # D3 is answered: only the new decision is open
    assert _gd(rej, answer, block=["open_decisions"])["blocking"] is True  # D5 itself is still open
    assert _gd(rej, "- [x] D5: supersedes D3: answered → validated · by: PO · note: ok")["open_decisions"] == []
    assert _gd(rej, "- [x] D5: supersedes D3: answered → rejected · by: PO · note: no")["open_decisions"] == ["D3", "D5"]
    bad = _gd("- [ ] D1: supersedes D2: x → _(open)_ · kind: decision · why: w", _D_REJ.replace("D3", "D2"), block=["open_decisions"])
    assert "?" in bad["open_decisions"] and bad["blocking"] is True  # a lower-id superseder is invalid: fail closed
    assert "?" in _gd("- [ ] D5: supersedes D9: x → _(open)_ · kind: decision · why: w")["open_decisions"]


# ---- P3-8: the `rule_coverage` check (local, from committed files); the JSON key exists only when rules are enabled ----
from ado import decisions, dod, rules  # noqa: E402

_AC3 = "<ul><li>earn points</li><li>gift cards earn nothing</li><li>show the balance</li></ul>"
_ROW0 = "| AC-1 | _(what it must do)_ | _(test IDs, …)_ | _(commit / PR)_ | _(red→green)_ |"


def _rules_md(wid, *specs, extra=""):
    """A rules file as `ado rules` writes it: (n, status) per rule, every label present (no warnings)."""
    html = "<p>Business rules</p>" + "".join(
        f"<p>Rule {n}: Customers earn {n} points.</p><p>Why: w</p><p>Example: e</p><p>Owner: Loyalty product owner</p><p>Status: {s}</p>"
        for n, s in specs) + extra
    lines, probs = rules.normalise(html)
    return rules.render_file({"id": wid, "fields": {"System.Title": "Points", "System.WorkItemType": "Feature"}},
                             rules.parse(lines, wid, probs), "contoso", "Fabrikam", "2026-11-03", "0.3.0")


F1201 = _rules_md(1201, (1, "approved"), (2, "approved"), (3, "draft"), (4, "retired"))
F1200 = _rules_md(1200, (1, "approved"))
H = {r: v["hash"] for md, w in ((F1201, 1201), (F1200, 1200)) for r, v in rules.parse_file(md, w)["rules"].items()}
ALL = f"BR-1200-1@{H['BR-1200-1']}, BR-1201-1@{H['BR-1201-1']}, BR-1201-2@{H['BR-1201-2']}"


def _files(**md):
    return {int(k[1:]): rules.parse_file(v, int(k[1:])) for k, v in md.items() if v is not None}


def _gl(*rows, frm=(1201,), reviewed="", ac=_AC3):
    """A ledger: three AC lines, the given section 3 rows, the header lines `Rules from:` (None: absent) and `Rules reviewed:`."""
    md = render_dod(_wi(ac), "contoso", "Fabrikam", synced="2026-07-14", rules_from=None if frm is None else list(frm), rules_reviewed=reviewed)
    assert _ROW0 in md
    return md.replace(_ROW0, "\n".join(rows))


ROWS = ("| AC-1 | earn points [BR-1201-1] | FooTest.testX | abc | green |",
        "| AC-2 | gift cards earn nothing [BR-1201-2] | FooTest.testX | | green |",
        "| AC-3 | show the balance | FooTest.testX | | green |")


def _cov(ledger, files=None, decisions_md="", phase="pr", sources=SOURCES):
    return rules.coverage(ledger, sources, _files(f1201=F1201, f1200=F1200) if files is None else files,
                          dod.ledger_rules_from(ledger), dod.ledger_rules_reviewed(ledger), decisions.parse_decisions(decisions_md), phase)


def _clean():
    return _gl(*ROWS, reviewed=ALL.replace(f", BR-1200-1@{H['BR-1200-1']}", "").replace(f"BR-1200-1@{H['BR-1200-1']}, ", ""))


def test_coverage_keys_and_a_clean_story():
    c = _cov(_clean())
    assert list(c) == ["in_scope", "covered", "excluded", "uncovered", "unknown", "unconfirmed", "changed", "problems", "missing", "format", "header"]
    assert c["in_scope"] == c["covered"] == ["BR-1201-1", "BR-1201-2"]  # the draft and the retired rule are never in scope
    assert not any(c[k] for k in ("excluded", "uncovered", "unknown", "unconfirmed", "changed", "problems", "missing", "format", "header"))


def test_uncovered_rule_untagged_or_tagged_on_an_ac_with_no_present_test():
    c = _cov(_gl(ROWS[0], "| AC-2 | gift cards earn nothing | FooTest.testX | | |", ROWS[2], reviewed=_clean().split("reviewed:** ")[1].split("\n")[0]))
    assert c["covered"] == ["BR-1201-1"] and c["uncovered"] == ["BR-1201-2"]
    gone = _cov(_gl(ROWS[0], "| AC-2 | gift cards [BR-1201-2] | Gone.test | | |", ROWS[2], reviewed=ALL.split(", BR-1201-2")[0] + f", BR-1201-2@{H['BR-1201-2']}"))
    assert gone["uncovered"] == ["BR-1201-2"]
    empty = _cov(_gl(ROWS[0], "| AC-2 | gift cards [BR-1201-2] |  | | |", ROWS[2]))
    assert "BR-1201-2" in empty["uncovered"]


def test_extra_ac_row_does_not_cover():
    c = _cov(_gl(ROWS[0], "| AC-99 | extra [BR-1201-2] | FooTest.testX | | |", ROWS[2], "| AC-0 | zero [BR-1201-2] | FooTest.testX | | |",
                 "| AC-4 | beyond the three [BR-1201-2] | FooTest.testX | | |", "| AC-01 | not AC-1 [BR-1201-2] | FooTest.testX | | |"))
    assert c["covered"] == ["BR-1201-1"] and c["uncovered"] == ["BR-1201-2"]
    assert not c["unknown"]  # a known id cited on a row that does not count is not an unknown id


def test_row_with_absent_test_does_not_cover_even_if_other_row_mapped():
    # AC-2 is mapped but carries no tag; the tag sits on AC-3 whose test does not exist
    c = _cov(_gl(ROWS[0], "| AC-2 | y | FooTest.testX | | |", "| AC-3 | z [BR-1201-2] | Gone.test | | |"))
    assert c["uncovered"] == ["BR-1201-2"]
    # a duplicate AC-1 row: the tag is on the row whose test is absent; the other AC-1 row is mapped. tags() merges the two rows, so any absent duplicate disqualifies
    dup = _cov(_gl("| AC-1 | a | FooTest.testX | | |", "| AC-1 | a [BR-1201-1] [BR-1201-2] | Gone.test | | |", "| AC-2 | b | FooTest.testX | | |"))
    assert dup["uncovered"] == ["BR-1201-1", "BR-1201-2"]
    # one of several tests present is enough for a row
    ok = _cov(_gl("| AC-1 | a [BR-1201-1] | Gone.test, FooTest.testX | | |", "| AC-2 | b [BR-1201-2] | FooTest.testX | | |"))
    assert ok["covered"] == ["BR-1201-1", "BR-1201-2"]


def test_rule_in_a_fence_or_comment_row_is_not_cited():
    led = _gl(ROWS[0], "```", "| AC-2 | hidden [BR-1201-2] | FooTest.testX | | |", "```", "<!--", "| AC-3 | c [BR-1201-2] | FooTest.testX | | |", "-->")
    assert _cov(led)["uncovered"] == ["BR-1201-2"]


def test_unknown_ids_are_ids_or_a_question_mark_never_the_token_text():
    c = _cov(_gl(ROWS[0], "| AC-2 | b [BR-1201-2] [BR-1201-9] [BR-777-1] [BR-1201-0] xBR-1-1y | FooTest.testX | | |", ROWS[2]))
    assert c["unknown"] == ["BR-777-1", "BR-1201-9", "?", "?"]  # ids by (work item, number), then one `?` per malformed token
    assert "BR-1201-0" not in repr(c) and "xBR" not in repr(c)
    # draft and retired rules exist in the file: citing them is not an unknown id, and not a coverage claim
    d = _cov(_gl(ROWS[0], ROWS[1], "| AC-3 | c [BR-1201-3] [BR-1201-4] | FooTest.testX | | |"))
    assert d["unknown"] == [] and d["in_scope"] == ["BR-1201-1", "BR-1201-2"]


def test_first_pull_is_all_unconfirmed_none_changed():
    for led in (_gl(*ROWS), _gl(*ROWS, reviewed="none")):  # no `Rules reviewed:` line at all, or an empty record: no confirmed hash
        c = _cov(led)
        assert c["unconfirmed"] == ["BR-1201-1", "BR-1201-2"] and c["changed"] == []


def test_a_recorded_different_hash_is_changed_only():
    c = _cov(_gl(*ROWS, reviewed=f"BR-1201-1@{H['BR-1201-1']}, BR-1201-2@deadbeef"))
    assert c["changed"] == ["BR-1201-2"] and c["unconfirmed"] == []


def test_a_rule_with_no_recorded_hash_is_unconfirmed_while_the_rest_is_confirmed():
    c = _cov(_gl(*ROWS, reviewed=f"BR-1201-1@{H['BR-1201-1']}"))
    assert c["unconfirmed"] == ["BR-1201-2"] and c["changed"] == []


def test_a_mix_of_both_keeps_the_two_lists_separate():
    c = _cov(_gl(*ROWS, frm=(1201, 1200), reviewed=f"BR-1201-1@deadbeef"), files=_files(f1201=F1201, f1200=F1200))
    assert c["changed"] == ["BR-1201-1"] and c["unconfirmed"] == ["BR-1200-1", "BR-1201-2"]
    assert not set(c["changed"]) & set(c["unconfirmed"])


def test_an_unrecorded_rule_whose_text_no_longer_matches_its_hash_is_changed_not_unconfirmed():
    # `--confirm` refuses such a rule (the file text is not what was reviewed), so it is a human's: never "free to confirm"
    edited = F1201.replace("- Rule: Customers earn 1 points.", "- Rule: Customers earn nothing.")
    assert edited != F1201
    c = _cov(_gl(*ROWS, reviewed=f"BR-1201-2@{H['BR-1201-2']}"), files=_files(f1201=edited))
    assert c["changed"] == ["BR-1201-1"] and c["unconfirmed"] == []
    down = F1201.replace(f"### BR-1201-2 · approved · {H['BR-1201-2']}", f"### BR-1201-2 · draft · {H['BR-1201-2']}")
    d = _cov(_gl(*ROWS), files=_files(f1201=down))
    assert d["changed"] == ["BR-1201-2"] and d["unconfirmed"] == ["BR-1201-1"]


def test_a_malformed_record_makes_nothing_free_to_confirm():
    # `--confirm` refuses a malformed `Rules reviewed:` line (a human repairs it), so an agent must not be told to run it
    bad = _gl(*ROWS, reviewed=ALL).replace("@" + H["BR-1201-1"], "@XYZ")
    c = _cov(bad)
    assert c["header"] == ["rules-reviewed"] and c["unconfirmed"] == [] and c["changed"] == ["BR-1201-1", "BR-1201-2"]


def test_an_excluded_or_draft_rule_without_a_record_is_neither_list():
    ex = decisions.render_file(1001) + _excl("BR-1201-2") + "\n"
    c = _cov(_gl(*ROWS, reviewed=f"BR-1201-1@{H['BR-1201-1']}"), decisions_md=ex)
    assert c["unconfirmed"] == [] and c["changed"] == []  # not in scope, nothing recorded: nothing to confirm
    assert "BR-1201-3" not in _cov(_gl(*ROWS))["unconfirmed"]


def test_changed_judges_every_recorded_id_that_is_still_approved_in_scope_or_not():
    ex = decisions.render_file(1001) + _excl("BR-1201-2") + "\n"
    led = _gl(*ROWS, reviewed=f"BR-1201-1@{H['BR-1201-1']}, BR-1201-2@deadbeef")
    c = _cov(led, decisions_md=ex)
    assert c["excluded"] == ["BR-1201-2"] and "BR-1201-2" not in c["in_scope"]
    assert c["changed"] == ["BR-1201-2"]  # excluded, but its recorded hash differs from the approved rule in the file
    ok = _cov(_gl(*ROWS, reviewed=f"BR-1201-1@{H['BR-1201-1']}, BR-1201-2@{H['BR-1201-2']}"), decisions_md=ex)
    assert ok["changed"] == []
    # a recorded id whose rule is a draft in the file now is not judged here (a status change is the live drift check's)
    drafted = _cov(_gl(*ROWS, reviewed=f"BR-1201-1@{H['BR-1201-1']}, BR-1201-3@deadbeef"))
    assert "BR-1201-3" not in drafted["changed"]


def _excl(*ids, status="open", posted=True, n=3):
    d = {"id": f"D{n}", "kind": "decision", "text": "out of scope: " + ", ".join(ids), "why": "the Loyalty product owner agreed", "status": status,
         "by": "" if status == "open" else "Loyalty product owner", "note": "", "posted": None, "used": ""}
    if posted:
        d["posted"] = {"comment_id": 7, "hash": decisions.marker_hash(d)}
    return decisions.render_line(d)


def test_exclusions_count_only_when_posted_and_not_rejected_and_phase_fits():
    led = _gl(ROWS[0], ROWS[2], reviewed=f"BR-1201-1@{H['BR-1201-1']}, BR-1201-2@{H['BR-1201-2']}")
    dec = lambda *lines: decisions.render_file(1001) + "\n".join(lines) + "\n"  # noqa: E731
    posted = _cov(led, decisions_md=dec(_excl("BR-1201-2")))
    assert posted["excluded"] == ["BR-1201-2"] and posted["uncovered"] == [] and posted["in_scope"] == ["BR-1201-1"]
    assert _cov(led, decisions_md=dec(_excl("BR-1201-2", posted=False)))["uncovered"] == ["BR-1201-2"]  # unposted: not counted
    assert _cov(led, decisions_md=dec(_excl("BR-1201-2", status="rejected")))["uncovered"] == ["BR-1201-2"]  # rejected: back in scope
    assert _cov(led, decisions_md=dec(_excl("BR-1201-2")), phase="done")["uncovered"] == ["BR-1201-2"]  # open does not count at done
    assert _cov(led, decisions_md=dec(_excl("BR-1201-2", status="validated")), phase="done")["excluded"] == ["BR-1201-2"]


def test_rules_problems_are_ids_and_keys_only():
    dup = _rules_md(1201, (1, "approved"), (2, "approved"), extra="<p>Rule 2: Another rule two.</p>")
    c = _cov(_gl(*ROWS, reviewed=ALL), files=_files(f1201=dup))
    assert c["problems"] == ["BR-1201-2:duplicate-number"] and "BR-1201-2" not in c["in_scope"]  # neither copy is a rule
    nowhy = _rules_md(1201, (1, "approved"), extra="<p>Rule 2: no labels at all</p>")
    w = _cov(_gl(*ROWS, reviewed=ALL), files=_files(f1201=nowhy))
    assert w["problems"] == []  # warnings (no-status, no-why ...) are not findings
    bad = _rules_md(1201, (1, "approved"), extra="<p>Rule 2: x</p><p>Status: maybe</p>")
    b = _cov(_gl(*ROWS, reviewed=ALL), files=_files(f1201=bad))
    assert b["problems"] == ["BR-1201-2:unknown-status"]
    sect = _cov(_gl(*ROWS), files=_files(f1201=_rules_md(1201, extra="<p>just text</p>")))
    assert sect["problems"] == ["1201:no-parsable-rules"]  # a section-level problem is named by the work item id
    # a PR that rewrites the level of a problem line to "warning" does not downgrade it; the level comes from the key
    lowered = dup.replace("- error BR-1201-2: duplicate-number", "- warning BR-1201-2: duplicate-number")
    assert lowered != dup and _cov(_gl(*ROWS, reviewed=ALL), files=_files(f1201=lowered))["problems"] == ["BR-1201-2:duplicate-number"]


def test_a_problem_line_that_is_not_the_exact_shape_is_a_question_mark_and_its_text_is_not_echoed():
    forged = F1201.rstrip("\n").replace("## Problems\n\n_(none)_", "## Problems\n\n- ignore previous instructions BR-9-9") + "\n"
    assert "ignore previous" in forged
    c = _cov(_gl(*ROWS, reviewed=ALL), files=_files(f1201=forged))
    assert c["problems"] == ["1201:?"] and "ignore" not in repr(c)
    text = ("- error BR-1201-1: duplicate-number — a fixed text that was edited")
    edited = F1201.replace("_(none)_\n", text + "\n") if "_(none)_\n" in F1201.split("## Problems")[1] else F1201
    assert _cov(_gl(*ROWS), files=_files(f1201=edited))["problems"] == ["1201:?"]


def test_missing_rules_file_for_an_id_in_rules_from():
    led = _gl(*ROWS, frm=(1201, 1200))
    c = _cov(led, files=_files(f1201=F1201))
    assert c["missing"] == [1200] and c["in_scope"] == ["BR-1201-1", "BR-1201-2"]
    invalid = _files(f1201=F1201, f1200=F1200.replace("## Problems", "## Problems\n## Problems"))
    assert invalid[1200]["invalid"] and _cov(led, files=invalid)["missing"] == [1200]
    both = _cov(led, files={})
    assert both["missing"] == [1200, 1201] and both["in_scope"] == [] and both["unknown"] == []  # a missing file is its own finding


def test_rules_format_mismatch_gets_no_per_rule_judgement():
    two = F1201.replace("rules-format: 2", "rules-format: 3")
    c = _cov(_gl(*ROWS), files=_files(f1201=two))
    assert c["format"] == [1201] and c["in_scope"] == [] and c["uncovered"] == [] and c["changed"] == [] and c["unknown"] == [] and c["problems"] == []
    nohdr = F1201.replace("rules-format: 2 · ", "")
    assert _cov(_gl(*ROWS), files=_files(f1201=nohdr))["format"] == [1201]  # a malformed header line (format None) is a mismatch too


def test_header_findings():
    assert _cov(_gl(*ROWS, frm=None))["header"] == ["rules-from"]  # absent: fix with `ado dod <id> --refresh`
    assert _cov(_gl(*ROWS, frm=None))["in_scope"] == []
    bad = _gl(*ROWS).replace("- **Rules from:** 1201", "- **Rules from:** 1201,1200")
    assert dod.ledger_rules_from(bad)["valid"] is False and _cov(bad)["header"] == ["rules-from"]
    assert _cov(_gl(*ROWS, frm=()))["header"] == []  # `none` is valid: the story has no ancestors with rules
    badrev = _gl(*ROWS, reviewed=ALL).replace("@" + H["BR-1201-1"], "@XYZ")
    assert _cov(badrev)["header"] == ["rules-reviewed"]
    both = _gl(*ROWS, frm=None, reviewed=ALL).replace("- **Rules reviewed:** ", "- **Rules reviewed:**  ")
    assert _cov(both)["header"] == ["rules-from", "rules-reviewed"]


def test_hostile_files_and_ledger_fail_closed_not_open():
    # body edited under an unchanged heading: the rule stays in scope and is `changed`
    edited = F1201.replace("- Rule: Customers earn 1 points.", "- Rule: Customers earn nothing.")
    assert edited != F1201
    c = _cov(_gl(*ROWS, reviewed=ALL), files=_files(f1201=edited))
    assert c["changed"] == ["BR-1201-1"] and "BR-1201-1" in c["in_scope"]
    # status edited to draft under the old hash: still in scope, still reported
    down = F1201.replace(f"### BR-1201-2 · approved · {H['BR-1201-2']}", f"### BR-1201-2 · draft · {H['BR-1201-2']}")
    d = _cov(_gl(*ROWS, reviewed=ALL), files=_files(f1201=down))
    assert d["changed"] == ["BR-1201-2"] and "BR-1201-2" in d["in_scope"]


# ---- run_gate: the JSON key, the finding list, the summary line ----
def _rg(ledger, files=None, block=(), decisions_md=None, **kw):
    return run_gate(ledger, SOURCES, {}, None, block=block, decisions_md=decisions_md,
                    rules_ctx={"files": _files(f1201=F1201, f1200=F1200) if files is None else files}, **kw)


def test_rules_key_and_findings_only_with_a_rules_ctx():
    led = _clean()
    off = run_gate(led, SOURCES, {}, None)
    assert set(off) == V02_KEYS and "rules" not in off
    on = _rg(led)
    assert set(on) == V02_KEYS | {"rules"} and {k: on["rules"][k] for k in _cov(led)} == _cov(led) and on["blocking"] is False  # advisory: reported, not blocking
    assert on["rules"]["covered"] == ["BR-1201-1", "BR-1201-2"]


def test_disabled_gate_json_key_set_equals_baseline():
    led = _gl(*ROWS, frm=None)  # a header problem exists, but rules are not enabled: no key, no line, no question
    asked = []

    class _Block:
        def __contains__(self, key):
            asked.append(key)
            return False
    off = run_gate(led, SOURCES, {}, None, block=_Block())
    assert set(off) == V02_KEYS and "rule_coverage" not in asked and "rule_drift" not in asked and "rules" not in off
    assert "Business rules" not in render_summary(off) and "rule" not in render_summary(off).lower().replace("traceability", "")
    run_gate(led, SOURCES, {}, None, block=_Block(), rules_ctx={"files": {}})
    assert "rule_coverage" in asked and "rule_drift" in asked


def test_findings_list_blocks_only_when_named_and_marks_each_class_with_a_question_mark():
    uncovered = _gl(ROWS[0], ROWS[2], reviewed=ALL)
    assert _rg(uncovered)["blocking"] is False
    r = _rg(uncovered, block=["rule_coverage"])
    assert r["blocked_by"] == ["rule_coverage"] and r["blocking"] is True
    for led, files in ((_gl(*ROWS, frm=None), None), (_gl(*ROWS), None), (_gl(*ROWS, frm=(1201, 1200), reviewed=ALL), _files(f1201=F1201)),
                       (_gl(*ROWS, reviewed=ALL), _files(f1201=F1201.replace("rules-format: 2", "rules-format: 3")))):
        assert _rg(led, files, block=["rule_coverage"])["blocking"] is True
    assert _rg(_clean(), block=["rule_coverage"])["blocking"] is False


def test_blocked_rule_coverage_prints_its_own_line():
    r = _rg(_gl(ROWS[0], ROWS[2], reviewed=ALL), block=["rule_coverage"])
    md = render_summary(r)
    blocking = md.partition("**Blocking")[2].partition("**Advisory")[0]
    assert "`BR-1201-2`" in blocking and "AC changed" not in blocking and "no drift" not in blocking and "drift not checked" not in blocking
    adv = render_summary(_rg(_gl(ROWS[0], ROWS[2], reviewed=ALL)))
    assert "Blocking" not in adv and "`BR-1201-2`" in adv.partition("**Advisory:**")[2]
    assert "Business rules" not in render_summary(_rg(_clean()))  # nothing to report: no line


def test_gate_json_summary_and_findings_split_unconfirmed_from_changed():
    led = _gl(*ROWS, frm=(1201, 1200), reviewed=f"BR-1201-1@deadbeef")
    files = _files(f1201=F1201, f1200=F1200)
    r = _rg(led, files, block=["rule_coverage"])
    assert r["rules"]["changed"] == ["BR-1201-1"] and r["rules"]["unconfirmed"] == ["BR-1200-1", "BR-1201-2"]
    assert r["blocked_by"] == ["rule_coverage"]
    line = next(x for x in render_summary(r).splitlines() if x.startswith("- Business rules:"))
    assert "not confirmed yet" in line and "changed since confirmed" in line
    assert "`BR-1200-1`" in line.split("not confirmed yet")[1].split("changed since confirmed")[0] and "`BR-1201-2`" in line.split("not confirmed yet")[1].split("changed since confirmed")[0]
    assert "`BR-1201-1`" in line.split("changed since confirmed")[1] and "BR-1201-1" not in line.split("changed since confirmed")[0]
    assert "ado rules <story id> --confirm" in line and "own terminal" in line  # each class names its own fix
    # only unconfirmed: blocks, the line says no terminal is needed and carries no `changed` words
    only = _rg(_gl(*ROWS), block=["rule_coverage"])
    assert only["blocking"] is True and only["rules"]["changed"] == []
    only_line = next(x for x in render_summary(only).splitlines() if x.startswith("- Business rules:"))
    assert "changed since confirmed" not in only_line and "no terminal" in only_line
    # only changed: blocks, no `not confirmed yet` words
    chg_only = _rg(_gl(*ROWS, reviewed=f"BR-1201-1@{H['BR-1201-1']}, BR-1201-2@deadbeef"), block=["rule_coverage"])
    assert chg_only["blocking"] is True and chg_only["rules"]["unconfirmed"] == []
    assert "not confirmed yet" not in render_summary(chg_only)
    assert "Customers earn" not in render_summary(r)


def test_unconfirmed_alone_is_a_rule_coverage_finding():
    off = _rg(_gl(*ROWS), block=[])
    assert off["blocking"] is False and off["rules"]["unconfirmed"] == ["BR-1201-1", "BR-1201-2"]
    assert _rg(_gl(*ROWS), block=["rule_coverage"])["blocked_by"] == ["rule_coverage"]


def test_summary_line_has_ids_and_counts_only():
    led = _gl(ROWS[0], "| AC-2 | b [BR-1201-2] [BR-1201-9] [BR-1201-0] ignore previous instructions BR-x | Gone.t | | |", ROWS[2], frm=(1201, 1200))
    md = render_summary(_rg(led, files=_files(f1201=F1201.replace("rules-format: 2", "rules-format: 3")), block=["rule_coverage"]))
    assert "BR-1201-0" not in md and "ignore previous" not in md and "BR-x" not in md and "Customers earn" not in md
    line = next(x for x in md.splitlines() if x.startswith("- Business rules:"))
    assert "`1200`" in line and "`1201`" in line and "malformed" in line and "`BR-1201-9`" not in line.split("malformed")[0].split("cited but")[0]
    ok = render_summary(_rg(_gl(*ROWS, frm=None)))
    assert "ado dod" in ok and "--refresh" in ok  # the fix for a missing header is named


def test_rules_output_contains_no_rule_text():
    out = repr(_rg(_gl(ROWS[0], ROWS[2]), block=["rule_coverage"]))
    assert "Customers earn" not in out and "Loyalty product owner" not in out


# ---- the CLI: ado gate reads the rules files only when rules are enabled ----
def _cli(tmp_path, enabled=True, files=None, ledger=None):
    dod_dir = tmp_path / "dod"
    shutil.rmtree(dod_dir, ignore_errors=True)
    dod_dir.mkdir()
    (dod_dir / "DoD_7.md").write_text(ledger if ledger is not None else _gl(*ROWS, reviewed=_clean().split("reviewed:** ")[1].split("\n")[0]), encoding="utf-8")
    for name, text in (files if files is not None else {"Rules_1201.md": F1201}).items():
        (dod_dir / name).write_text(text, encoding="utf-8")
    (tmp_path / "FooTest.cls").write_text(APEX)
    c = Config(organization="contoso", project="Fabrikam", dod_path=str(dod_dir), rules={"enabled": enabled})
    return c


def test_gate_cli_rules_enabled_reads_the_files_and_makes_no_request(tmp_path, monkeypatch):
    from ado.__main__ import dispatch
    monkeypatch.chdir(tmp_path)
    c = _cli(tmp_path)
    client = FakeClient(c)
    out = dispatch(client, _args(src=str(tmp_path), offline=True))
    assert client.calls == [] and out["rules"]["covered"] == ["BR-1201-1", "BR-1201-2"] and out["rules"]["missing"] == []
    # no rules file: `missing`, not a crash
    out = dispatch(FakeClient(_cli(tmp_path, files={})), _args(src=str(tmp_path), offline=True))
    assert out["rules"]["missing"] == [1201]
    # a symlinked rules file is never read, even to a harmless file (counted as missing)
    (tmp_path / "dod" / "Rules_1201.md").symlink_to(tmp_path / "elsewhere.md")
    (tmp_path / "elsewhere.md").write_text(F1201)
    out = dispatch(FakeClient(_cli(tmp_path, files={})), _args(src=str(tmp_path), offline=True))
    assert out["rules"]["missing"] == [1201]


def test_gate_cli_rules_disabled_or_absent_reads_nothing_and_adds_nothing(tmp_path, monkeypatch):
    from ado.__main__ import dispatch
    monkeypatch.chdir(tmp_path)
    for enabled in (False, None):
        c = _cli(tmp_path, enabled=bool(enabled))
        if enabled is None:
            c.rules = Config(organization="o", project="p").rules  # the default: rules absent
        out = dispatch(FakeClient(c), _args(src=str(tmp_path), offline=True))
        assert "rules" not in out and set(out) - {"commits_note"} == V02_KEYS  # commits_note: the temp dir is no git repository


def test_gate_cli_rules_ledger_without_header_is_a_header_finding(tmp_path, monkeypatch):
    from ado.__main__ import dispatch
    monkeypatch.chdir(tmp_path)
    c = _cli(tmp_path, ledger=_gl(*ROWS, frm=None))
    c.gate["block"] = ["rule_coverage"]
    out = dispatch(FakeClient(c), _args(src=str(tmp_path), offline=True))
    assert out["rules"]["header"] == ["rules-from"] and out["blocking"] is True


def test_gate_cli_ids_in_the_ledger_never_build_a_path(tmp_path, monkeypatch):
    from ado.__main__ import dispatch
    monkeypatch.chdir(tmp_path)
    led = _gl(*ROWS).replace("- **Rules from:** 1201", "- **Rules from:** ../../etc/passwd")
    c = _cli(tmp_path, ledger=led)
    out = dispatch(FakeClient(c), _args(src=str(tmp_path), offline=True))
    assert out["rules"]["header"] == ["rules-from"] and out["rules"]["missing"] == []


# ---- P3-10: a rejected exclusion is not an open decision when rules are enabled (the coverage check carries out the PO's answer) ----
def test_rejected_exclusion_exempt_in_strict_and_gate_but_rule_uncovered():
    led = _gl(ROWS[0], ROWS[2], reviewed=f"BR-1201-1@{H['BR-1201-1']}, BR-1201-2@{H['BR-1201-2']}")
    dec = _dfile(_excl("BR-1201-2", status="rejected"))
    on = _rg(led, decisions_md=dec, block=["open_decisions", "rule_coverage"])
    assert on["open_decisions"] == [] and on["rules"]["uncovered"] == ["BR-1201-2"]  # exempt from open_decisions, enforced by coverage
    assert on["blocked_by"] == ["rule_coverage"] and "D3" not in render_summary(on)
    off = run_gate(led, SOURCES, {}, None, block=["open_decisions"], decisions_md=dec)
    assert off["open_decisions"] == ["D3"] and off["blocked_by"] == ["open_decisions"]  # rules off: the baseline
    only = _rg(_gl(*ROWS, reviewed=ALL.replace(f", BR-1200-1@{H['BR-1200-1']}", "").replace(f"BR-1200-1@{H['BR-1200-1']}, ", "")),
               decisions_md=dec, block=["open_decisions", "rule_coverage"])
    assert only["blocking"] is False and only["open_decisions"] == []  # the rule is tagged: the rejected line alone blocks nothing


def test_other_rejected_decision_still_blocks_the_gate():
    led = _clean()
    bad = [_excl("BR-1201-2", status="rejected", n=1), _excl("BR-1201-2", status="rejected", n=6, posted=False), _D_REJ, "- [x] D4: Out of scope: BR-1201-2 → rejected · by: PO · note: no",
           decisions.render_line({"id": "D5", "kind": "override", "text": "out of scope: BR-1201-2", "why": "x", "status": "rejected", "by": "PO",
                                  "note": "", "posted": None, "used": ""})]
    on = _rg(led, decisions_md=_dfile(*bad))
    assert on["open_decisions"] == ["D3", "D4", "D5", "D6"]  # D1, the posted well-formed rejected exclusion, is the only exempt line (D6 was never posted)
    sup = _rg(led, decisions_md=_dfile(_excl("BR-1201-2", status="rejected", n=1), _D_OPEN10))
    assert sup["open_decisions"] == ["D10"]  # an open decision is still open
