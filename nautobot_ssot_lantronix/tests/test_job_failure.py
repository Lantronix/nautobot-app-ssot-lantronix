"""A failed sync says why in the job log and the worker log. Runs under nautobot-server test."""

from unittest import mock

from nautobot.core.testing import TransactionTestCase, create_job_result_and_run_job
from nautobot.extras.choices import JobResultStatusChoices

from nautobot_ssot_lantronix.percepxion.client import AuthError, PercepxionUnavailable

MODULE = "nautobot_ssot_lantronix.jobs"
JOB = "PercepxionDataSource"
SECRET = "sup3r-secret-pw"


def run_failing(error):
    with mock.patch(f"{MODULE}.PercepxionClient") as client:
        client.return_value.login.side_effect = error
        return create_job_result_and_run_job(MODULE, JOB, dryrun=True)


class JobFailureLoggingTests(TransactionTestCase):
    databases = ("default", "job_logs")

    def test_rejected_login_reason_reaches_job_and_worker_logs(self):
        with self.assertLogs("nautobot_ssot_lantronix.sync", "WARNING") as logs:
            result = run_failing(AuthError(f"POST /v2/user/login returned 401 {SECRET}"))
        self.assertEqual(result.status, JobResultStatusChoices.STATUS_FAILURE)
        self.assertIn(str(result.pk), logs.output[0])
        self.assertIn("rejected the login", logs.output[0])
        entries = list(result.job_log_entries.values_list("message", flat=True))
        self.assertTrue(any("Sync failed" in m and "rejected the login" in m for m in entries))
        self.assertNotIn(SECRET, "".join(entries) + "".join(logs.output))

    def test_read_failure_reason_reaches_job_log(self):
        error = PercepxionUnavailable("POST /v1/job/jobgroup/search returned HTTP 400")
        with self.assertLogs("nautobot_ssot_lantronix.sync", "WARNING"):
            result = run_failing(error)
        entries = list(result.job_log_entries.values_list("message", flat=True))
        self.assertTrue(any("jobgroup/search returned HTTP 400" in m for m in entries))
