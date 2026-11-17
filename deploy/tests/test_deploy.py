"""Run the real deployment shell with a fake Docker CLI; never touch a daemon."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "steamdeck" / "deploy.sh"
IMAGE = "ghcr.io/xqt-debug/rag-center@sha256:" + "a" * 64
FAKE = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as f:
    f.write(json.dumps(a) + '\n')
s = ' '.join(a)
fail = os.environ.get('FAIL', '')
if fail == 'MIGRATION_ONLY' and a[-4:] == ['run', '--rm', '--no-deps', 'migrate']:
    sys.exit(1)
if fail and fail in s:
    sys.exit(1)
if a[0] == 'inspect':
    fmt = a[2]
    if 'Health.Status' in fmt: print('healthy')
    elif '.Name' in fmt: print('/rag-center-db-1')
    elif '.Mounts' in fmt: print(os.environ['RAG_DEPLOY_DIR'] + '/postgres')
    elif '.Running' in fmt: print('true')
    else: print('started 0')
elif 'ps' in a:
    if a[-1] == 'db': print('db-id')
    elif a[-1] == 'caddy':
        if os.environ.get('CADDY_RUNNING'): print('caddy-id')
    elif '--status' in a: print('old-api\nold-worker')
    else: print('worker-id')
elif 'pg_dump' in a: print('fake archive')
elif 'pg_restore' in a: print('archive contents')
"""


@unittest.skipUnless(os.name == "posix", "Requires Linux bash/flock/tar")
class DeploymentFailures(unittest.TestCase):
    def run_deploy(self, failure="", image=IMAGE, https=False, caddy_image=IMAGE, running=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name in ("postgres", "files", "bin"):
            (root / name).mkdir()
        for name in (".env", "app.env", "compose.yaml"):
            (root / name).write_text("original settings\n")
        (root / "files" / "note").write_text("original file")
        if https:
            (root / "caddy.env").write_text("ALIYUN_ACCESS_KEY_ID=test\nALIYUN_ACCESS_KEY_SECRET=test\n")
        docker = root / "bin" / "docker"
        docker.write_text(FAKE)
        docker.chmod(0o755)
        env = dict(
            os.environ,
            PATH=f"{root / 'bin'}:{os.environ['PATH']}",
            HOME=str(root),
            RAG_DEPLOY_DIR=str(root),
            CALLS=str(root / "calls"),
            FAIL=failure,
            RAG_WORKER_STABILITY_SECONDS="0",
            CADDY_RUNNING="1" if running else "",
        )
        command = ["bash", str(SCRIPT), image]
        if caddy_image:
            command.append(caddy_image)
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        calls = (
            [json.loads(line) for line in (root / "calls").read_text().splitlines()]
            if (root / "calls").exists()
            else []
        )
        return result, calls, root

    def test_success_orders_writers_backup_migration_and_records_digest(self):
        result, calls, root = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        joined = [" ".join(c) for c in calls]
        stop = next(i for i, c in enumerate(joined) if "stop -t" in c)
        dump = next(i for i, c in enumerate(joined) if "pg_dump" in c)
        migrate = next(i for i, c in enumerate(joined) if c.endswith("run --rm --no-deps migrate"))
        self.assertLess(stop, dump)
        self.assertLess(dump, migrate)
        self.assertIn(IMAGE, (root / "release.env").read_text())
        backup = next((root / "backups").iterdir())
        self.assertTrue((backup / "COMPLETE").exists())
        self.assertEqual((backup / "app.env").read_text(), "original settings\n")
        self.assertTrue((backup / "files.tar.gz").is_file())

    def test_pull_failure_does_not_stop_writers(self):
        result, calls, root = self.run_deploy("pull api")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("stop" in c for c in calls))
        self.assertFalse((root / "release.env").exists())

    def test_backup_failure_restarts_original_ids(self):
        result, calls, root = self.run_deploy("pg_dump")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(["start", "old-api"], calls)
        self.assertIn(["start", "old-worker"], calls)
        self.assertFalse((root / "release.env").exists())

    def test_failed_archive_validation_restarts_original_ids(self):
        result, calls, _ = self.run_deploy("pg_restore")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(["start", "old-api"], calls)

    def test_migration_start_and_health_failures_leave_writers_stopped(self):
        for failure in ("run --rm --no-deps migrate", "up -d", "SELECT count", ".State.Running"):
            with self.subTest(failure=failure):
                # The migration failure must not match the preflight command.
                match = failure if failure != "run --rm --no-deps migrate" else "MIGRATION_ONLY"
                result, calls, root = self.run_deploy(match)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(c[0] == "start" for c in calls))
                self.assertEqual(calls[-1][-3:], ["stop", "api", "worker"])
                self.assertFalse((root / "release.env").exists())

    def test_mutable_tag_rejected(self):
        result, calls, _ = self.run_deploy(image="ghcr.io/xqt-debug/rag-center:main")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_https_preflight_failures_do_not_stop_writers(self):
        for failure in ("pull caddy", "validate --config", "--service-ports", "RAG_PUBLIC_URL"):
            with self.subTest(failure=failure):
                result, calls, root = self.run_deploy(failure, https=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("stop" in c for c in calls))
                self.assertFalse((root / "release.env").exists())

    def test_https_requires_immutable_caddy_image_before_stopping(self):
        for caddy_image in ("", "ghcr.io/xqt-debug/rag-center-caddy:latest"):
            with self.subTest(caddy_image=caddy_image):
                result, calls, _ = self.run_deploy(https=True, caddy_image=caddy_image)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("stop" in c for c in calls))

    def test_https_keeps_configuration_after_temporary_bundle_is_removed(self):
        result, calls, root = self.run_deploy(https=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CADDY_IMAGE=" + IMAGE, (root / "release.env").read_text())
        self.assertTrue((root / "Caddyfile").is_file())
        self.assertTrue((root / "compose.https.yaml").is_file())
        backup = next((root / "backups").iterdir())
        self.assertTrue((backup / "caddy.env").is_file())
        joined = [" ".join(c) for c in calls]
        start = next(i for i, c in enumerate(joined) if c.endswith("--wait-timeout 180 api worker"))
        proxy = next(i for i, c in enumerate(joined) if c.endswith("--force-recreate caddy"))
        self.assertLess(start, proxy)

    def test_running_caddy_does_not_trigger_conflicting_port_probe(self):
        result, calls, _ = self.run_deploy(https=True, running=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any("--service-ports" in c for c in calls))

    def test_https_acceptance_failure_does_not_record_success(self):
        result, calls, root = self.run_deploy("ProxyConnection", https=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls[-1][-3:], ["stop", "api", "worker"])
        self.assertFalse((root / "release.env").exists())
