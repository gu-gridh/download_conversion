import requests
import json
import sys

from env_config import get_server_url_from_env, load_dotenv

load_dotenv()
SERVER_URL = get_server_url_from_env().rstrip("/")


def process_image(api_url, image_id, quality, formats=None):
    payload = {
        "api_url": api_url,
        "id": image_id,
        "quality": quality,
    }
    if formats:
        payload["formats"] = formats

    response = requests.post(f"{SERVER_URL}/process-image", json=payload)
    return response.json()


def main():
    if len(sys.argv) < 4:
        print("Usage: python client_example.py <api_url> <id> <quality> [formats...]")
        print("Example: python client_example.py https://munch.dh.gu.se/api/painting-images/ 16 1 jpeg png")
        print("Quality: 1=high, 2=half, 3=low")
        sys.exit(1)

    api_url = sys.argv[1]
    image_id = sys.argv[2]
    quality = int(sys.argv[3])
    formats = sys.argv[4:] if len(sys.argv) > 4 else None

    result = process_image(api_url, image_id, quality, formats)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
