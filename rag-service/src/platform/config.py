from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


SECRET_ROOT = Path("/run/tyt-rpa/secrets")

# 平台运行时数据库密钥（规范 08 §12.1：一个文件一个值，按文件名读取）
_DATABASE_SECRETS = (
    "DATABASE_HOST",
    "DATABASE_PORT",
    "DATABASE_SCHEMA",
    "DATABASE_RUNTIME_USERNAME",
    "DATABASE_RUNTIME_PASSWORD",
    "DATABASE_TLS_CA_CERTIFICATE",
)


def conformance_mode() -> bool:
    return os.environ.get("RPA_CONFORMANCE_MODE", "").lower() == "true"


def _read_secret(name: str, *, required: bool = True) -> str:
    override_root = os.environ.get("RPA_SECRET_ROOT")
    root = Path(override_root) if override_root else SECRET_ROOT
    path = root / name
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        if required:
            raise RuntimeError(f"Required Controller secret is missing: {name}") from None
        return ""
    if required and not value:
        raise RuntimeError(f"Required Controller secret is empty: {name}")
    return value


def database_url_from_secrets() -> tuple[str, Path | None] | None:
    """从 secrets root 构造运行时数据库 URL（规范 08 §12.1）。

    返回 (url, tls_ca_path)；secrets 未提供 DATABASE_HOST 时返回 None
    （由配置层回退 DATABASE_URL）。DATABASE_RUNTIME_HOST 是 Controller
    内部主机限定模式，不下发给应用。
    """
    override_root = os.environ.get("RPA_SECRET_ROOT")
    root = Path(override_root) if override_root else SECRET_ROOT
    host_path = root / "DATABASE_HOST"
    if not host_path.exists():
        return None
    host = host_path.read_text(encoding="utf-8").strip()
    port = _read_secret("DATABASE_PORT")
    schema = _read_secret("DATABASE_SCHEMA")
    username = _read_secret("DATABASE_RUNTIME_USERNAME")
    password = _read_secret("DATABASE_RUNTIME_PASSWORD")
    if not (host and port and schema and username and password):
        raise RuntimeError("Incomplete DATABASE_* secrets under secret root")
    query = ""
    ca_path = root / "DATABASE_TLS_CA_CERTIFICATE"
    if ca_path.exists() and ca_path.read_text(encoding="utf-8").strip():
        query = f"?ssl_ca={quote(str(ca_path))}"
    url = (
        f"mysql+asyncmy://{quote(username)}:{quote(password)}"
        f"@{host}:{port}/{schema}{query}"
    )
    return url, ca_path if query else None


@dataclass(frozen=True)
class PlatformConfig:
    app_key: str
    environment: str
    release_version: str
    source_commit: str
    control_plane_base_url: str
    control_plane_ca_path: Path | None
    service_client_id: str
    service_secret: str
    service_secret_version: str
    signature_version: str = "v1"
    database_url: str | None = None
    database_tls_ca_path: Path | None = None

    @classmethod
    def load(cls) -> "PlatformConfig":
        app_key = os.environ.get("RPA_APP_KEY", "rag-database")
        if conformance_mode():
            return cls(
                app_key=app_key,
                environment="CONFORMANCE",
                release_version=os.environ.get("RPA_RELEASE_VERSION", "conformance"),
                source_commit=os.environ.get("RPA_SOURCE_COMMIT", "unknown"),
                control_plane_base_url=os.environ.get(
                    "RPA_CONTROL_PLANE_PUBLIC_ORIGIN", "http://127.0.0.1"
                ).rstrip("/"),
                control_plane_ca_path=None,
                service_client_id=os.environ.get("TEST_HMAC_CLIENT_ID", "conformance-client"),
                service_secret=os.environ.get("TEST_HMAC_SECRET", "conformance-secret"),
                service_secret_version=os.environ.get("TEST_HMAC_SECRET_VERSION", "1"),
            )

        environment = os.environ.get("RPA_ENVIRONMENT", "LOCAL")
        if environment == "LOCAL":
            return cls(
                app_key=app_key,
                environment=environment,
                release_version=os.environ.get("RPA_RELEASE_VERSION", "dev"),
                source_commit=os.environ.get("RPA_SOURCE_COMMIT", "local"),
                control_plane_base_url="",
                control_plane_ca_path=None,
                service_client_id="",
                service_secret="",
                service_secret_version="",
            )

        ca_path = SECRET_ROOT / "CONTROL_PLANE_TLS_CA_CERTIFICATE"
        if os.environ.get("RPA_SECRET_ROOT"):
            ca_path = Path(os.environ["RPA_SECRET_ROOT"]) / ca_path.name
        _read_secret(ca_path.name)
        secrets_database = database_url_from_secrets()
        database_url = secrets_database[0] if secrets_database is not None else None
        database_ca = secrets_database[1] if secrets_database is not None else None
        return cls(
            app_key=app_key,
            environment=environment,
            release_version=os.environ["RPA_RELEASE_VERSION"],
            source_commit=os.environ["RPA_SOURCE_COMMIT"],
            control_plane_base_url=_read_secret("RPA_CONTROL_PLANE_BASE_URL").rstrip("/"),
            control_plane_ca_path=ca_path,
            service_client_id=_read_secret("RPA_PROJECT_SERVICE_CLIENT_ID"),
            service_secret=_read_secret("RPA_PROJECT_SERVICE_SECRET"),
            service_secret_version=_read_secret("RPA_PROJECT_SERVICE_SECRET_VERSION"),
            database_url=database_url,
            database_tls_ca_path=database_ca,
        )
