from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session")
def mysql_url() -> Iterator[str]:
    name = f"rag-db-test-{uuid.uuid4().hex[:10]}"
    password = "test-root-password"
    image = os.environ.get("MYSQL_TEST_IMAGE", "mysql:8.4")
    run = subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--env",
            f"MYSQL_ROOT_PASSWORD={password}",
            "--env",
            "MYSQL_DATABASE=rag",
            "--publish",
            "127.0.0.1::3306",
            image,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert run.stdout.strip()

    try:
        port_output = subprocess.run(
            ["docker", "port", name, "3306/tcp"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        port = int(port_output.rsplit(":", 1)[1])

        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            probe = subprocess.run(
                [
                    "docker",
                    "exec",
                    "--env",
                    f"MYSQL_PWD={password}",
                    name,
                    "mysqladmin",
                    "ping",
                    "-h",
                    "127.0.0.1",
                    "-uroot",
                    "--silent",
                ],
                capture_output=True,
            )
            if probe.returncode == 0:
                break
            time.sleep(1)
        else:
            logs = subprocess.run(
                ["docker", "logs", name], capture_output=True, text=True
            ).stdout
            pytest.fail(f"MySQL did not become ready:\n{logs}")

        yield f"mysql+asyncmy://root:{password}@127.0.0.1:{port}/rag"
    finally:
        subprocess.run(
            ["docker", "rm", "--force", name],
            check=False,
            capture_output=True,
        )


@pytest.fixture()
def migrated_mysql_url(mysql_url: str) -> str:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = mysql_url
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert migration.returncode == 0, migration.stdout + migration.stderr
    return mysql_url

