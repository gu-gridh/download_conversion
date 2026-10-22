import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import requests
from PIL import Image, UnidentifiedImageError
from PIL import Image as PILImage

from annotations import apply_focus, draw_annotations, fetch_annotations, parse_annotations, render_variant_key
from env_config import get_index_db_from_env, get_index_enabled_from_env, get_storage_root_from_env, get_download_base_url_from_env, load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

DEFAULT_STORAGE_ROOT = get_storage_root_from_env()
DEFAULT_DB_PATH = get_index_db_from_env(DEFAULT_STORAGE_ROOT)
DOWNLOAD_BASE_URL = get_download_base_url_from_env()

# No default Pillow pixel cap so very large trusted images can be processed.
DEFAULT_MAX_IMAGE_PIXELS = None

QUALITY_SCALES = {
    1: 1.0,
    2: 0.5,
    3: 0.25,
}

QUALITY_LABELS = {
    1: "high",
    2: "medium",
    3: "low",
}

VALID_FORMATS = {"jpeg", "jpg", "png"}
DH_GU_PROJECT_PATTERN = re.compile(r"^(?P<project>[a-z0-9][a-z0-9-]*)\.dh\.gu\.se$", re.IGNORECASE)


def normalize_format(file_format: str) -> str:
    if not file_format:
        return "jpeg"
    value = file_format.strip().lower().replace("jpg", "jpeg")
    if value not in VALID_FORMATS:
        raise ValueError(f"Unsupported image format: {file_format}")
    return value


def parse_api_url(api_url: str) -> Optional[str]:
    if not api_url:
        return None
    try:
        parsed = urlparse(api_url.strip())
    except ValueError:
        return None

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return None

    match = DH_GU_PROJECT_PATTERN.fullmatch(hostname)
    if not match:
        return None

    project_name = match.group("project")
    if not project_name:
        return None
    return project_name


