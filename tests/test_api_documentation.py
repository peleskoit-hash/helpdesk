import os
import subprocess
import sys
from pathlib import Path

import pytest


CHECK_ROUTES = """
import os

from fastapi.testclient import TestClient
from api.app.main import app, settings

assert settings.app_env == os.environ["APP_ENV"]

# A non-context-managed client does not run lifespan handlers.
client = TestClient(app)
expected = 404 if settings.is_prod else 200
for path in ("/openapi.json", "/docs", "/redoc"):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == expected, (path, response.status_code)

health = client.get("/health")
assert health.status_code == 200 and health.json() == {"ok": True}
assert client.get("/login").status_code == 200
"""


@pytest.mark.parametrize("mode", ["dev", "prod"])
def test_api_documentation_routes_by_environment(mode, tmp_path):
    root = Path(__file__).resolve().parents[1]
    cwd = tmp_path / "isolated"
    cwd.mkdir()
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if key in os.environ}
    env.update(
        PYTHONPATH=str(root),
        PYTHONDONTWRITEBYTECODE="1",
        APP_ENV=mode,
        DATABASE_URL="sqlite:///:memory:",
        SESSION_SECRET_KEY="synthetic-docs-test-secret",
    )
    result = subprocess.run(
        [sys.executable, "-c", CHECK_ROUTES],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
