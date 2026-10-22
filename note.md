I want to wirte a code to be able to receive a post request that includes:
API url
id
quality 
and then be able to sownlaid file and iiif_file from provided api and id like this:
api: https://munch.dh.gu.se/api/painting-images/
id : 16
quality : 1
and then download the file and iiif_file from the provided API and ID with this_ https://munch.dh.gu.se/api/painting-images/16/

Qulaity 1 is high and main file, quality 2 is half quality(50%) and 3 (25%) is the low quality.

Then we should be able to convert iiif images to different quality sizes and formats like JPEG or PNG.
Then we can save them somewhere on our server based on project names to avoid conversion later in case they are already in server.
Before we start the conversion and saving process, we should check if the requested quality and format already exist on the server to avoid redundant work. For this we have to find a way to make an efficient lookup mechanism to avoid slow I/O operations.

We need some kind of regex to parse the API and based on project name look up the corresponding files on the server.

Then we can have the link to downlod folder for each project, so users can easily access the converted and stored images and also send it to server to be able to download images.

Server folder: /data/cdhdata/public/[project_name]
project name in url usually it's first part before dh.gu.se like munch in munch.dh.gu.se
usually apis are endpoints under the project name, like https://[project_name].dh.gu.se/api/[image_api]/.



Build a backend service that receives a POST request containing an api_url, image id, quality, and output format. The service should retrieve image information from a DH/Gu.se API, download the required source/IIIF image, convert it to the requested quality and format, store the result on the server, and return a downloadable URL.
Example input:

{
  "api_url": "https://munch.dh.gu.se/api/painting-images/",
  "id": 16,
  "quality": 1,
  "format": "jpeg"
}

The resource endpoint is constructed as:
https://munch.dh.gu.se/api/painting-images/16/

Quality levels are:

1 = 100% / high quality
2 = 50% dimensions
3 = 25% dimensions
The API response contains a main file and an iiif_file. Use the IIIF image as the source for generating resized derivatives and support at least JPEG and PNG output.
Files must be stored under:

/data/cdhdata/public/[project_name]/

Extract [project_name] from the API hostname. For example, munch should be extracted from munch.dh.gu.se. The expected API pattern is generally:
https://[project_name].dh.gu.se/api/[endpoint]/

Use safe hostname parsing/regex validation rather than accepting arbitrary paths.
The most important requirement is caching: before downloading or converting anything, check whether the requested project, image ID, quality, and format already exist. Avoid expensive filesystem scans by implementing an efficient lookup/index, preferably using a lightweight database such as SQLite. The filesystem should still be verified when a cached record is found.

Use a predictable directory and filename structure, for example:

/data/cdhdata/public/munch/converted/16/quality-2.jpeg

The API response should indicate whether the file was already cached and provide its download URL.
Also account for concurrent requests for the same image/quality/format so that two requests do not perform the same conversion simultaneously.

Please structure the code cleanly with separate components for API retrieval, project-name parsing, caching/indexing, image conversion, storage, and the HTTP API. Include validation, error handling, logging, and tests for the main workflow.