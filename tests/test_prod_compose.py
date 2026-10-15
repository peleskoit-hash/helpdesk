"""Validate PROD Compose without reading real env files or printing secrets.

Run: python tests/test_prod_compose.py (requires Docker Compose).
"""
import json
import os
from pathlib import Path
import subprocess
import unittest


class ProdComposeTest(unittest.TestCase):
    def test_bitrix_key_is_required_and_passed_to_api(self):
        env = dict(os.environ, ESDESK_ENV_FILE=os.devnull,
                   POSTGRES_DB="test", POSTGRES_USER="test",
                   POSTGRES_PASSWORD="test", DATABASE_URL="postgresql://test:test@db/test",
                   APP_TIMEZONE="UTC", ATTACHMENT_STORAGE_DIR="/tmp/attachments")
        for value in (None, "", "compose-test-key"):
            with self.subTest(key_state="missing" if value is None else "set" if value else "empty"):
                env.pop("BITRIX_PASSWORD_ENCRYPTION_KEY", None)
                if value is not None:
                    env["BITRIX_PASSWORD_ENCRYPTION_KEY"] = value
                result = subprocess.run(
                    ["docker", "compose", "--env-file", os.devnull, "-f",
                     "docker-compose.prod.yml", "config", "--format", "json"],
                    cwd=Path(__file__).resolve().parents[1], env=env,
                    capture_output=True, text=True, check=False,
                )
                if value:
                    self.assertTrue(result.returncode == 0, "Compose config validation failed")
                    config = json.loads(result.stdout)
                    self.assertTrue(
                        config["services"]["api"]["environment"].get("BITRIX_PASSWORD_ENCRYPTION_KEY") == value,
                        "API encryption key was not passed through",
                    )
                else:
                    self.assertTrue(result.returncode != 0, "Missing or empty key must fail")
                    self.assertIn("BITRIX_PASSWORD_ENCRYPTION_KEY", result.stderr)


if __name__ == "__main__":
    unittest.main()
