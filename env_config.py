import os
from pathlib import Path
from typing import Dict, Optional

DEFAULT_STORAGE_ROOT = Path("./image_storage")
DEFAULT_DB_FILENAME = ".file_index.db"
DEFAULT_SERVER_HOST = "0.0.0.0"
DEFAULT_SERVER_PORT = 5000
DEFAULT_SERVER_URL = "http://localhost:5000"
DEFAULT_INDEX_ENABLED = True
DEFAULT_DOWNLOAD_BASE_URL = ""


def _parse_dotenv_value(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        return ""

    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1]
    return value


def _parse_dotenv_line(line: str) -> Optional[tuple[str, str]]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()

    if "=" not in stripped:
        return None

    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None

    value = _parse_dotenv_value(raw_value)
    return key, value


def load_dotenv_file(dotenv_path: Path) -> Dict[str, str]:
    loaded: Dict[str, str] = {}
    if not dotenv_path.exists() or not dotenv_path.is_file():
        return loaded

    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_dotenv_line(line)
        if not parsed:
            continue
        key, value = parsed
        if key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def load_dotenv() -> Optional[Path]:
    current_dir = Path(__file__).resolve().parent
    candidates = [
        Path.cwd() / ".env",
        current_dir / ".env",
        current_dir.parent / ".env",
    ]

    seen = set()
    for candidate in candidates:
        normalized = candidate.resolve() if candidate.exists() else candidate
        if str(normalized) in seen:
            continue
        seen.add(str(normalized))

        if candidate.exists() and candidate.is_file():
            load_dotenv_file(candidate)
            return candidate
    return None


def get_storage_root_from_env() -> Path:
    configured = os.environ.get("IMAGE_STORAGE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_STORAGE_ROOT


def get_index_db_from_env(storage_root: Optional[Path] = None) -> Path:
    configured = os.environ.get("IMAGE_INDEX_DB", "").strip()
    if configured:
        return Path(configured).expanduser()

    effective_storage_root = Path(storage_root) if storage_root is not None else get_storage_root_from_env()
    return effective_storage_root / DEFAULT_DB_FILENAME


def get_server_host_from_env() -> str:
    value = os.environ.get("IMAGE_SERVER_HOST", "").strip()
    return value or DEFAULT_SERVER_HOST


def get_server_port_from_env() -> int:
    value = os.environ.get("IMAGE_SERVER_PORT", "").strip()
    if not value:
        return DEFAULT_SERVER_PORT
    try:
        return int(value)
    except ValueError:
        return DEFAULT_SERVER_PORT


def get_server_url_from_env() -> str:
    value = os.environ.get("IMAGE_SERVER_URL", "").strip()
    return value or DEFAULT_SERVER_URL


def get_download_base_url_from_env() -> str:
    value = os.environ.get("IMAGE_DOWNLOAD_BASE_URL", "").strip()
    return value or DEFAULT_DOWNLOAD_BASE_URL


def _parse_bool_env(value: str, default: bool) -> bool:
    normalized = (value or "").strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def get_index_enabled_from_env() -> bool:
    value = os.environ.get("IMAGE_INDEX_ENABLED", "")
    return _parse_bool_env(value, DEFAULT_INDEX_ENABLED)
