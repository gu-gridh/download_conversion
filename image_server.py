from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from env_config import get_index_db_from_env, get_server_host_from_env, get_server_port_from_env, get_storage_root_from_env, load_dotenv
from image_processor import ImageProcessor, parse_api_url

app = FastAPI(title="Image Processing Server")

load_dotenv()

storage_root = get_storage_root_from_env()
index_db = get_index_db_from_env(storage_root)
processor = ImageProcessor(storage_root=storage_root, index_db=index_db)
app.mount("/downloads", StaticFiles(directory=str(processor.storage_root)), name="downloads")


class ProcessImageRequest(BaseModel):
    api_url: str = Field(..., description="Base API URL (e.g., https://munch.dh.gu.se/api/painting-images/)")
    project_name: Optional[str] = Field(None, description="Optional explicit project name override. If omitted, parsed from api_url")
    id: str = Field(..., description="Image identifier")
    quality: int = Field(..., description="Quality level: 1 (high), 2 (half), 3 (low)", ge=1, le=3)
    format: Optional[str] = Field(default="jpeg", description="Output format, e.g. jpeg or png")
    formats: Optional[List[str]] = Field(None, description="Optional list of output formats, e.g., ['jpeg', 'png']")
    annotations: Optional[str] = Field(None, description="Annotation API URL (with filters) whose annotations are drawn onto the image")
    focus: bool = Field(default=False, description="Render the image in grayscale at 45% brightness before drawing annotations")


class ProcessImageResponse(BaseModel):
    project_name: str
    image_id: str
    quality: int
    quality_label: str
    scale: float
    api_url: str
    download_url: str
    files: dict
    raw_download: Optional[dict] = None
    iiif_info: Optional[dict] = None
    annotations: Optional[dict] = None
    focus: bool = False


class ProcessImageLocalRequest(BaseModel):
    project_name: str = Field(..., description="Local project name used for storage path")
    id: str = Field(..., description="Image identifier")
    quality: int = Field(..., description="Quality level: 1 (high), 2 (half), 3 (low)", ge=1, le=3)
    format: Optional[str] = Field(default="jpeg", description="Output format, e.g. jpeg or png")
    formats: Optional[List[str]] = Field(None, description="Optional list of output formats, e.g., ['jpeg', 'png']")
    width: int = Field(default=200, ge=1, description="Mock source image width")
    height: int = Field(default=200, ge=1, description="Mock source image height")
    color: str = Field(default="#4a6d89", description="Mock source image color")


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/process-image", response_model=ProcessImageResponse)
def process_image(request: ProcessImageRequest):
    if request.project_name:
        project_name = request.project_name.strip()
    else:
        project_name = parse_api_url(request.api_url)
    if not project_name:
        raise HTTPException(status_code=400, detail=f"Could not determine project name from api_url: {request.api_url}")

    formats = request.formats
    if formats is None:
        formats = [request.format or "jpeg"]
    elif request.format and request.format not in formats:
        formats = [request.format, *formats]

    try:
        result = processor.process_request(
            request.api_url,
            str(request.id),
            request.quality,
            formats,
            project_name=project_name,
            annotations_url=request.annotations,
            focus=request.focus,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/process-image-local", response_model=ProcessImageResponse)
def process_image_local(request: ProcessImageLocalRequest):
    formats = request.formats
    if formats is None:
        formats = [request.format or "jpeg"]
    elif request.format and request.format not in formats:
        formats = [request.format, *formats]

    try:
        return processor.process_local_mock_request(
            project_name=request.project_name,
            image_id=str(request.id),
            quality=request.quality,
            formats=formats,
            width=request.width,
            height=request.height,
            color=request.color,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/projects")
def list_project_files_by_query(api_url: Optional[str] = None, project_name: Optional[str] = None):
    resolved_project = project_name
    if not resolved_project and api_url:
        resolved_project = parse_api_url(api_url)
    if not resolved_project:
        raise HTTPException(status_code=400, detail="Provide either project_name or api_url")
    files = processor.index.get_by_project(resolved_project)
    return {"project_name": resolved_project, "api_url": api_url, "files": files}


@app.get("/projects/{project_name}")
def list_project_files(project_name: str):
    files = processor.index.get_by_project(project_name)
    return {"project_name": project_name, "files": files}


@app.get("/projects/{project_name}/{image_id}")
def get_image_files(project_name: str, image_id: str):
    files = processor.index.get_by_project(project_name)
    filtered = [f for f in files if f["image_id"] == image_id]
    return {"project_name": project_name, "image_id": image_id, "files": filtered}


@app.get("/projects-by-query/{image_id}")
def get_image_files_by_query(image_id: str, api_url: Optional[str] = None, project_name: Optional[str] = None):
    resolved_project = project_name
    if not resolved_project and api_url:
        resolved_project = parse_api_url(api_url)
    if not resolved_project:
        raise HTTPException(status_code=400, detail="Provide either project_name or api_url")
    files = processor.index.get_by_project(resolved_project)
    filtered = [f for f in files if f["image_id"] == image_id]
    return {"project_name": resolved_project, "image_id": image_id, "api_url": api_url, "files": filtered}


if __name__ == "__main__":
    import uvicorn
    host = get_server_host_from_env()
    port = get_server_port_from_env()
    uvicorn.run(app, host=host, port=port)
