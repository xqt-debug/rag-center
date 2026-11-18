"""Run the real deployment shell with a fake Docker CLI; never touch a daemon."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "steamdeck" / "deploy.sh"
IMAGE = "ghcr.io/xqt-debug/rag-center@sha256:" + "a" * 64
TUNNEL_IMAGE = "cloudflare/cloudflared@sha256:" + "b" * 64
SIGNING_SECRET = "existing-production-signing-secret-123456789"
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
if '--images' in a:
    print(os.environ['TEST_TUNNEL_IMAGE'])
elif a[0] == 'ps':
    if os.environ.get('LEGACY_CADDY'): print('legacy-caddy-id')
elif a[0] == 'inspect':
    if len(a) == 2:
        service = 'db' if a[1] == 'rag-center-db-1' else 'api'
        print(json.dumps([{'Name': '/rag-center-' + service + '-1', 'Config': {
            'Labels': {'com.docker.compose.project': 'rag-center', 'com.docker.compose.service': service},
            'Env': ['POSTGRES_PASSWORD=existing-db-password',
                    'RAG_SECRET=existing-production-signing-secret-123456789']}}]))
        sys.exit(0)
    fmt = a[2]
    if 'Health.Status' in fmt: print('healthy')
    elif '.Name' in fmt: print('/rag-center-db-1')
    elif '.Mounts' in fmt: print(os.environ['RAG_DEPLOY_DIR'] + '/postgres')
    elif '.Running' in fmt: print('true')
    else: print('started 0')
elif 'ps' in a:
    if a[-1] == 'db': print('db-id')
    elif '--status' in a: print('old-api\nold-worker')
    else: print('worker-id')
elif 'pg_dump' in a: print('fake archive')
elif 'pg_restore' in a: print('archive contents')
"""


