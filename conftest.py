"""
pytest settings for the whole Voice Creation Center.

A test that does not run is not a pass. pytest's word for it is "skipped";
here every such test is reported as NOT RUN, letter N, and counted as
"not run" in the summary line, with its reason printed beside it.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
for _p in (_ROOT, _ROOT / "engines", _ROOT / "studio"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def pytest_report_teststatus(report, config):
    if report.skipped and report.when in ("setup", "call"):
        return "not run", "N", "NOT RUN"
    return None


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    not_run = terminalreporter.stats.get("not run", [])
    if not not_run:
        return
    terminalreporter.section("NOT RUN — these tests did not run and are not passes")
    for rep in not_run:
        reason = rep.longrepr[2] if isinstance(rep.longrepr, tuple) else str(rep.longrepr)
        if reason.startswith("Skipped: "):
            reason = reason[len("Skipped: "):]
        terminalreporter.write_line(f"{rep.nodeid}: {reason}")
