"""`ado qa` (piece 5, S-5): the QA file `<dod_path>/QA_<id>.md` (QA-written text only) and `--check`, which computes coverage from the ledger and the
rules files and reports ids and line numbers, never QA text. Local only: no token, no request. Fictional data."""
import json
import os
import time
from pathlib import Path

import pytest

from ado import __main__ as _main
from ado import qa
from ado.dod import SECTION2
from tests.test_rules_file import file_md

SECRET = "SEKRET-QA-TEXT"
LEDGER = (f"# DoD — 1001\n\n- **Rules from:** 1201\n\n## 1. Story\n\n### Acceptance Criteria\n\n- Customers see their points\n- Points never pay delivery\n\n"
          f"{SECTION2}\n\n## 3. AC\n\n## 5. Checklist\n- [ ] x\n")
CASE = "- [x] TC-{n}: {text} -> {res} · covers: {covers} · how: {how}"


def case(n=1, covers="AC-1", res="pass", how="manual", text="check it"):
    return CASE.format(n=n, text=text, res=res, covers=covers, how=how)


def qa_md(*lines, plan="Try the main flows."):
    return "# QA for 1001\n\n## Test plan\n\n" + plan + "\n\n## QA verification\n\n" + "\n".join(lines) + "\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    (tmp_path / "dod").mkdir()
    (tmp_path / "dod/DoD_1001.md").write_text(LEDGER)
    (tmp_path / "dod/Rules_1201.md").write_text(file_md())  # BR-1201-1 and BR-1201-3 approved, BR-1201-2 draft
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADO_CONFIG", raising=False)
    monkeypatch.delenv("ADO_TOKEN", raising=False)
    monkeypatch.setattr(_main, "AdoClient", lambda c: pytest.fail("ado qa is local only"))
    return tmp_path


