Work Plan: Image Download, Conversion, and Storage Service
1. Objective
Develop a service that accepts a POST request containing an image API URL, image ID, and requested quality. The service should:
Retrieve the original image metadata/file and IIIF image from the specified API.
Download the required source files.
Convert IIIF images into the requested image dimensions, quality, and format.
Store converted images on the server according to the project name.
Check whether a requested image/format/quality already exists before performing any download or conversion.
Provide an accessible download location for each project.
Minimize unnecessary I/O and image-processing operations through an efficient lookup mechanism.
2. Input API
The service should expose a POST endpoint accepting approximately the following parameters:
{
  "api_url": "https://munch.dh.gu.se/api/painting-images/",
  "id": 16,
  "quality": 1
}

For example, the corresponding API resource would be:
https://munch.dh.gu.se/api/painting-images/16/

Input parameters
Parameter	Description
api_url	Base API endpoint for the image resource
id	ID of the image/resource
quality	Requested image quality

Quality levels:
1 — High quality / original or main file
2 — Medium quality / approximately 50% of the original dimensions
3 — Low quality / approximately 25% of the original dimensions
The API response should be inspected to identify both the main image file and the corresponding iiif_file.
3. Project Name Detection
The project name should normally be extracted from the API hostname.
For example:

https://munch.dh.gu.se/api/painting-images/

would produce:
project_name = munch

The expected URL structure is generally:
https://[project_name].dh.gu.se/api/[image_api]/

A parser/regex should therefore be implemented to extract the project name safely.
Conceptually:

^https?://([a-zA-Z0-9-]+)\.dh\.gu\.se/

The extracted project name will determine where files are stored:
/data/cdhdata/public/[project_name]/

For the Munch project:
/data/cdhdata/public/munch/

The parser should validate the hostname rather than blindly accepting arbitrary paths, since the API URL will determine where files are stored.
4. API Retrieval
Once the project and resource ID have been identified, the service should request:
https://munch.dh.gu.se/api/painting-images/16/

The response should be parsed to obtain:
Main/original image file
iiif_file
Relevant image metadata
Any information required to construct IIIF requests
The implementation should not assume that every API has exactly the same response structure. Where possible, the API response format should be validated before processing.
5. Source File Download
The service should download the required source image(s) from the API.
The preferred workflow is:

POST request
    ↓
Validate input
    ↓
Extract project name
    ↓
Build resource URL
    ↓
Request API metadata
    ↓
Identify main file + IIIF file
    ↓
Check local storage
    ↓
Download only if necessary

Downloaded source files should be stored in a predictable project-specific directory.
For example:

/data/cdhdata/public/munch/

with a structure such as:
munch/
├── original/
├── iiif/
├── converted/
└── ...

The exact directory structure should be finalized during implementation.
6. IIIF Image Conversion
The IIIF image should be used as the source for generating derivative images.
The service should support:

Different dimensions/scales
JPEG output
PNG output
Quality levels 1, 2, and 3
Potentially additional formats in the future
The quality mapping should initially be:
Quality	Scale
1	100%
2	50%
3	25%

For example, if the source image is:
4000 × 3000

then:
Quality 1 → 4000 × 3000
Quality 2 → 2000 × 1500
Quality 3 → 1000 × 750

The conversion layer should be designed so that additional sizes and formats can be added later without redesigning the entire service.
7. File Format Conversion
The conversion service should support at least:
JPEG
PNG

The requested output format should be part of the conversion request.
For example:

{
  "api_url": "https://munch.dh.gu.se/api/painting-images/",
  "id": 16,
  "quality": 2,
  "format": "jpeg"
}

The resulting file could be stored as:
/data/cdhdata/public/munch/converted/16/2.jpeg

or using a more descriptive naming convention such as:
/data/cdhdata/public/munch/converted/16/quality-2.jpeg

The final naming convention should encode all properties that affect the output so that different files cannot accidentally overwrite each other.
8. Avoiding Redundant Processing
A key requirement is that the service must not download or convert an image if the requested result already exists.
Before processing a request, the service should check:

Does the source image already exist?
Does the requested quality already exist?
Does the requested format already exist?
Does the existing file correspond to the required version/source?
For example, if this file already exists:
/data/cdhdata/public/munch/converted/16/quality-2.jpeg

and the user requests:
ID = 16
quality = 2
format = JPEG

the service should immediately return the existing file instead of downloading and converting the image again.
9. Efficient File Lookup
Repeated filesystem searches can become expensive when the number of images becomes large. Therefore, the service should avoid repeatedly scanning directories.
A lookup/index mechanism should be implemented.

One possible approach is to maintain a lightweight database or index containing:

project
image_id
quality
format
file_path
source_version
created_at

For example:
munch | 16 | 1 | jpeg | /data/cdhdata/public/munch/... | ...
munch | 16 | 2 | jpeg | /data/cdhdata/public/munch/... | ...
munch | 16 | 3 | png  | /data/cdhdata/public/munch/... | ...

The application can then perform a fast lookup:
(project, image_id, quality, format)

instead of repeatedly performing filesystem traversal.
A lightweight database such as SQLite could be sufficient initially, although the implementation should allow moving to another database if the service grows.

The filesystem should still be treated as the final source of truth: after a lookup, the application should verify that the referenced file actually exists.

10. Recommended Storage Structure
A predictable project-based directory structure should be used.
For example:

