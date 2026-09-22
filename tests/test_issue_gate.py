"""
Ratchet gate over the per-configuration issue matrix (tools/audit_configs.py).

tests/known_issues.yml records, per configuration, the issue codes that are
currently accepted as known debt (PLAN.md tracks their burn-down). This test
fails in two situations:

  1. A configuration exhibits a code that is NOT in its baseline entry — a
     regression (or a new configuration that was added without a baseline
     entry). Fix the configuration, do not extend the baseline.
  2. A configuration's baseline lists a code that no longer applies — the fix
     landed, so the baseline must shrink. Regenerate it with
         /usr/bin/python3 tools/audit_configs.py --write-baseline
     and commit the smaller file together with the fix.

Codes that depend on the BGM checkout (CLK-BGM, RST-*, PLL, GOWIN-OPT,
POLARITY, NO-ORACLE) are compared only when BGM is present, so the gate
also works on machines without the upstream repository.
"""

import os

import yaml

from tools import audit_configs


_BGM_DEPENDENT = {"CLK-BGM", "RST-NONE", "RST-BGM", "PLL", "GOWIN-OPT", "POLARITY", "NO-ORACLE"}


def _baseline():
    with open(audit_configs.BASELINE_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {k: set(v or []) for k, v in data.items()}


def test_issue_matrix_matches_baseline():
    bgm_present = os.path.isdir(audit_configs.BGM_BOARDS)
    baseline = _baseline()
    rows = audit_configs.audit_all()

    regressions = []
    stale = []
    seen = set()
    for row in rows:
        cfg_id = row["id"]
        seen.add(cfg_id)
        actual = set(row["issues"])
        expected = baseline.get(cfg_id)
        if expected is None:
            regressions.append("{}: no baseline entry (codes: {})".format(
                cfg_id, ", ".join(sorted(actual)) or "none"))
            continue
        if not bgm_present:
            actual -= _BGM_DEPENDENT
            expected = expected - _BGM_DEPENDENT
        new_codes = actual - expected
        gone_codes = expected - actual
        if new_codes:
            regressions.append("{}: new {}".format(cfg_id, ", ".join(sorted(new_codes))))
        if gone_codes:
            stale.append("{}: fixed {}".format(cfg_id, ", ".join(sorted(gone_codes))))

    removed = sorted(set(baseline) - seen)
    if removed:
        stale.append("baseline lists configurations that no longer exist: " + ", ".join(removed))

    msg = []
    if regressions:
        msg.append("REGRESSIONS (fix the configuration, do not edit the baseline):\n  "
                   + "\n  ".join(regressions))
    if stale:
        msg.append("STALE BASELINE (run `tools/audit_configs.py --write-baseline`):\n  "
                   + "\n  ".join(stale))
    assert not msg, "\n\n".join(msg)


def test_no_configuration_errors_out():
    rows = audit_configs.audit_all()
    broken = [r["id"] for r in rows if "ERROR" in r["issues"]]
    assert not broken, "audit crashed on: " + ", ".join(broken)
