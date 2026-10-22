#!/usr/bin/env python3
"""Standalone CLI for running image_processor directly (no HTTP server).

Usage:
    python process_cli.py --api_url https://munch.dh.gu.se/api/painting-images/ --id 16 --quality 2
    python process_cli.py --api_url https://munch.dh.gu.se/api/painting-images/ --id 16 --quality 2 --project_name munch
    python process_cli.py --api_url https://saintsophia.dh.gu.se/api/inscriptions/image/ --id 49 --quality 1 --project_name saintsophia
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from image_processor import ImageProcessor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert images from a project API to multiple quality levels and formats.",
    )
    parser.add_argument(
        "--api_url",
        required=True,
        help="Base API URL, e.g. https://munch.dh.gu.se/api/painting-images/",
    )
    parser.add_argument(
        "--id",
        required=True,
        help="Image identifier",
    )
    parser.add_argument(
        "--quality",
        required=True,
        type=int,
        choices=[1, 2, 3],
        help="Quality level: 1 (high), 2 (half), 3 (low)",
    )
    parser.add_argument(
        "--project_name",
        default=None,
        help="Optional explicit project name override. If omitted, parsed from api_url",
    )
    parser.add_argument(
        "--formats",
        nargs="+",
        default=["jpeg", "png"],
        help="Output formats, e.g. jpeg png (default: jpeg png)",
    )
    parser.add_argument(
        "--annotations",
        default=None,
        help="Annotation API URL (with filters) whose annotations are drawn onto the image",
    )
    parser.add_argument(
        "--focus",
        action="store_true",
        help="Render the image in grayscale at 45%% brightness before drawing annotations",
    )
    parser.add_argument(
        "--storage_root",
        default=None,
        help="Override storage root (defaults to IMAGE_STORAGE_ROOT env or ./image_storage)",
    )
    parser.add_argument(
        "--index_db",
        default=None,
        help="Override SQLite index DB path",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    processor = ImageProcessor(
        storage_root=Path(args.storage_root) if args.storage_root else None,
        index_db=Path(args.index_db) if args.index_db else None,
    )

    try:
        result = processor.process_request(
            api_url=args.api_url,
            image_id=str(args.id),
            quality=args.quality,
            formats=args.formats,
            project_name=args.project_name,
            annotations_url=args.annotations,
            focus=args.focus,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent))
    return 0


if __name__ == "__main__":
    sys.exit(main())