# Download & Conversion Tool

A small service that downloads images from GRIDH project APIs (such as `munch.dh.gu.se` and `saintsophia.dh.gu.se`), resizes them to a quality level, converts them to JPEG or PNG, and stores the results by project so they can be served as static downloads. It can also draw a project's annotations onto the image.

You can use it in two ways:

- **HTTP server** (FastAPI): [image_server.py](image_server.py)
- **Command line** with no server: [process_cli.py](process_cli.py)

## How it works

1. The tool requests `{api_url}/{id}/` and reads the JSON for a source image link (`iiif_file`, `iiif_url`, `main_file`, `file`, `image_url`, …).
2. It downloads the image, scales it to the requested quality, and saves each requested format.
3. If an `annotations` URL is given, it fetches every page of annotations and draws them onto the image. With `focus` on, the image is first turned grayscale and darkened.
4. Each result is recorded in a SQLite index, so asking for the same file again returns the cached copy without downloading it again.

| Quality | Scale |
|---------|-------|
| `1` | 100% (high) |
| `2` | 50% (medium) |
| `3` | 25% (low) |

## Setup

Requires Python 3.9+.

```bash
pip install fastapi uvicorn pydantic requests pillow
cp .env.example .env   # then adjust as needed
```

## Usage

### Server

```bash
python image_server.py
```

The server listens on `http://0.0.0.0:5000` by default. Interactive API docs are at `http://localhost:5000/docs`.

```bash
curl -X POST http://localhost:5000/process-image \
  -H "Content-Type: application/json" \
  -d '{
    "api_url": "https://munch.dh.gu.se/api/painting-images/",
    "id": "16",
    "quality": 2,
    "formats": ["jpeg", "png"]
  }'
```

The response lists every output file with its status (`created`, `cached` or `error`) and a download URL under `/downloads/...`.

### CLI

```bash
python process_cli.py \
  --api_url https://munch.dh.gu.se/api/painting-images/ \
  --id 16 --quality 2 --formats jpeg png --pretty
```

Run `python process_cli.py --help` to see all options.

### Annotations

```bash
python process_cli.py \
  --api_url https://munch.dh.gu.se/api/painting-images/ \
  --id 29 --quality 3 --formats jpeg \
  --annotations "https://munch.dh.gu.se/api/annotation/?panel=solen&category=6" \
  --focus
```

Supported selectors are Munch `SvgSelector` (polygons, points, polylines) and Saint Sophia `FragmentSelector` (`xywh=percent:...`).

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/process-image` | Download, convert and store an image |
| `POST` | `/process-image-local` | Same pipeline with a generated mock image (no external API needed) |
| `GET` | `/projects/{project}` | List stored files for a project |
| `GET` | `/projects/{project}/{id}` | List stored files for one image |
| `GET` | `/downloads/...` | Serve stored files |
| `GET` | `/health` | Health check |

## Project names

By default, the project name comes from the API hostname: `munch.dh.gu.se` → `munch`. For a host that doesn't follow the `{project}.dh.gu.se` pattern, or to store under a different name, pass `project_name` (or `--project_name` on the CLI).

## Storage layout

```
image_storage/
  {project}/converted/{id}/{quality}/
    {name}-{quality}.jpeg
    {name}-{quality}.png
    {variant}/            # annotated / focus renderings
```

## Configuration

These are set in `.env` (see [.env.example](.env.example)):

| Variable | Default | Description |
|----------|---------|-------------|
| `IMAGE_STORAGE_ROOT` | `./image_storage` | Where converted files are stored |
| `IMAGE_INDEX_DB` | `{storage}/.file_index.db` | SQLite index path |
| `IMAGE_INDEX_ENABLED` | `true` | Set `false` for an in-memory index |
| `IMAGE_SERVER_HOST` / `IMAGE_SERVER_PORT` | `0.0.0.0` / `5000` | Server bind address |
| `IMAGE_DOWNLOAD_BASE_URL` | empty | Prefix that makes the returned download URLs absolute |
| `IMAGE_MAX_IMAGE_PIXELS` | `default` | Pillow pixel limit (`none`, `default`, or a number) |

## Files

| File | Role |
|------|------|
| [image_server.py](image_server.py) | FastAPI app and endpoints |
| [process_cli.py](process_cli.py) | Command-line entry point |
| [image_processor.py](image_processor.py) | Download, conversion, storage and index |
| [annotations.py](annotations.py) | Annotation fetching, parsing and drawing |
| [env_config.py](env_config.py) | `.env` loading and settings |
| [client_example.py](client_example.py) | Minimal Python client for the server |

For full request and response details, see [IMAGE_SERVER_README.md](IMAGE_SERVER_README.md).
