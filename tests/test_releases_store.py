"""Ledger rules: prod rows require a CR, and the audit export contains only prod."""

import sqlite3
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config, assert_runtime_config
from gitlab_client import cicd_enabled
from model import DeploymentRequest
from skip_list import format_skipped, parse_skip_list, partition_tenants, read_skipped
from releases_store import (
    EnvironmentBusy,
    clear_environment_lock,
    get_environment_lock,
    import_sample_releases,
    list_prod_audit,
    list_releases,
    note_pipeline_running,
    portal_started_pipeline,
    prod_audit_csv,
    record_release,
    release_environment,
    release_stale_reservation,
    reserve_environment,
    set_pipeline_status,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample-releases.json"


def _release(path, **overrides):
    payload = {
        "username": "dev-operator",
        "environment": "dev",
        "tenant_list": "dev-only-tenant",
        "pipeline_trigger": "tenant_baseline",
        "taint": False,
        "cr_number": None,
        "change_summary": "",
        "gitlab_pipeline_id": 11,
        "gitlab_web_url": None,
        "path": path,
    }
    payload.update(overrides)
    return record_release(**payload)


class ReleaseLedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "releases.db")

    def tearDown(self):
        self.directory.cleanup()

    def test_prod_model_requires_and_normalizes_cr(self):
        with self.assertRaises(ValidationError):
            DeploymentRequest(
                targetEnvironment="prod",
                allowProd=True,
                tenantList="tenant-a",
                pipelineTrigger="tenant_baseline",
                taint=False,
            )
        accepted = DeploymentRequest(
            targetEnvironment="prod",
            allowProd=True,
            tenantList="tenant-a",
            pipelineTrigger="tenant_baseline",
            taint=False,
            crNumber="chg0012345",
        )
        self.assertEqual(accepted.crNumber, "CHG0012345")
        self.assertFalse(accepted.dryRun)
        planned = DeploymentRequest(
            targetEnvironment="prod",
            allowProd=False,
            tenantList="tenant-a",
            pipelineTrigger="tenant_baseline",
            taint=False,
            crNumber="CHG0012345",
        )
        self.assertTrue(planned.dryRun)
        self.assertIsNone(planned.crNumber)

    def test_non_prod_discards_cr(self):
        accepted = DeploymentRequest(
            targetEnvironment="dev",
            allowProd=False,
            tenantList="tenant-a",
            pipelineTrigger="tenant_baseline",
            taint=False,
            crNumber="CHG0012345",
        )
        self.assertIsNone(accepted.crNumber)

    def test_store_rejects_prod_without_cr(self):
        with self.assertRaises(ValueError):
            _release(self.path, environment="prod", username="prod-operator", cr_number=None)
        self.assertEqual(list_releases(self.path), [])

    def test_prod_dry_run_is_stored_without_a_change_request(self):
        release_id = _release(
            self.path,
            environment="prod",
            username="prod-operator",
            cr_number=None,
            dry_run=True,
            change_summary="terraform plan",
        )
        stored = list_releases(self.path)
        self.assertEqual(stored[0]["id"], release_id)
        self.assertTrue(stored[0]["dryRun"])
        self.assertIsNone(stored[0]["crNumber"])
        self.assertEqual(stored[0]["changeSummary"], "terraform plan")

    def test_existing_ledger_gains_a_dry_run_column(self):
        connection = sqlite3.connect(self.path)
        connection.executescript(
            """
            CREATE TABLE releases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                released_at TEXT NOT NULL,
                username TEXT NOT NULL,
                environment TEXT NOT NULL,
                tenant_list TEXT NOT NULL,
                pipeline_trigger TEXT NOT NULL,
                taint INTEGER NOT NULL,
                cr_number TEXT,
                change_summary TEXT NOT NULL DEFAULT '',
                gitlab_pipeline_id INTEGER,
                gitlab_web_url TEXT,
                pipeline_status TEXT,
                skipped_tenants TEXT NOT NULL DEFAULT '',
                CHECK (environment IN ('dev', 'test', 'preprod', 'prod')),
                CHECK (
                    (environment = 'prod' AND cr_number IS NOT NULL)
                    OR (environment != 'prod' AND cr_number IS NULL)
                )
            );
            INSERT INTO releases (
                released_at, username, environment, tenant_list, pipeline_trigger,
                taint, cr_number
            ) VALUES (
                '2026-01-01T00:00:00Z', 'op', 'prod', 'atlas', 'tenant_baseline', 0, 'CHG0012345'
            );
            """
        )
        connection.close()
        _release(
            self.path,
            environment="prod",
            username="planner",
            tenant_list="atlas",
            cr_number="CHG0000001",
            dry_run=True,
            gitlab_pipeline_id=12,
        )
        stored = list_releases(self.path)
        self.assertEqual([row["username"] for row in stored], ["op", "planner"])
        self.assertFalse(stored[0]["dryRun"])
        self.assertEqual(stored[0]["crNumber"], "CHG0012345")
        self.assertTrue(stored[1]["dryRun"])
        self.assertIsNone(stored[1]["crNumber"])

    def test_audit_csv_is_prod_only(self):
        _release(self.path)
        _release(
            self.path,
            username="=prod-operator",
            environment="prod",
            tenant_list="prod-tenant",
            cr_number="CHG0012345",
            gitlab_pipeline_id=99,
        )
        _release(
            self.path,
            username="preprod-operator",
            environment="preprod",
            tenant_list="preprod-tenant",
            cr_number="CHG0099999",
        )

        audit = list_prod_audit(self.path)
        self.assertEqual([row["username"] for row in audit], ["=prod-operator"])
        self.assertEqual(audit[0]["crNumber"], "CHG0012345")
        self.assertTrue(all(row["environment"] == "prod" for row in audit))

        # A CR passed for pre-prod must not be stored.
        preprod = [row for row in list_releases(self.path) if row["environment"] == "preprod"]
        self.assertIsNone(preprod[0]["crNumber"])

        csv_text = prod_audit_csv(self.path).lstrip("\ufeff")
        self.assertIn("username,date_utc,time_utc,cr_number", csv_text.splitlines()[0])
        self.assertIn("CHG0012345", csv_text)
        self.assertIn("'=prod-operator", csv_text)
        self.assertNotIn("dev-only-tenant", csv_text)
        self.assertNotIn("preprod-tenant", csv_text)
        self.assertNotIn("CHG0099999", csv_text)

    def test_status_is_limited_to_pipelines_this_portal_started(self):
        _release(self.path, gitlab_pipeline_id=40)
        self.assertTrue(portal_started_pipeline(40, self.path))
        self.assertFalse(portal_started_pipeline(41, self.path))

    def test_one_inflight_release_per_environment(self):
        dev = reserve_environment("dev", self.path)
        with self.assertRaises(EnvironmentBusy):
            reserve_environment("dev", self.path)
        prod = reserve_environment("prod", self.path)

        self.assertFalse(note_pipeline_running("dev", 40, prod, self.path))
        self.assertTrue(note_pipeline_running("dev", 40, dev, self.path))
        set_pipeline_status(40, "running", self.path)
        set_pipeline_status(40, "status with spaces", self.path)
        clear_environment_lock(40, self.path)
        self.assertFalse(release_environment("prod", dev, self.path))
        self.assertTrue(release_environment("prod", prod, self.path))
        reserve_environment("dev", self.path)
        reserve_environment("prod", self.path)

    def test_stale_reservation_cannot_release_a_newer_hold(self):
        stale = reserve_environment("dev", self.path)
        self.assertFalse(release_stale_reservation("dev", timedelta(minutes=3), self.path))
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "UPDATE environment_lock SET reserved_at = ? WHERE environment = 'dev'",
                ("2000-01-01T00:00:00Z",),
            )
            connection.commit()
        finally:
            connection.close()
        self.assertTrue(release_stale_reservation("dev", timedelta(minutes=3), self.path))
        self.assertIsNone(get_environment_lock("dev", self.path))

        newer = reserve_environment("dev", self.path)
        self.assertFalse(release_environment("dev", stale, self.path))
        self.assertIsNotNone(get_environment_lock("dev", self.path))
        self.assertTrue(release_environment("dev", newer, self.path))

    def test_rejects_unsafe_trigger_fields(self):
        with self.assertRaises(ValidationError):
            DeploymentRequest(
                targetEnvironment="dev",
                allowProd=False,
                tenantList="atlas;curl",
                pipelineTrigger="tenant_baseline",
                taint=False,
            )
        with self.assertRaises(ValidationError):
            DeploymentRequest(
                targetEnvironment="dev",
                allowProd=False,
                tenantList="atlas",
                pipelineTrigger="tenant_services",
                taint=True,
                services=["airflow", "$(id)"],
            )
        planned = DeploymentRequest(
            targetEnvironment="prod",
            allowProd=False,
            tenantList="atlas",
            pipelineTrigger="tenant_baseline",
            taint=False,
            crNumber="CHG0012345",
        )
        self.assertTrue(planned.dryRun)
        self.assertIsNone(planned.crNumber)
        accepted = DeploymentRequest(
            targetEnvironment="dev",
            allowProd=False,
            tenantList=" atlas, borealis ",
            pipelineTrigger="tenant_services",
            taint=True,
            services=["airflow", "airflow"],
            replaceResource="$(rm)",
        )
        self.assertEqual(accepted.tenantList, "atlas,borealis")
        self.assertEqual(accepted.services, ["airflow"])
        self.assertIsNone(accepted.replaceResource)
        everyone = DeploymentRequest(
            targetEnvironment="dev",
            allowProd=False,
            tenantList=" * ",
            pipelineTrigger="tenant_baseline",
            taint=False,
        )
        self.assertEqual(everyone.tenantList, "*")
        self.assertIsNone(everyone.selectedService)
        with self.assertRaises(ValidationError):
            DeploymentRequest(
                targetEnvironment="dev",
                allowProd=False,
                tenantList="atlas",
                pipelineTrigger="tenant_services",
                taint=False,
                singleServiceDeploy=True,
            )
        ignored = DeploymentRequest(
            targetEnvironment="dev",
            allowProd=False,
            tenantList="atlas",
            pipelineTrigger="tenant_baseline",
            taint=False,
            singleServiceDeploy=True,
            selectedService="airflow",
        )
        self.assertFalse(ignored.singleServiceDeploy)
        self.assertIsNone(ignored.selectedService)
        one_service = DeploymentRequest(
            targetEnvironment="dev",
            allowProd=False,
            tenantList="atlas",
            pipelineTrigger="tenant_services",
            taint=False,
            singleServiceDeploy=True,
            selectedService="airflow",
        )
        self.assertEqual(one_service.selectedService, "airflow")
        cleared = DeploymentRequest(
            targetEnvironment="dev",
            allowProd=False,
            tenantList="atlas",
            pipelineTrigger="tenant_baseline",
            taint=False,
            singleServiceDeploy=False,
            selectedService="airflow",
        )
        self.assertIsNone(cleared.selectedService)
        nested = DeploymentRequest(
            targetEnvironment="dev",
            allowProd=False,
            tenantList="CARs/Hyundai/kona, CARs/Toyota/camry, CARs/Hyundai/kona",
            pipelineTrigger="tenant_baseline",
            taint=False,
        )
        self.assertEqual(nested.tenantList, "CARs/Hyundai/kona,CARs/Toyota/camry")
        for bad in ("CARs/../kona", "CARs//kona", "/CARs/Hyundai/kona", "CARs/Hyundai/kona/a/b/c/d"):
            with self.assertRaises(ValidationError):
                DeploymentRequest(
                    targetEnvironment="dev",
                    allowProd=False,
                    tenantList=bad,
                    pipelineTrigger="tenant_baseline",
                    taint=False,
                )

    def test_full_release_skips_only_the_named_environment(self):
        rules = parse_skip_list(
            "\n".join(
                [
                    "# comment",
                    "dev CARs/Broken/stall pipeline exits on purpose",
                    "prod CARs/Broken/flat pipeline exits on purpose",
                    "nope not-a-path ignored",
                ]
            )
        )
        running, skipped = partition_tenants(
            ["CARs/Audi/a4", "CARs/Broken/stall", "CARs/Broken/flat"],
            rules["dev"],
        )
        self.assertEqual(running, ["CARs/Audi/a4", "CARs/Broken/flat"])
        self.assertEqual(skipped, [("CARs/Broken/stall", "pipeline exits on purpose")])
        self.assertEqual(read_skipped(format_skipped(skipped)), [
            {"path": "CARs/Broken/stall", "reason": "pipeline exits on purpose"},
        ])

    def test_https_refuses_the_default_session_secret(self):
        saved = (
            Config.SESSION_SECRET_KEY,
            Config.SESSION_COOKIE_SECURE,
            Config.KEYCLOAK_CLIENT_SECRET,
        )
        try:
            Config.KEYCLOAK_CLIENT_SECRET = "present-secret-value"
            Config.SESSION_SECRET_KEY = "dev-session-secret-change-me"
            Config.SESSION_COOKIE_SECURE = True
            with self.assertRaises(RuntimeError):
                assert_runtime_config()
            Config.SESSION_COOKIE_SECURE = False
            assert_runtime_config()
            Config.SESSION_SECRET_KEY = "short"
            with self.assertRaises(RuntimeError):
                assert_runtime_config()
        finally:
            (
                Config.SESSION_SECRET_KEY,
                Config.SESSION_COOKIE_SECURE,
                Config.KEYCLOAK_CLIENT_SECRET,
            ) = saved

    def test_sample_fixture_keeps_real_rows_and_reimports_cleanly(self):
        _release(self.path, tenant_list="kept-tenant", gitlab_pipeline_id=42)
        first = import_sample_releases(str(FIXTURE), self.path)
        second = import_sample_releases(str(FIXTURE), self.path)

        self.assertEqual(first, 18)
        self.assertEqual(second, 18)
        releases = list_releases(self.path)
        self.assertEqual(len(releases), 19)
        self.assertTrue(any(row["tenantList"] == "kept-tenant" for row in releases))

        audit = list_prod_audit(self.path)
        self.assertEqual(len(audit), 4)
        self.assertTrue(all(row["crNumber"] for row in audit))
        self.assertNotIn("kept-tenant", prod_audit_csv(self.path))
        dev_rows = [row for row in releases if row["environment"] == "dev"]
        self.assertTrue(dev_rows)
        self.assertTrue(all(row["crNumber"] is None for row in dev_rows))


class CicdFilterTests(unittest.TestCase):
    def test_disabled_cicd_is_left_out(self):
        self.assertFalse(cicd_enabled({"builds_access_level": "disabled"}))
        self.assertFalse(cicd_enabled({"jobs_enabled": False}))

    def test_enabled_cicd_stays(self):
        self.assertTrue(cicd_enabled({"builds_access_level": "enabled"}))
        self.assertTrue(cicd_enabled({"builds_access_level": "private"}))
        self.assertTrue(cicd_enabled({"jobs_enabled": True}))
        self.assertTrue(cicd_enabled({}))


if __name__ == "__main__":
    unittest.main()