class FileIndex:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else get_index_db_from_env()
        self.db_path = self.db_path.expanduser()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _db_path_candidates(self) -> List[Path]:
        candidates: List[Path] = [self.db_path]

        cwd_candidate = Path.cwd() / ".file_index.db"
        temp_candidate = Path(tempfile.gettempdir()) / "download_conversion_file_index.db"

        for candidate in (cwd_candidate, temp_candidate):
            if candidate not in candidates:
                candidates.append(candidate)
        return candidates

    def _connect_for_init(self, candidate_db_path: Path):
        candidate_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(candidate_db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        init_errors: List[str] = []

        for candidate in self._db_path_candidates():
            try:
                conn = self._connect_for_init(candidate)
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS files (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_name TEXT NOT NULL,
                        image_id TEXT NOT NULL,
                        quality INTEGER NOT NULL,
                        file_format TEXT NOT NULL,
                        file_hash TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        UNIQUE(project_name, image_id, quality, file_format)
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_lookup
                    ON files(project_name, image_id, quality, file_format)
                    """
                )
                conn.commit()
                conn.close()

                if candidate != self.db_path:
                    logger.warning(
                        "Configured IMAGE_INDEX_DB '%s' is not writable. Falling back to '%s'.",
                        self.db_path,
                        candidate,
                    )
                self.db_path = candidate
                return
            except (OSError, sqlite3.OperationalError) as exc:
                init_errors.append(f"{candidate}: {exc}")

        joined_errors = "; ".join(init_errors)
        raise sqlite3.OperationalError(f"Unable to open SQLite index database. Tried: {joined_errors}")

    def exists(self, project_name: str, image_id: str, quality: int, file_format: str) -> Optional[str]:
        normalized_format = normalize_format(file_format)
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT file_path FROM files WHERE project_name = ? AND image_id = ? AND quality = ? AND file_format = ?",
            (project_name, str(image_id), int(quality), normalized_format),
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            path = row["file_path"]
            if os.path.exists(path):
                return path
            self.delete(project_name, image_id, quality, normalized_format)
        return None

    def insert(
        self,
        project_name: str,
        image_id: str,
        quality: int,
        file_format: str,
        file_path: str,
        file_hash: str,
    ):
        normalized_format = normalize_format(file_format)
        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT OR REPLACE INTO files (project_name, image_id, quality, file_format, file_hash, file_path) VALUES (?, ?, ?, ?, ?, ?)",
                (project_name, str(image_id), int(quality), normalized_format, file_hash, file_path),
            )
            conn.commit()
        finally:
            conn.close()

    def delete(self, project_name: str, image_id: str, quality: int, file_format: str):
        normalized_format = normalize_format(file_format)
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM files WHERE project_name = ? AND image_id = ? AND quality = ? AND file_format = ?",
            (project_name, str(image_id), int(quality), normalized_format),
        )
        conn.commit()
        conn.close()

    def get_by_project(self, project_name: str) -> List[Dict]:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT project_name, image_id, quality, file_format, file_path FROM files WHERE project_name = ?",
            (project_name,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {"project_name": r["project_name"], "image_id": r["image_id"], "quality": r["quality"], "file_format": r["file_format"], "file_path": r["file_path"]}
            for r in rows
        ]


class InMemoryIndex:
    def __init__(self):
        self._records: Dict[Tuple[str, str, int, str], Dict[str, str]] = {}
        self._lock = threading.Lock()

    def exists(self, project_name: str, image_id: str, quality: int, file_format: str) -> Optional[str]:
        key = (project_name, str(image_id), int(quality), normalize_format(file_format))
        with self._lock:
            record = self._records.get(key)
            if not record:
                return None
            file_path = record["file_path"]
            if os.path.exists(file_path):
                return file_path
            self._records.pop(key, None)
            return None

    def insert(
        self,
        project_name: str,
        image_id: str,
        quality: int,
        file_format: str,
        file_path: str,
        file_hash: str,
    ):
        key = (project_name, str(image_id), int(quality), normalize_format(file_format))
        with self._lock:
            self._records[key] = {
                "project_name": project_name,
                "image_id": str(image_id),
                "quality": int(quality),
                "file_format": normalize_format(file_format),
                "file_path": file_path,
                "file_hash": file_hash,
            }

    def delete(self, project_name: str, image_id: str, quality: int, file_format: str):
        key = (project_name, str(image_id), int(quality), normalize_format(file_format))
        with self._lock:
            self._records.pop(key, None)

    def get_by_project(self, project_name: str) -> List[Dict]:
        with self._lock:
            return [
                {
                    "project_name": record["project_name"],
                    "image_id": record["image_id"],
                    "quality": record["quality"],
                    "file_format": record["file_format"],
                    "file_path": record["file_path"],
                }
                for record in self._records.values()
                if record["project_name"] == project_name
            ]


def compute_file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ImageProcessor:
    def __init__(self, storage_root: Optional[Path] = None, index_db: Optional[Path] = None):
        self.storage_root = Path(storage_root) if storage_root is not None else get_storage_root_from_env()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        if get_index_enabled_from_env():
            resolved_index_db = Path(index_db) if index_db is not None else get_index_db_from_env(self.storage_root)
            self.index = FileIndex(resolved_index_db)
        else:
            logger.warning("SQLite index disabled via IMAGE_INDEX_ENABLED. Using in-memory index (non-persistent).")
            self.index = InMemoryIndex()
        self._request_locks: Dict[Tuple[str, str, int], threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._configure_pillow_limits()

    def _configure_pillow_limits(self):
        configured_value = os.environ.get("IMAGE_MAX_IMAGE_PIXELS", "").strip()
        if configured_value.lower() in {"", "default"}:
            max_pixels = DEFAULT_MAX_IMAGE_PIXELS
        elif configured_value.lower() in {"none", "unlimited", "off"}:
            max_pixels = None
        else:
            try:
                max_pixels = int(configured_value)
            except ValueError:
                logger.warning(
                    "Invalid IMAGE_MAX_IMAGE_PIXELS=%s. Falling back to default=%s",
                    configured_value,
                    DEFAULT_MAX_IMAGE_PIXELS,
                )
                max_pixels = DEFAULT_MAX_IMAGE_PIXELS

        PILImage.MAX_IMAGE_PIXELS = max_pixels
        logger.info("Pillow MAX_IMAGE_PIXELS set to %s", max_pixels)

    def _sanitize_base_name(self, value: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
        sanitized = sanitized.strip("._-")
        return sanitized or "image"

    def _base_name_from_url(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            parsed = urlparse(value)
            path = parsed.path or ""
        except ValueError:
            path = value
        basename = Path(unquote(path)).name
        if not basename:
            return None
        stem = Path(basename).stem
        return self._sanitize_base_name(stem) if stem else None

    def resolve_output_base_name(self, api_resource: Dict, selected_source_url: Optional[str], image_id: str) -> str:
        candidates: List[Optional[str]] = []
        if isinstance(api_resource, dict):
            for key in ("main_file", "file", "image_url", "iiif_file", "iiif_url"):
                value = api_resource.get(key)
                if isinstance(value, str):
                    candidates.append(self._base_name_from_url(value))

        candidates.append(self._base_name_from_url(selected_source_url))
        candidates.append(self._sanitize_base_name(str(image_id)))

        for candidate in candidates:
            if candidate:
                return candidate
        return "image"

    def _lock_for(self, project_name: str, image_id: str, quality: int):
        key = (project_name, str(image_id), int(quality))
        with self._locks_guard:
            lock = self._request_locks.setdefault(key, threading.Lock())
        return lock

    def normalize_project_name(self, project_name: str) -> str:
        return project_name.strip().lower()

    def get_project_storage_root(self, project_name: str) -> Path:
        return self.storage_root / self.normalize_project_name(project_name)

    def get_storage_path(
        self,
        project_name: str,
        image_id: str,
        quality: int,
        file_format: str,
        output_base_name: Optional[str] = None,
        variant: Optional[str] = None,
    ) -> Path:
        normalized_format = normalize_format(file_format)
        quality_value = int(quality)
        base_name = self._sanitize_base_name(output_base_name or str(image_id))
        file_name = f"{base_name}-{quality_value}.{normalized_format}"
        directory = self.get_project_storage_root(project_name) / "converted" / str(image_id) / str(quality_value)
        if variant:
            directory = directory / variant
        return directory / file_name

    def find_existing(self, project_name: str, image_id: str, quality: int, file_format: str, variant: Optional[str] = None) -> Optional[str]:
        if not variant:
            return self.index.exists(project_name, str(image_id), int(quality), file_format)
        # Annotated/focused renderings live in a per-variant directory and are not indexed.
        directory = self.get_storage_path(project_name, image_id, quality, file_format, variant=variant).parent
        suffix = f"-{int(quality)}.{normalize_format(file_format)}"
        if directory.is_dir():
            for candidate in directory.iterdir():
                if candidate.is_file() and candidate.name.endswith(suffix):
                    return str(candidate)
        return None

    def build_download_url(self, project_name: str, image_id: str, quality: int, file_format: str, output_base_name: Optional[str] = None) -> str:
        normalized_format = normalize_format(file_format)
        quality_value = int(quality)
        base_name = self._sanitize_base_name(output_base_name or str(image_id))
        relative_path = f"{self.normalize_project_name(project_name)}/converted/{str(image_id)}/{quality_value}/{base_name}-{quality_value}.{normalized_format}"
        if DOWNLOAD_BASE_URL:
            return f"{DOWNLOAD_BASE_URL.rstrip('/')}/{relative_path}"
        return f"/downloads/{relative_path}"

    def build_download_url_from_path(self, file_path: str) -> str:
        relative_path = Path(file_path).resolve().relative_to(self.storage_root.resolve())
        relative = "/".join(relative_path.parts)
        if DOWNLOAD_BASE_URL:
            return f"{DOWNLOAD_BASE_URL.rstrip('/')}/{relative}"
        return f"/downloads/{relative}"

    def download_image(self, url: str, timeout: Optional[int] = None) -> Tuple[bytes, str]:
        response = requests.get(url, timeout=timeout, stream=True)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "application/octet-stream")
        buffer = io.BytesIO()
        for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
            if chunk:
                buffer.write(chunk)
        return buffer.getvalue(), content_type

    def fetch_api_resource(self, api_url: str, image_id: str) -> Dict:
        resource_url = f"{api_url.rstrip('/')}/{image_id}/"
        try:
            response = requests.get(resource_url, timeout=60)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ValueError(f"Failed to fetch resource for image {image_id}: {exc}") from exc

        payload = response.content
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type or payload.lstrip()[:1] in (b"{", b"["):
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return {"raw_url": resource_url, "content_type": content_type, "size": len(payload)}
        return {"raw_url": resource_url, "content_type": content_type, "size": len(payload)}

    def resolve_source_image_url(self, api_resource: Dict, resource_url: str) -> Optional[str]:
        candidates = self.resolve_source_image_urls(api_resource, resource_url)
        return candidates[0] if candidates else None

    def resolve_source_image_urls(self, api_resource: Dict, resource_url: str) -> List[str]:
        candidates: List[str] = []

        def add_candidate(value: Optional[str]):
            if not value:
                return
            if value.startswith("http"):
                candidate = value
            else:
                candidate = f"{resource_url.rsplit('/', 2)[0]}/{value.lstrip('/')}"
            if candidate not in candidates:
                candidates.append(candidate)

        if not isinstance(api_resource, dict):
            add_candidate(resource_url)
            return candidates

        for key in ("iiif_file", "iiif_url", "iiif", "main_file", "file", "image_url", "source_url"):
            value = api_resource.get(key)
            if isinstance(value, str) and value:
                add_candidate(value)
            if isinstance(value, dict):
                nested = value.get("url") or value.get("href")
                if nested:
                    add_candidate(nested)

        if "files" in api_resource and isinstance(api_resource["files"], dict):
            for nested_key in ("iiif_file", "iiif", "image"):
                nested = api_resource["files"].get(nested_key)
                if isinstance(nested, dict):
                    url_value = nested.get("url") or nested.get("href")
                    if url_value:
                        add_candidate(url_value)

        if not candidates:
            add_candidate(resource_url)
        return candidates

    def download_iiif_info(self, api_url: str, image_id: str) -> Optional[Dict]:
        resource_url = f"{api_url.rstrip('/')}/{image_id}/"
        try:
            payload = self.fetch_api_resource(api_url, image_id)
            if isinstance(payload, dict):
                source_urls = self.resolve_source_image_urls(payload, resource_url)
                if source_urls:
                    return {"source_url": source_urls[0], "source_urls": source_urls, "payload": payload}
            return {"raw_url": resource_url, "payload": payload}
        except ValueError as exc:
            logger.warning("Could not fetch API resource for image %s: %s", image_id, exc)
            return None

    def save_file(
        self,
        data: bytes,
        project_name: str,
        image_id: str,
        quality: int,
        file_format: str,
        output_base_name: Optional[str] = None,
        variant: Optional[str] = None,
    ) -> Path:
        normalized_format = normalize_format(file_format)
        target_path = self.get_storage_path(project_name, image_id, quality, normalized_format, output_base_name=output_base_name, variant=variant)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        file_hash = compute_file_hash(data)

        with open(target_path, "wb") as fh:
            fh.write(data)

        if not variant:
            self.index.insert(project_name, str(image_id), quality, normalized_format, str(target_path), file_hash)
        return target_path

    def convert_image_data(
        self,
        image_data: bytes,
        quality: int,
        target_format: str,
        focus: bool = False,
        shapes: Optional[List[Dict]] = None,
    ) -> Tuple[bytes, str]:
        normalized_format = normalize_format(target_format)
        scale = QUALITY_SCALES.get(int(quality), 1.0)
        format_upper = "JPEG" if normalized_format == "jpeg" else "PNG"

        with Image.open(io.BytesIO(image_data)) as img:
            if img.mode == "P":
                img = img.convert("RGBA" if "transparency" in img.info else "RGB")
            if img.mode == "CMYK":
                img = img.convert("RGB")

            new_width = max(1, int(img.size[0] * scale))
            new_height = max(1, int(img.size[1] * scale))
            img_resized = img.resize((new_width, new_height), Image.LANCZOS) if scale < 1.0 else img
            # Focus dims the photo first so annotation colours keep full brightness.
            if focus:
                img_resized = apply_focus(img_resized)
            if shapes:
                img_resized = draw_annotations(img_resized, shapes)

            buffer = io.BytesIO()
            save_kwargs = {}
            if format_upper == "JPEG":
                if img_resized.mode in ("RGBA", "P", "LA"):
                    img_resized = img_resized.convert("RGB")
                save_kwargs["quality"] = 95 if quality == 1 else (80 if quality == 2 else 70)
                save_kwargs["optimize"] = True
            elif format_upper == "PNG":
                save_kwargs["optimize"] = True

            img_resized.save(buffer, format=format_upper, **save_kwargs)
            return buffer.getvalue(), normalized_format

    def _generate_mock_image_data(self, width: int = 200, height: int = 200, color: str = "#4a6d89") -> bytes:
        img = Image.new("RGB", (max(1, int(width)), max(1, int(height))), color=color)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=95, optimize=True)
        return buffer.getvalue()

    def process_local_mock_request(
        self,
        project_name: str,
        image_id: str,
        quality: int,
        formats: Optional[List[str]] = None,
        width: int = 200,
        height: int = 200,
        color: str = "#4a6d89",
    ) -> Dict:
        normalized_project = self.normalize_project_name(project_name)
        requested_formats = []
        for fmt in (formats or ["jpeg", "png"]):
            requested_formats.append(normalize_format(fmt))

        results = {
            "project_name": normalized_project,
            "image_id": str(image_id),
            "quality": int(quality),
            "quality_label": QUALITY_LABELS.get(int(quality), f"q{quality}"),
            "scale": QUALITY_SCALES.get(int(quality), 1.0),
            "api_url": "local-mock",
            "download_url": f"local-mock://{normalized_project}/{image_id}",
            "files": {},
            "mock_source": {
                "width": max(1, int(width)),
                "height": max(1, int(height)),
                "color": color,
            },
        }

        missing_formats = []
        for fmt in requested_formats:
            existing = self.index.exists(normalized_project, str(image_id), int(quality), fmt)
            if existing:
                results["files"][fmt] = {
                    "status": "cached",
                    "path": existing,
                    "download_url": self.build_download_url_from_path(existing),
                }
            else:
                missing_formats.append(fmt)

        if not missing_formats:
            return results

        request_lock = self._lock_for(normalized_project, str(image_id), int(quality))
        with request_lock:
            for fmt in list(missing_formats):
                existing = self.index.exists(normalized_project, str(image_id), int(quality), fmt)
                if existing:
                    results["files"][fmt] = {
                        "status": "cached",
                        "path": existing,
                        "download_url": self.build_download_url_from_path(existing),
                    }
                    missing_formats.remove(fmt)

            if not missing_formats:
                return results

            source_data = self._generate_mock_image_data(width=width, height=height, color=color)
            output_base_name = self._sanitize_base_name(str(image_id))

            for fmt in missing_formats:
                converted_data, converted_format = self.convert_image_data(source_data, quality, fmt)
                file_path = self.save_file(
                    converted_data,
                    normalized_project,
                    str(image_id),
                    int(quality),
                    converted_format,
                    output_base_name=output_base_name,
                )
                results["files"][fmt] = {
                    "status": "created",
                    "path": str(file_path),
                    "size_bytes": len(converted_data),
                    "format": converted_format,
                    "scale": results["scale"],
                    "download_url": self.build_download_url(
                        normalized_project,
                        str(image_id),
                        int(quality),
                        converted_format,
                        output_base_name=output_base_name,
                    ),
                }

        return results

    def process_request(
        self,
        api_url: str,
        image_id: str,
        quality: int,
        formats: Optional[List[str]] = None,
        project_name: Optional[str] = None,
        annotations_url: Optional[str] = None,
        focus: bool = False,
    ) -> Dict:
        if project_name:
            project_name = self.normalize_project_name(project_name)
        else:
            project_name = self.normalize_project_name(parse_api_url(api_url))
        if not project_name:
            raise ValueError(f"Could not determine project name from api_url: {api_url}")

        requested_formats = []
        for fmt in (formats or ["jpeg", "png"]):
            requested_formats.append(normalize_format(fmt))

        results = {
            "project_name": project_name,
            "image_id": str(image_id),
            "quality": int(quality),
            "quality_label": QUALITY_LABELS.get(int(quality), f"q{quality}"),
            "api_url": api_url,
            "download_url": f"{api_url.rstrip('/')}/{image_id}/",
            "files": {},
        }
        results["scale"] = QUALITY_SCALES.get(int(quality), 1.0)

        # Annotations are fetched on every request so edits upstream change the cache key.
        shapes: List[Dict] = []
        if annotations_url:
            shapes = parse_annotations(fetch_annotations(annotations_url))
            results["annotations"] = {"url": annotations_url, "count": len(shapes)}
        variant = render_variant_key(shapes, focus)
        results["focus"] = bool(focus)

        missing_formats = []
        for fmt in requested_formats:
            existing = self.find_existing(project_name, str(image_id), int(quality), fmt, variant)
            if existing:
                results["files"][fmt] = {
                    "status": "cached",
                    "path": existing,
                    "download_url": self.build_download_url_from_path(existing),
                }
            else:
                missing_formats.append(fmt)

        if not missing_formats:
            return results

        request_lock = self._lock_for(project_name, str(image_id), int(quality))
        with request_lock:
            for fmt in list(missing_formats):
                existing = self.find_existing(project_name, str(image_id), int(quality), fmt, variant)
                if existing:
                    results["files"][fmt] = {
                        "status": "cached",
                        "path": existing,
                        "download_url": self.build_download_url_from_path(existing),
                    }
                    missing_formats.remove(fmt)

            if not missing_formats:
                return results

            resource_url = f"{api_url.rstrip('/')}/{image_id}/"
            api_resource = self.fetch_api_resource(api_url, str(image_id))
            source_urls = self.resolve_source_image_urls(api_resource, resource_url)
            if not source_urls:
                raise ValueError(f"Could not determine a valid IIIF/source image for {resource_url}")

            source_data: Optional[bytes] = None
            content_type: Optional[str] = None
            selected_source_url: Optional[str] = None
            download_errors: List[str] = []

            for source_url in source_urls:
                logger.info("Downloading source image for %s/%s from %s", project_name, image_id, source_url)
                try:
                    source_data, content_type = self.download_image(source_url)
                    selected_source_url = source_url
                    break
                except requests.RequestException as exc:
                    logger.warning("Failed to download source image from %s: %s", source_url, exc)
                    download_errors.append(f"{source_url}: {exc}")

            if source_data is None:
                message = "Failed to download source image from all candidates: " + "; ".join(download_errors)
                for fmt in missing_formats:
                    results["files"][fmt] = {
                        "status": "error",
                        "message": message,
                    }
                return results

            results["raw_download"] = {"content_type": content_type, "size_bytes": len(source_data)}
            api_info = self.download_iiif_info(api_url, str(image_id))
            if api_info:
                results["iiif_info"] = api_info
            if selected_source_url:
                results["source_url"] = selected_source_url

            output_base_name = self.resolve_output_base_name(api_resource, selected_source_url, str(image_id))

            for fmt in missing_formats:
                try:
                    converted_data, converted_format = self.convert_image_data(source_data, quality, fmt, focus=focus, shapes=shapes)
                except PILImage.DecompressionBombError as exc:
                    results["files"][fmt] = {
                        "status": "error",
                        "message": (
                            "Source image is too large for current Pillow limit. "
                            "Increase IMAGE_MAX_IMAGE_PIXELS or set it to 'none' for trusted sources. "
                            f"Details: {exc}"
                        ),
                    }
                except UnidentifiedImageError:
                    results["files"][fmt] = {
                        "status": "error",
                        "message": "Downloaded data is not a recognizable image",
                    }
                    continue
                except Exception as exc:
                    results["files"][fmt] = {
                        "status": "error",
                        "message": str(exc),
                    }
                    continue

                file_path = self.save_file(
                    converted_data,
                    project_name,
                    str(image_id),
                    int(quality),
                    converted_format,
                    output_base_name=output_base_name,
                    variant=variant,
                )
                results["files"][fmt] = {
                    "status": "created",
                    "path": str(file_path),
                    "size_bytes": len(converted_data),
                    "format": converted_format,
                    "scale": results["scale"],
                    "download_url": self.build_download_url_from_path(str(file_path)),
                }

        return results