@unittest.skipUnless(os.name == "posix", "Requires Linux bash/flock/tar")
class DeploymentFailures(unittest.TestCase):
    def run_deploy(
        self,
        failure="",
        image=IMAGE,
        tunnel_image=TUNNEL_IMAGE,
        token="a" * 80,
        legacy=False,
        existing_token=None,
        secret_env=None,
        existing_app_secrets=None,
    ):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name in ("postgres", "files", "bin"):
            (root / name).mkdir()
        for name in (".env", "app.env", "compose.yaml"):
            (root / name).write_text("original settings\n")
        (root / "files" / "note").write_text("original file")
        if existing_token is not None:
            (root / "cloudflared.env").write_text("TUNNEL_TOKEN=" + existing_token + "\n")
        if existing_app_secrets is not None:
            (root / "app-secrets.env").write_text(existing_app_secrets)
        if legacy:
            (root / "caddy.env").write_text("legacy credentials\n")
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
            TEST_TUNNEL_IMAGE=tunnel_image,
            LEGACY_CADDY="1" if legacy else "",
            TUNNEL_TOKEN=token or "",
            DEPLOY_POSTGRES_PASSWORD="",
            DEPLOY_RAG_SECRET="",
            DEPLOY_EMBEDDING_API_KEY="",
        )
        env.update(secret_env or {})
        command = ["bash", str(SCRIPT), image]
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

    def test_environment_secret_creates_private_token_file_without_leaking(self):
        token = "test-secret-" + "a" * 70
        result, calls, root = self.run_deploy(token=token)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((root / "cloudflared.env").read_text(), "TUNNEL_TOKEN=" + token + "\n")
        self.assertEqual((root / "cloudflared.env").stat().st_mode & 0o777, 0o600)
        self.assertNotIn(token, result.stdout + result.stderr + json.dumps(calls))

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

    def test_tunnel_preflight_failures_do_not_stop_writers(self):
        for failure in ("pull cloudflared", "RAG_PUBLIC_URL"):
            with self.subTest(failure=failure):
                result, calls, root = self.run_deploy(failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("stop" in c for c in calls))
                self.assertFalse((root / "release.env").exists())

    def test_tunnel_requires_token_before_stopping(self):
        for token in (None, "", "replace-me"):
            with self.subTest(token=token):
                result, calls, _ = self.run_deploy(token=token)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("stop" in c for c in calls))

    def test_tunnel_rejects_mutable_image_before_stopping(self):
        result, calls, _ = self.run_deploy(tunnel_image="cloudflare/cloudflared:latest")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("stop" in c for c in calls))

    def test_tunnel_records_digest_and_only_stops_old_proxy_after_readiness(self):
        result, calls, root = self.run_deploy(legacy=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CLOUDFLARED_IMAGE=" + TUNNEL_IMAGE, (root / "release.env").read_text())
        backup = next((root / "backups").iterdir())
        self.assertTrue((backup / "cloudflared.env").is_file())
        self.assertTrue((backup / "caddy.env").is_file())
        joined = [" ".join(c) for c in calls]
        start = next(i for i, c in enumerate(joined) if c.endswith("--wait-timeout 180 api worker"))
        tunnel = next(i for i, c in enumerate(joined) if c.endswith("up -d --no-deps cloudflared"))
        ready = next(i for i, c in enumerate(joined) if "http://cloudflared:2000/ready" in c)
        stop = calls.index(["stop", "legacy-caddy-id"])
        self.assertLess(start, tunnel)
        self.assertLess(tunnel, ready)
        self.assertLess(ready, stop)

    def test_tunnel_acceptance_failure_preserves_legacy_proxy_and_success_marker(self):
        result, calls, root = self.run_deploy("http://cloudflared:2000/ready", legacy=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls[-1][-3:], ["stop", "api", "worker"])
        self.assertNotIn(["stop", "legacy-caddy-id"], calls)
        self.assertFalse((root / "release.env").exists())

    def test_missing_or_malformed_secret_preserves_existing_token_and_writers(self):
        for token in (None, "invalid", "a" * 80 + "\nINJECTED=yes"):
            with self.subTest(token=token):
                result, calls, root = self.run_deploy(token=token, existing_token="old-token")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((root / "cloudflared.env").read_text(), "TUNNEL_TOKEN=old-token\n")
                self.assertFalse(any("stop" in c for c in calls))

    def test_other_environment_secrets_preserve_values_permissions_and_do_not_leak(self):
        api_key = "key-$dollar${variable}-'quoted'-\"double\"-#hash-\\slash"
        supplied = {
            "DEPLOY_POSTGRES_PASSWORD": "existing-db-password",
            "DEPLOY_RAG_SECRET": SIGNING_SECRET,
            "DEPLOY_EMBEDDING_API_KEY": api_key,
        }
        result, calls, root = self.run_deploy(secret_env=supplied)
        self.assertEqual(result.returncode, 0, result.stderr)
        raw = (root / "app-secrets.env").read_text()
        self.assertIn("RAG_EMBEDDING_API_KEY=" + api_key + "\n", raw)
        self.assertIn("RAG_SECRET=" + SIGNING_SECRET + "\n", raw)
        self.assertIn("POSTGRES_PASSWORD=existing-db-password\n", (root / ".env").read_text())
        self.assertEqual((root / "app-secrets.env").stat().st_mode & 0o777, 0o600)
        output = result.stdout + result.stderr + json.dumps(calls)
        for value in supplied.values():
            self.assertNotIn(value, output)

    def test_missing_optional_secrets_preserves_existing_overrides(self):
        original = "RAG_EMBEDDING_API_KEY=previous-key\nRAG_SECRET=" + SIGNING_SECRET + "\n"
        result, _, root = self.run_deploy(existing_app_secrets=original)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(original, (root / "app-secrets.env").read_text())

    def test_mismatched_or_multiline_secret_fails_before_config_changes_or_stopping(self):
        for secret_env in (
            {"DEPLOY_POSTGRES_PASSWORD": "different-password"},
            {"DEPLOY_RAG_SECRET": "different-production-secret-123456789"},
            {"DEPLOY_EMBEDDING_API_KEY": "key\nRAG_ENV=development"},
        ):
            with self.subTest(keys=list(secret_env)):
                result, calls, root = self.run_deploy(secret_env=secret_env, existing_token="old-token")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("stop" in c for c in calls))
                self.assertEqual((root / ".env").read_text(), "original settings\n")
                self.assertEqual((root / "cloudflared.env").read_text(), "TUNNEL_TOKEN=old-token\n")
                self.assertFalse((root / "app-secrets.env").exists())