/data/cdhdata/public/
├── munch/
│   ├── original/
│   ├── iiif/
│   └── converted/
│       ├── 16/
│       │   ├── quality-1.jpeg
│       │   ├── quality-1.png
│       │   ├── quality-2.jpeg
│       │   └── quality-3.jpeg
│       └── 17/
│           └── ...
│
├── another-project/
│   └── ...

This provides a clear separation between projects and makes the files easy to access independently of the application.
11. Download URLs
Each project should have a corresponding publicly accessible download directory.
For example:

/data/cdhdata/public/munch/

could be exposed through the web server as something like:
https://[server]/munch/

Users should therefore be able to access the generated files directly.
The service should return the download URL in its response.

Example:

{
  "project": "munch",
  "id": 16,
  "quality": 2,
  "format": "jpeg",
  "status": "available",
  "file": "/munch/converted/16/quality-2.jpeg"
}

12. Processing Workflow
The complete workflow should be:
                 POST request
                      │
                      ▼
              Validate parameters
                      │
                      ▼
              Extract project name
                      │
                      ▼
             Build API resource URL
                      │
                      ▼
             Request API metadata
                      │
                      ▼
          ┌────── Local lookup ──────┐
          │                          │
       Exists?                    Missing?
          │                          │
          ▼                          ▼
     Return file             Download source
                                     │
                                     ▼
                              Obtain IIIF image
                                     │
                                     ▼
                              Convert image
                                     │
                                     ▼
                              Save derivative
                                     │
                                     ▼
                              Update index
                                     │
                                     ▼
                              Return URL

The most important optimization is that the lookup should happen before downloading and conversion.
13. API Response
The service should return a consistent response regardless of whether the image had to be generated or already existed.
For example:

{
  "project": "munch",
  "image_id": 16,
  "quality": 2,
  "format": "jpeg",
  "status": "ready",
  "cached": true,
  "file": "/munch/converted/16/quality-2.jpeg"
}

If the file had to be generated:
{
  "project": "munch",
  "image_id": 16,
  "quality": 2,
  "format": "jpeg",
  "status": "ready",
  "cached": false,
  "file": "/munch/converted/16/quality-2.jpeg"
}

14. Validation and Error Handling
The service should validate:
API URL format
Allowed dh.gu.se hostname structure
Project name
Image ID
Quality (1, 2, or 3)
Output format (jpeg, png, etc.)
API response
Existence/accessibility of source files
IIIF image availability
File permissions on the destination directory
Appropriate HTTP responses should be returned, for example:
400 Bad Request
404 Not Found
422 Unprocessable Entity
500 Internal Server Error

The service should also log failed downloads, conversion errors, and API errors.
15. Implementation Phases
Phase 1 — API endpoint
Create POST endpoint.
Define request/response schema.
Validate input parameters.
Extract project name from API URL.
Phase 2 — API integration
Request the image API.
Parse the API response.
Identify the main image and IIIF image.
Implement download functionality.
Phase 3 — Image conversion
Implement IIIF image processing.
Implement quality levels 1, 2, and 3.
Add JPEG and PNG support.
Establish a consistent file naming convention.
Phase 4 — Storage
Implement project-based directory structure.
Save source and converted images.
Configure public access to project directories.
Phase 5 — Caching/indexing
Implement a fast lookup mechanism.
Check for existing files before downloading/converting.
Store metadata about generated files.
Handle missing files and stale index entries.
Phase 6 — API response and download links
Return generated/existing file URLs.
Indicate whether the result was served from cache.
Provide project-level download locations.
Phase 7 — Testing and optimization
Test:
New image download
Existing image lookup
Quality 1/2/3
JPEG/PNG conversion
Multiple projects
Invalid API URLs
Missing image IDs
Missing IIIF files
Concurrent requests for the same image
Large images
Existing files with missing index records
16. Important Design Consideration: Concurrent Requests
The service should also handle the case where two requests arrive at the same time for the same image.
For example:

Request A → image 16 / quality 2 / JPEG
Request B → image 16 / quality 2 / JPEG

Both requests might perform the lookup before either has finished creating the file, resulting in duplicate downloads/conversions.
A locking mechanism should therefore be considered so that only one process generates a particular derivative at a time.

Conceptually:

Check index
    ↓
Check file
    ↓
Acquire image-specific lock
    ↓
Check file again
    ↓
Generate if missing
    ↓
Update index
    ↓
Release lock

This will be particularly important once the service is handling multiple simultaneous users.
17. Final Architecture
The initial architecture can therefore be kept relatively simple:
                    Client
                      │
                      │ POST
                      ▼
              Image Processing API
                      │
          ┌───────────┼────────────┐
          │           │            │
          ▼           ▼            ▼
       API Client   Cache/Index   Converter
          │           │            │
          ▼           ▼            ▼
       DH API       SQLite       IIIF/Image
                                  Processing
                                      │
                                      ▼
                           /data/cdhdata/public/
                                      │
                    ┌─────────────────┴───────────────┐
                    │                                 │
                  munch                         other-project
                    │                                 │
               converted/                         converted/
                    │                                 │
                  files                             files

The main design principle should be:
Resolve the project → look up the requested derivative → reuse it if available → otherwise retrieve the source → convert → store → index → return the download URL.
This approach should make the service efficient, predictable, and extensible while avoiding unnecessary downloads, filesystem scans, and image conversions.