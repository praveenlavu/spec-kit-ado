# P4-18: scripts/ci_smoke, the six consumer cases (a) to (f) of docs/release-checklist.md as fixtures. Each case is built as a real temporary git repository and
# the LOCAL checks (`ado gate`, `ado strict ...`, `ado done` with its decisions cross-check) must give the exit code and rule ids the checklist expects.
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ado.client import AdoError
from ado.config import Config

SMOKE = Path(__file__).resolve().parent.parent / "scripts" / "ci_smoke"
_spec = importlib.util.spec_from_file_location("ci_smoke_run_local", SMOKE / "run_local.py")
run_local = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_local)

# Written out here, not read from cases.json: editing the data cannot quietly change what the checklist promises (D§4.8, case by case).
B = ["--base", "main"]
STRICT_TARGET = {"mode": "strict", "source": "target branch"}
EXPECTED = {
    "a": [(["branch", "--id-from", "feature/members-list", *B], 1, [], {}), (["gate", "1001", "--offline", *B], 0, [], {"blocking": False}),
          (["strict", "mode", *B], 0, [], {"mode": "advisory", "source": "pull request"})],
    "b": [(["strict", "mode", *B], 0, [], STRICT_TARGET),
          (["strict", "check", "1001", "--phase", "pr", *B], 2, ["stack-profile-drift"], {"phase": "pr", "mode": "strict", "ok": False})],
    "c": [(["strict", "mode", *B], 0, [], STRICT_TARGET),
          (["gate", "1001", "--offline", *B], 0, [], {"blocking": False, "config": "target branch (strict)"}),
          (["strict", "check", "1001", "--phase", "pr", *B], 0, [], {"phase": "pr", "mode": "strict", "ok": True}),
          (["strict", "verify", "1001", "--no-record", *B], 0, [], {"mode": "strict", "ok": True, "recorded": False})],
    "d": [(["strict", "verify", "1001", "--no-record", *B], 2, ["green-fails"], {"mode": "strict", "ok": False})],
    "e": [(["strict", "check", "1001", "--phase", "done", *B], 0, [], {"phase": "done", "mode": "strict", "ok": True}),
          (["done", "1001", *B], 0, [], {"ready": True, "applied": False, "unticked": []})],
    "f": [(["done", "1001", *B], 2, ["decisions-xcheck-marker"], {"phase": "done", "mode": "strict", "ok": False})],
}


def test_the_cases_are_exactly_a_to_f_with_the_promised_steps():
    cases = run_local.load_cases()
    assert list(cases) == list("abcdef")
    for name, steps in EXPECTED.items():
        assert [(s["argv"], s["exit"], s.get("rules", []), s.get("out", {})) for s in cases[name]["steps"]] == steps, name
        assert cases[name]["title"]


@pytest.mark.parametrize("name", list("abcdef"))
def test_every_case_gives_the_expected_local_result(name):
    assert run_local.run_case(name, run_local.load_cases()[name]) == []


def test_a_wrong_expectation_is_reported_not_swallowed():
    case = run_local.load_cases()["f"]
    wrong = {**case, "steps": [{"argv": ["done", "1001", "--base", "main"], "exit": 0}]}  # the forged marker is expected to pass
    bad = run_local.run_case("f", wrong)
    assert len(bad) == 1 and bad[0][2] == {"exit": 2, "rules": ["decisions-xcheck-marker"], "out": {}}
    wrong = {**case, "steps": [{"argv": ["done", "1001", "--base", "main"], "exit": 2, "rules": ["decisions-xcheck-verdict"]}]}
    assert len(run_local.run_case("f", wrong)) == 1  # the right exit with the wrong rule id still differs


def test_a_strict_case_whose_target_is_advisory_is_caught():
    """The strict mode and its source are asserted, so a case cannot pass while the strict rules were never the ones evaluated."""
    case = run_local.load_cases()["c"]
    bad = run_local.run_case("c", {**case, "target_config": "config.advisory.json", "stack": False})
    assert bad and any(argv[:2] == ["strict", "mode"] and got["out"] == {"mode": "advisory", "source": "pull request"} for argv, _w, got in bad)