def run(capsys, *argv):
    code = _main.main(["qa", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def write(repo, text, name="QA_1001.md"):
    (repo / "dod" / name).write_text(text, encoding="utf-8")


def check(capsys):
    code, out, err = run(capsys, "1001", "--check")
    return code, (json.loads(out) if out else None), err


# ---- creating the file -------------------------------------------------------------------------------------------------------------------------------

def test_creates_the_template_once_and_never_rewrites(repo, capsys):
    code, out, _ = run(capsys, "1001")
    path = repo / "dod/QA_1001.md"
    assert code == 0 and json.loads(out) == {"id": 1001, "file": "dod/QA_1001.md", "created": True}
    text = path.read_text()
    assert text.count("## Test plan") == 1 and text.count("## QA verification") == 1
    assert [ln for ln in text.splitlines() if ln.startswith("- [")] == []  # the template holds no case: QA writes them
    assert "--check" in text and "/reject signoff-qa" in text
    path.write_text(text + "\nQA edit\n")
    os.utime(path, (1, 1))
    code, out, _ = run(capsys, "1001")
    assert code == 0 and json.loads(out)["created"] is False
    assert path.read_text() == text + "\nQA edit\n" and path.stat().st_mtime == 1  # untouched


def test_create_needs_no_ledger_and_makes_the_directory(tmp_path, monkeypatch, capsys):
    (tmp_path / "ado.config.json").write_text(json.dumps({"organization": "contoso", "project": "Fabrikam"}))
    monkeypatch.chdir(tmp_path)
    assert run(capsys, "1001")[0] == 0 and (tmp_path / "dod/QA_1001.md").is_file()


def test_create_refuses_a_link_and_leaves_it(repo, capsys):
    (repo / "outside.md").write_text("x")
    link = repo / "dod/QA_1001.md"
    link.symlink_to(repo / "outside.md")
    code, out, err = run(capsys, "1001")
    assert (code, out) == (2, "") and err.strip() == "error: " + qa.E_FILE and (repo / "outside.md").read_text() == "x"
    link.unlink()
    link.symlink_to(repo / "missing.md")  # a dangling link is a file that cannot be written either
    assert run(capsys, "1001")[0] == 2 and not (repo / "missing.md").exists()


def test_story_id_is_validated(repo):
    for bad in ("0", "-1", "abc", "9999999999"):
        with pytest.raises(SystemExit) as e:
            _main.main(["qa", bad])
        assert e.value.code == 2
    with pytest.raises(SystemExit):
        _main.main(["qa"])


# ---- --check -----------------------------------------------------------------------------------------------------------------------------------------

def test_a_full_trace_passes(repo, capsys):
    write(repo, qa_md(case(1, "AC-1, BR-1201-1"), case(2, "AC-2, BR-1201-3", how="automated")))
    code, out, err = check(capsys)
    assert code == 0 and err == "" and out["ok"] is True and out["findings"] == []
    assert out["id"] == 1001 and out["cases"] == 2 and out["results"] == {"pass": 2, "fail": 0, "blocked": 0}
    assert out["note"] == qa.NOTE and "/reject signoff-qa" in qa.NOTE and "case ids" in qa.NOTE


def test_findings_are_ids_and_line_numbers_only(repo, capsys):
    lines = [case(1, "AC-1", text=SECRET),
             f"- [ ] TC-2: {SECRET} -> maybe · covers: AC-2 · how: manual",  # not a result
             case(3, "BR-1201-1", res="fail", how="automated"),
             f"- [x] {SECRET}"]
    write(repo, qa_md(*lines, plan=f"- [ ] {SECRET} in the plan is free text"))
    code, out, err = check(capsys)
    assert code == 2 and out["ok"] is False
    assert out["findings"] == [{"finding": "malformed-case", "line": 10}, {"finding": "malformed-case", "line": 12},
                               {"finding": "uncovered-ac", "id": "AC-2"}, {"finding": "uncovered-rule", "id": "BR-1201-3"}]
    assert out["cases"] == 2 and out["results"] == {"pass": 1, "fail": 1, "blocked": 0}
    assert SECRET not in json.dumps(out) + err


@pytest.mark.parametrize("bad", [
    "- [x] TC-1: a -> pass · covers: AC-1",  # no how
    "- [x] TC-1: a -> pass · covers: AC-1 · how: telepathy",
    "- [x] TC-1: a -> pass · covers: · how: manual",
    "- [x] TC-1: a -> pass · covers: AC-1,AC-2 · how: manual",  # comma without a space
    "- [x] TC-1: a -> pass · covers: AC-0 · how: manual",
    "- [x] TC-1: a -> pass · covers: BR-1-2-3 · how: manual",
    "- [x] TC-1: a -> pass · covers: ac-1 · how: manual",
    "- [x] TC-1: a -> PASS · covers: AC-1 · how: manual",
    "- [y] TC-1: a -> pass · covers: AC-1 · how: manual",
    "- [x] TC-0: a -> pass · covers: AC-1 · how: manual",
    "- [x] T-1: a -> pass · covers: AC-1 · how: manual",
    "- [x] TC-1:  -> pass · covers: AC-1 · how: manual",  # no description
    "- [x] TC-1: a -> pass · covers: AC-1 · how: manual " + "x" * 2000,
    "- [] TC-1: a -> pass · covers: AC-1 · how: manual",
])
def test_malformed_case_shapes(repo, capsys, bad):
    write(repo, qa_md(bad, case(2, "AC-2, BR-1201-1, BR-1201-3")))
    code, out, _ = check(capsys)
    assert {"finding": "malformed-case", "line": 9} in out["findings"] and {"finding": "uncovered-ac", "id": "AC-1"} in out["findings"]
    assert out["cases"] == 1 and code == 2


def test_a_repeated_case_id_is_malformed_and_covers_nothing(repo, capsys):
    write(repo, qa_md(case(1, "AC-1, BR-1201-1, BR-1201-3"), case(1, "AC-2")))
    out = check(capsys)[1]
    assert out["findings"] == [{"finding": "malformed-case", "line": 10}, {"finding": "uncovered-ac", "id": "AC-2"}] and out["cases"] == 1


def test_a_failed_or_blocked_case_still_covers(repo, capsys):
    write(repo, qa_md(case(1, "AC-1, BR-1201-1", res="fail"), case(2, "AC-2, BR-1201-3", res="blocked")))
    code, out, _ = check(capsys)
    assert code == 0 and out["results"] == {"pass": 0, "fail": 1, "blocked": 1}  # QA's verdict is the signature; `check` only traces


def test_cases_in_fences_comments_and_other_sections_do_not_count(repo, capsys):
    text = ("# QA\n\n## Test plan\n\n" + case(1, "AC-1") + "\n\n## QA verification\n\n```\n" + case(2, "AC-2") + "\n```\n<!--\n" + case(3, "BR-1201-1")
            + "\n-->\n" + case(4, "BR-1201-3") + "\n\n## Notes\n\n" + case(5, "AC-1") + "\n")
    write(repo, text)
    out = check(capsys)[1]
    assert out["cases"] == 1 and [f["id"] for f in out["findings"] if f["finding"] == "uncovered-ac"] == ["AC-1", "AC-2"]


def test_one_qa_verification_section_is_required(repo, capsys):
    for text in ("# QA\n\n## Test plan\n\nx\n", qa_md(case()) + "\n## QA verification\n\n" + case(2)):
        write(repo, text)
        code, out, err = run(capsys, "1001", "--check")
        assert (code, out) == (2, "") and err.strip() == "error: " + qa.E_SECTION


def test_rules_and_decisions_decide_what_is_in_scope(repo, capsys):
    (repo / "dod/DoD_1001.md").write_text(LEDGER.replace("- **Rules from:** 1201", "- **Rules from:** none"))
    write(repo, qa_md(case(1, "AC-1, AC-2")))
    assert check(capsys)[0] == 0  # no rules file named: nothing to cover
    (repo / "dod/DoD_1001.md").write_text(LEDGER)
    (repo / "dod/Rules_1201.md").unlink()
    out = check(capsys)[1]
    assert out["findings"] == [{"finding": "rules-unreadable"}]  # a named file that cannot be read is said, never skipped
    (repo / "dod/Rules_1201.md").write_text("not a rules file")
    assert check(capsys)[1]["findings"] == [{"finding": "rules-unreadable"}]


def test_the_check_reads_but_never_writes(repo, capsys):
    write(repo, qa_md(case(1, "AC-1")))
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in (repo / "dod").iterdir()}
    time.sleep(0.01)
    check(capsys)
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in (repo / "dod").iterdir()} == before


