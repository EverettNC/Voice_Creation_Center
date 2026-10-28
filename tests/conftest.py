"""
A test that cannot run here is reported as NOT RUN, with its reason
(pytest -ra prints it). It never passes and is never a quiet "s".
"""


def pytest_report_teststatus(report, config):
    if report.skipped:
        return "not run", "N", "NOT RUN"
    return None