def test_the_close_preview_is_ready_and_a_not_ready_expectation_is_caught():
    case = run_local.load_cases()["e"]
    flipped = {**case, "steps": [{**case["steps"][1], "out": {"ready": False}}]}
    bad = run_local.run_case("e", flipped)
    assert len(bad) == 1 and bad[0][2]["out"] == {"ready": True}


def test_a_case_that_cannot_write_its_record_stops_the_build(tmp_path):
    case = {**run_local.load_cases()["c"], "claimed_green": "wip"}  # a record cannot be made for a green commit at which the tests fail
    with pytest.raises(RuntimeError, match="verification record could not be written"):
        run_local.build(case, tmp_path)


def test_the_clean_and_the_forged_decisions_differ_only_in_the_marker():
    clean = (SMOKE / "fixtures" / "decisions_clean.md").read_text(encoding="utf-8")
    forged = (SMOKE / "fixtures" / "decisions_forged.md").read_text(encoding="utf-8")
    assert clean != forged and clean.replace("posted: 10:ccd493a5", "posted: 99:ccd493a5") == forged


def test_a_run_leaves_the_environment_and_the_directory_as_it_found_them(tmp_path, monkeypatch):
    monkeypatch.setattr(run_local.tempfile, "tempdir", str(tmp_path))  # its own temp dir: a parallel test's repository is never counted
    before_env, before_cwd = dict(os.environ), os.getcwd()
    run_local.run_case("c", run_local.load_cases()["c"])
    assert dict(os.environ) == before_env and os.getcwd() == before_cwd
    assert list(tmp_path.iterdir()) == []  # the temporary repository is removed


def test_the_smoke_client_answers_known_reads_and_refuses_everything_else():
    client = run_local.SmokeClient(Config(organization="contoso", project="Fabrikam"))
    assert client.get("/_apis/connectionData")["authenticatedUser"]["id"] == run_local._TOKEN_IDENTITY
    for method, path in (("GET", "/Fabrikam/_apis/somewhere/else"), ("PATCH", "/Fabrikam/_apis/wit/workitems/1001"), ("POST", "/Fabrikam/_apis/wit/workitems/$Task")):
        with pytest.raises(AdoError):
            client.request(method, path, {})


def test_main_exit_codes(capsys):
    assert run_local.main(["zzz"]) == 2 and "unknown case" in capsys.readouterr().err
    assert run_local.main(["c"]) == 0 and "(c) valid strict evidence" in capsys.readouterr().out


def test_fixtures_hold_only_fictional_names():
    allowed = {"example.invalid", "contoso.example"}
    for f in (p for p in SMOKE.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        text = f.read_text(encoding="utf-8")
        for dom in re.findall(r"[\w.+-]+@([\w.-]+\.\w+)", text):
            assert dom in allowed, (f, dom)
        assert "praveenlavu" not in text.lower() and "dev.azure.com/" not in text.replace("https://dev.azure.com/contoso/", ""), f


def test_pytest_never_collects_the_fixture_repositories():
    assert 'collect_ignore_glob = ["fixtures/*"]' in (SMOKE / "conftest.py").read_text(encoding="utf-8")
    assert (SMOKE / "fixtures" / "tree" / "red" / "tests" / "test_a.py").is_file()  # the failing test the red commit holds


def test_run_local_runs_as_documented_without_the_package_installed(tmp_path):
    """F7 (C M-5): `python3 scripts/ci_smoke/run_local.py` on a Python that has not installed the package (`-S`: no site-packages, no .pth) finds src/ itself."""
    r = subprocess.run([sys.executable, "-S", str(SMOKE / "run_local.py"), "a"], capture_output=True, text=True, cwd=str(tmp_path),
                       env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")})
    assert r.returncode == 0 and "ModuleNotFoundError" not in r.stderr and "(a)" in r.stdout, (r.stdout[-300:], r.stderr[-300:])
