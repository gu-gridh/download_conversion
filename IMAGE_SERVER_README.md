# Image Processing Server

A FastAPI-based server for receiving image processing requests via POST, downloading images from APIs, converting them to different quality levels and formats, and storing them organized by project names.

## Features

- **POST endpoint** to receive `api_url`, `id`, and `quality` parameters
- **Pydantic models** for request validation with automatic error responses
- **IIIF image support** - downloads and parses image data from API endpoints
- **Quality levels**:
  - Quality 1: High (100% scale)
  - Quality 2: Half (50% scale)
  - Quality 3: Low (25% scale)
- **Format conversion**: JPEG and PNG output
- **Project-based storage**: Files organized by project name parsed from API URL
- **Efficient lookup**: SQLite index with indexed columns for O(1) existence checks
- **Caching**: Skips redundant downloads and conversions if files already exist
- **Automatic API docs**: Interactive Swagger UI at `/docs` and ReDoc at `/redoc`

## Installation

```bash
pip install -r requirements.txt
```

### Configure Environment

```bash
cp .env.example .env
```

Edit `.env` for your machine/server before running.

## Usage

### Start the Server

```bash
python image_server.py
```

The server runs on `http://0.0.0.0:5000`. Interactive API documentation is available at `http://localhost:5000/docs`.

### Send a POST Request

```bash
curl -X POST http://localhost:5000/process-image \
  -H "Content-Type: application/json" \
  -d '{
    "api_url": "https://munch.dh.gu.se/api/painting-images/",
    "id": "16",
    "quality": 1,
    "formats": ["jpeg", "png"]
  }'
```

### Send a Fully Local Mock Request (No External API)

Use this endpoint to test conversion, saving, and download URLs without relying on remote image services.

```bash
curl -X POST http://localhost:5000/process-image-local \
  -H "Content-Type: application/json" \
  -d '{
    "project_name": "local-project",
    "id": "42",
    "quality": 2,
    "formats": ["jpeg", "png"],
    "width": 120,
    "height": 80,
    "color": "#336699"
  }'
```

Then test file delivery from the generated download URL:

```bash
curl -I http://localhost:5000/downloads/local-project/converted/42/2/42-2.jpeg
```

### Request Parameters

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `api_url` | string | Yes | Base API URL (e.g., `https://munch.dh.gu.se/api/painting-images/`) |
| `project_name` | string | No | Optional explicit project name override. If omitted, parsed from `api_url` hostname. Useful when the URL hostname doesn't match the desired storage project name |
| `id` | string | Yes | Image identifier |
| `quality` | int | Yes | Quality level: 1 (high), 2 (half), 3 (low) |
| `formats` | list | No | Output formats: `["jpeg", "png"]` (default: `["jpeg", "png"]`) |
| `annotations` | string | No | Annotation API URL including the frontend's current filters. All pages are fetched and drawn onto the image |
| `focus` | bool | No | When `true`, the image is rendered as CSS `grayscale(1) brightness(0.45)` before annotations are drawn (default: `false`) |

### Annotations and Focus Mode

```bash
curl -X POST http://localhost:5000/process-image \
  -H "Content-Type: application/json" \
  -d '{
    "api_url": "https://munch.dh.gu.se/api/painting-images/",
    "id": "29",
    "annotations": "https://munch.dh.gu.se/api/annotation/?panel=solen&category=6",
    "quality": 3,
    "formats": ["jpeg"],
    "focus": true
  }'
```

The annotation URL is fetched (following `next` pagination links) and each annotation's geometry parser is chosen by `target.selector.type`:

| Selector | Used by | Drawn as |
|----------|---------|----------|
| `SvgSelector` `<polygon>` | Munch | Closed outline, no fill, colour from `body.categories[0].color` (fallback `#ff0000`) |
| `SvgSelector` `<polygon data-point="true">` | Munch | Outline plus fill in the same colour at 35% opacity. Points smaller than a few pixels are drawn as a small solid dot (radius 1.25 px per 1000 px of the longest side) so they stay visible |
| `SvgSelector` `<polyline>` | Munch | Open outline (last point not joined to the first) |
| `FragmentSelector` `xywh=percent:x,y,w,h` | Saint Sophia | Rectangle, no fill, white dotted outline |

SVG coordinates are 0–1 and fragment coordinates are 0–100 percent; both are multiplied by the output image size after resizing to the requested quality. Stroke widths are 1.5 px per 1000 px of the image's longest side.

Annotated or focused renderings are stored in a sub-directory named after a hash of the drawn shapes and the focus flag (`converted/{id}/{quality}/{variant}/...`). The annotation API is queried on every request, so a cached rendering is reused only while the annotations are unchanged. These variant files are not added to the SQLite index, so they do not appear in the `/projects` listings.

