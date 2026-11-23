import json, os, subprocess, sys, time
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

def test_command_line_run_covers_every_feature_and_writes_reports(tmp_path):
    r = subprocess.run([sys.executable, "selftest.py", "--out", str(tmp_path)], cwd=APP, env={**os.environ, "SELFTEST_FAST": "1"},
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2500:]
    d = json.loads((tmp_path / "report.json").read_text())
    assert d["summary"]["failed"] == 0 and d["summary"]["total"] >= 30 and len(d["perf"]) >= 15
    assert all(row["median"] > 0 for row in d["perf"]) and d["summary"]["slowest_load"] > 0       # load times were really measured
    assert "<table" in (tmp_path / "report.html").read_text()

def test_filter_runs_only_matching_checks(tmp_path):
    r = subprocess.run([sys.executable, "selftest.py", "--only", "Sharing", "--no-perf", "--out", str(tmp_path)], cwd=APP, capture_output=True, text=True, timeout=120)
    d = json.loads((tmp_path / "report.json").read_text()); assert r.returncode == 0 and {c["category"] for c in d["checks"]} == {"Sharing"} and d["perf"] == []

def test_in_app_page_is_admin_only_and_runs_live(make_env):
    e = make_env(SELFTEST_FAST=1); amy = e.client().register("amy"); bob = e.client().register("bob")
    assert amy.get("/admin/selftest").status_code == 200
    assert bob.get("/admin/selftest").status_code == 403 and bob.post("/admin/selftest/run").status_code == 403 and bob.get("/admin/selftest/status").status_code == 403
    amy.post("/admin/selftest/run")
    end, seen = time.time() + 240, []
    while time.time() < end:
        d = amy.get(f"/admin/selftest/status?since={len(seen)}").get_json(); seen += d["events"]
        if d["state"] != "running": break
        time.sleep(1)
    kinds = [x["event"] for x in seen]
    assert d["state"] == "done" and kinds[0] == "start" and kinds[-1] == "done" and "check" in kinds and "perf" in kinds, kinds[-5:]
    assert seen[-1]["summary"]["failed"] == 0, [x for x in seen if x.get("status") in ("fail", "error")]
    assert "Craft Stash self-test" in amy.html("/admin/selftest/report") and amy.get("/admin/selftest/report?format=json").status_code == 200
    assert e.mod.SELFTEST["state"] == "done"
