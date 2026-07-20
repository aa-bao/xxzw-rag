from __future__ import annotations

import subprocess
import time
import uuid
import os
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
                    name,
                    "mysqladmin",
                    "ping",
                    "-h",
                    "127.0.0.1",
                    "-uroot",
                    f"-p{password}",
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
