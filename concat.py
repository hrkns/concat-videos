#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

from concat_core import (
    ConcatCancelled,
    ConcatError,
    ConcatJob,
    discover_videos,
    resolve_output_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Concatenate all MP4 files in a folder in alphabetical order using ffmpeg."
    )
    parser.add_argument(
        "folder",
        help="Path to the folder containing MP4 files"
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output file path or filename. If only a filename is provided, it will be created inside the source folder. Defaults to source_folder/output.mp4"
    )
    parser.add_argument(
        "-y",
        "--overwrite",
        action="store_true",
        help="Replace an existing output without prompting",
    )
    args = parser.parse_args()

    source_folder = Path(args.folder).expanduser().resolve()

    if not source_folder.exists() or not source_folder.is_dir():
        print(f"Error: '{source_folder}' is not a valid folder.", file=sys.stderr)
        return 1

    output_path = resolve_output_path(source_folder, args.output)
    try:
        mp4_files = discover_videos(source_folder, output_path)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if not mp4_files:
        print(f"Error: no .mp4 files found in '{source_folder}' (excluding output file if applicable).", file=sys.stderr)
        return 1

    print("Files to concatenate:")
    for f in mp4_files:
        print(f" - {f.name}")

    try:
        job = ConcatJob(mp4_files, output_path)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if output_path.exists() and not args.overwrite:
        if not sys.stdin.isatty():
            print(
                f"Error: output already exists: '{output_path}'. "
                "Use --overwrite to replace it.",
                file=sys.stderr,
            )
            return 1
        try:
            answer = input(f"\nOutput already exists: '{output_path}'. Replace it? [y/N] ")
        except EOFError:
            print(
                f"\nError: output already exists: '{output_path}'. "
                "Use --overwrite to replace it.",
                file=sys.stderr,
            )
            return 1
        except KeyboardInterrupt:
            print("\nCancelled; the existing output was not changed.", file=sys.stderr)
            return 130
        if answer.strip().casefold() not in {"y", "yes"}:
            print("Cancelled; the existing output was not changed.")
            return 0

    try:
        job.run(print)
        print(f"\nDone. Output created at: {output_path}")
        return 0
    except ConcatCancelled:
        print("\nConcatenation cancelled.", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        job.cancel()
        print("\nConcatenation cancelled.", file=sys.stderr)
        return 130
    except (ConcatError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return error.exit_code if isinstance(error, ConcatError) and error.exit_code else 1


if __name__ == "__main__":
    raise SystemExit(main())