def test_a_ledger_change_leaves_the_qa_file_byte_identical(repo, capsys):
    write(repo, qa_md(case(1, "AC-1")))
    before = (repo / "dod/QA_1001.md").read_bytes()
    (repo / "dod/DoD_1001.md").write_text(LEDGER.replace("## 3. AC\n", "## 3. AC\n\n| AC-1 | x | t | e | green |\n"))
    run(capsys, "1001")
    check(capsys)
    assert (repo / "dod/QA_1001.md").read_bytes() == before


# ---- confined reads ---------------------------------------------------------------------------------------------------------------------------------

def test_missing_oversize_link_and_foreign_files_are_refused(repo, capsys):
    code, out, err = run(capsys, "1001", "--check")
    assert (code, out) == (2, "") and err.strip() == "error: " + qa.E_NO_QA
    (repo / "dod/QA_1001.md").write_text(qa_md(case()) + "x" * (1 << 20))  # over 1 MiB
    assert run(capsys, "1001", "--check")[2].strip() == "error: " + qa.E_READ
    (repo / "dod/QA_1001.md").unlink()
    (repo / "elsewhere.md").write_text(qa_md(case(1, "AC-1")))
    (repo / "dod/QA_1001.md").symlink_to(repo / "elsewhere.md")
    assert run(capsys, "1001", "--check")[2].strip() == "error: " + qa.E_READ
    (repo / "dod/QA_1001.md").unlink()
    os.link(repo / "elsewhere.md", repo / "dod/QA_1001.md")  # a hard link may name a file outside the repo
    assert run(capsys, "1001", "--check")[2].strip() == "error: " + qa.E_READ
    (repo / "dod/QA_1001.md").unlink()
    write(repo, qa_md(case()))
    (repo / "dod/DoD_1001.md").unlink()
    assert run(capsys, "1001", "--check")[2].strip() == "error: " + qa.E_LEDGER


def test_hostile_input_is_linear(repo, capsys):
    t = time.monotonic()
    write(repo, qa_md("- [x] TC-1: " + " -> pass · covers: AC-1 · how: manual" * 15_000, *[case(n, "AC-1") for n in range(2, 3000)]))
    assert check(capsys)[0] == 2 and time.monotonic() - t < 5
