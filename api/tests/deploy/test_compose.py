"""Compose darf einen gesetzten API-Schreibschutz nicht vom Proxy entkoppeln."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.yml"


@pytest.fixture
def compose_project(tmp_path):
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose ist nicht installiert")
    if "traefik.http.middlewares" not in COMPOSE.read_text():
        pytest.skip("Self-Hosted Compose ohne Box-Traefik-Labels")
    version = subprocess.run(["docker", "compose", "version"], capture_output=True)
    if version.returncode:
        pytest.skip("Docker Compose ist nicht installiert")
    shutil.copy(COMPOSE, tmp_path / "docker-compose.yml")
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / ".env").write_text("KBA_API_WRITE_TOKEN=test-write-token\n")
    return tmp_path


def resolve(project, *, explicit_env, shell_token=None):
    cmd = ["docker", "compose"]
    if explicit_env:
        cmd += ["--env-file", "api/.env"]
    cmd += ["config", "--format", "json"]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KBA_", "COMPOSE_"))}
    if shell_token is not None:
        env["KBA_API_WRITE_TOKEN"] = shell_token
    return subprocess.run(cmd, cwd=project, env=env, capture_output=True, text=True)


def test_missing_interpolation_token_stops_deployment(compose_project):
    # env_file setzt die Container-Variable, aber KEINE Compose-Interpolation.
    # Genau das erzeugte live einen leeren Proxy-Header und HTTP 401 für Hermes.
    result = resolve(compose_project, explicit_env=False)
    assert result.returncode != 0, "Deploy mit leerem Traefik-Schreib-Token wurde akzeptiert"
    assert "KBA_API_WRITE_TOKEN" in result.stderr


def test_explicit_env_keeps_proxy_and_api_token_equal(compose_project):
    result = resolve(compose_project, explicit_env=True)
    assert result.returncode == 0, result.stderr
    api = json.loads(result.stdout)["services"]["api"]
    header = "traefik.http.middlewares.kba-writetoken.headers.customrequestheaders."
    assert api["labels"][header + "X-KBA-Write-Token"] == api["environment"]["KBA_API_WRITE_TOKEN"]


def test_shell_override_keeps_proxy_and_api_token_equal(compose_project):
    # Shell-Werte haben Vorrang vor --env-file, nicht aber vor service.env_file.
    # Proxy und API müssen deshalb dieselbe interpolierte Quelle verwenden.
    result = resolve(compose_project, explicit_env=True, shell_token="shell-write-token")
    assert result.returncode == 0, result.stderr
    api = json.loads(result.stdout)["services"]["api"]
    header = "traefik.http.middlewares.kba-writetoken.headers.customrequestheaders."
    assert api["labels"][header + "X-KBA-Write-Token"] == api["environment"]["KBA_API_WRITE_TOKEN"]


def test_nginx_rejects_removed_html_routes_instead_of_the_catch_all():
    """Fehlende .html-Dateien liefern 404 statt still index.html (200)."""
    conf = (Path(__file__).resolve().parents[3] / "deploy" / "cockpit-nginx.conf").read_text()
    assert r"location ~ \.html$" in conf
    assert "try_files $uri =404;" in conf
