"""通过真实 Compose 解析器验证密钥覆盖，不启动或访问 Docker daemon。"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "steamdeck"


@unittest.skipUnless(shutil.which("docker"), "Requires the Docker Compose CLI")
class SecretCompose(unittest.TestCase):
    def test_raw_secret_preserves_special_characters_and_overrides_app_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(SOURCE / "compose.yaml", root / "compose.yaml")
            (root / ".env").write_text("POSTGRES_PASSWORD=test-password\n")
            (root / "app.env").write_text("RAG_SECRET=old-signing\nRAG_EMBEDDING_API_KEY=old-key\n")
            (root / "cloudflared.env").write_text("TUNNEL_TOKEN=test-token\n")
            api_key = "test-$dollar${variable}-'single'-\"double\"-#hash-\\slash"
            signing = "test-signing-secret-12345678901234567890"
            (root / "app-secrets.env").write_text(f"RAG_SECRET={signing}\nRAG_EMBEDDING_API_KEY={api_key}\n")
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    str(root / ".env"),
                    "-f",
                    str(root / "compose.yaml"),
                    "--profile",
                    "tools",
                    "config",
                    "--format",
                    "json",
                ],
                env={
                    **os.environ,
                    "RAG_IMAGE": "ghcr.io/xqt-debug/rag-center@sha256:" + "a" * 64,
                    "POSTGRES_PASSWORD": "test-password",
                },
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            config = json.loads(result.stdout)
            for service in ("api", "worker", "migrate"):
                with self.subTest(service=service):
                    env = config["services"][service]["environment"]
                    self.assertEqual(env["RAG_SECRET"], signing)
                    # Compose 导出为可再次加载的配置时会将 $ 转义成 $$。
                    self.assertEqual(env["RAG_EMBEDDING_API_KEY"], api_key.replace("$", "$$"))
                    self.assertIn("rag:test-password@db:5432/rag", env["RAG_DATABASE_URL"])