### Local Mock Request Parameters

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `project_name` | string | Yes | Local storage project name |
| `id` | string | Yes | Image identifier |
| `quality` | int | Yes | Quality level: 1 (high), 2 (half), 3 (low) |
| `formats` | list | No | Output formats: `["jpeg", "png"]` |
| `width` | int | No | Mock source image width (default: 200) |
| `height` | int | No | Mock source image height (default: 200) |
| `color` | string | No | Mock source image color (default: `#4a6d89`) |

### Response

```json
{
  "project_name": "painting-images",
  "image_id": "16",
  "quality": 1,
  "quality_label": "high",
  "scale": 1.0,
  "api_url": "https://munch.dh.gu.se/api/painting-images/",
  "download_url": "https://munch.dh.gu.se/api/painting-images/16/",
  "files": {
    "jpeg": {
      "status": "created",
      "path": "./image_storage/painting-images/16/high/jpeg/image.jpg",
      "size_bytes": 12345,
      "format": "JPEG",
      "scale": 1.0
    }
  }
}
```

### File Status Values

- `created` - File was freshly converted and saved
- `cached` - File already existed, no processing was done
- `error` - Processing failed (error details in `message` field)

### Query Stored Files

```bash
# List all files for a project (by project name)
curl http://localhost:5000/projects/munch

# List files for a project using api_url or project_name query params
curl "http://localhost:5000/projects?api_url=https://munch.dh.gu.se/api/painting-images/"
curl "http://localhost:5000/projects?project_name=munch"

# List files for a specific image
curl http://localhost:5000/projects/munch/16

# List files for a specific image using api_url or project_name query params
curl "http://localhost:5000/projects-by-query/16?api_url=https://munch.dh.gu.se/api/painting-images/"
curl "http://localhost:5000/projects-by-query/16?project_name=munch"
```

## Storage Structure

```
image_storage/
  {project_name}/
    converted/
      {image_id}/
        {quality}/
          {base_name}-{quality}.jpeg
          {base_name}-{quality}.png
          {variant}/                      # only for annotated / focus renderings
            {base_name}-{quality}.jpeg
```

## Regex API Parsing

The project name is extracted from the API URL using this pattern:

```python
import re
API_URL_PATTERN = re.compile(r"https?://(?P<domain>[^/]+)/(?:.*/)?(?P<project>[^/]+)/?$")
```

Examples:
- `https://munch.dh.gu.se/api/painting-images/` → `painting-images`
- `https://example.com/api/artworks/` → `artworks`

## Lookup Mechanism

Files are tracked in a SQLite database (`image_storage/.file_index.db`) with indexed columns on `(project_name, image_id, quality, file_format)`. This provides O(1) lookups to check if a requested file already exists before performing I/O-intensive download and conversion operations.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `IMAGE_STORAGE_ROOT` | `./image_storage` | Root directory for stored files |
| `IMAGE_INDEX_DB` | empty -> `${IMAGE_STORAGE_ROOT}/.file_index.db` | SQLite database path |
| `IMAGE_SERVER_HOST` | `0.0.0.0` | Bind host used by `image_server.py` |
| `IMAGE_SERVER_PORT` | `5000` | Bind port used by `image_server.py` |
| `IMAGE_SERVER_URL` | `http://localhost:5000` | Base URL used by `client_example.py` |
| `IMAGE_DOWNLOAD_BASE_URL` | empty | Base URL prefix for download URLs in API responses (e.g. `https://data.dh.gu.se`). When set, returned URLs are absolute like `https://data.dh.gu.se/{project}/converted/{id}/{quality}/{name}-{quality}.{ext}`. When empty, relative `/downloads/...` paths are returned |
| `IMAGE_MAX_IMAGE_PIXELS` | `default` | Pillow max pixel limit (`none`, `default`, or integer) |

## Running Tests

```bash
python test_image_processor.py
```

## Client Example

```bash
python client_example.py https://munch.dh.gu.se/api/painting-images/ 16 1 jpeg png
```

## Standalone CLI (No HTTP Server)

Run `image_processor` directly without starting the FastAPI server. Useful for one-off conversions or testing without exposing the HTTP endpoint.

```bash
python process_cli.py \
  --api_url https://munch.dh.gu.se/api/painting-images/ \
  --id 16 \
  --quality 2 \
  --formats jpeg png
```

Override the project name explicitly (useful when the URL hostname differs from the desired storage project):

```bash
python process_cli.py \
  --api_url https://saintsophia.dh.gu.se/api/inscriptions/image/ \
  --id 49 \
  --quality 1 \
  --project_name saintsophia
```

Options:
- `--api_url` (required): Base API URL
- `--id` (required): Image identifier
- `--quality` (required): 1 (high), 2 (half), or 3 (low)
- `--project_name`: Optional project name override
- `--formats`: Output formats (default: `jpeg png`)
- `--annotations`: Annotation API URL to draw onto the image
- `--focus`: Grayscale + 45% brightness before drawing annotations
- `--storage_root`: Override storage root
- `--index_db`: Override SQLite index DB path
- `--pretty`: Pretty-print JSON output
