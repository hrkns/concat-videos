#!/usr/bin/env python3

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def resolve_output_path(source_folder: Path, output_arg: str | None) -> Path:
    if not output_arg:
        return source_folder / "output.mp4"

    output_path = Path(output_arg).expanduser()

    # If only a filename is provided, place it inside the source folder.
    if output_path.parent == Path('.'):
        return source_folder / output_path.name

    return output_path.resolve()


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
    args = parser.parse_args()

    source_folder = Path(args.folder).expanduser().resolve()

    if not source_folder.exists() or not source_folder.is_dir():
        print(f"Error: '{source_folder}' is not a valid folder.", file=sys.stderr)
        return 1

    output_path = resolve_output_path(source_folder, args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path_resolved = output_path.resolve()

    mp4_files = sorted(
        [
            f.resolve()
            for f in source_folder.iterdir()
            if f.is_file() and f.suffix.lower() == ".mp4" and f.resolve() != output_path_resolved
        ],
        key=lambda p: p.name.lower()
    )

    if not mp4_files:
        print(f"Error: no .mp4 files found in '{source_folder}' (excluding output file if applicable).", file=sys.stderr)
        return 1

    print("Files to concatenate:")
    for f in mp4_files:
        print(f" - {f.name}")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
        concat_file = Path(tmp.name)
        for video_file in mp4_files:
            escaped = str(video_file).replace("'", r"'\\''")
            tmp.write(f"file '{escaped}'\n")

    ffmpeg_cmd = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        str(output_path_resolved),
    ]

    print("\nRunning:")
    print(" ".join(ffmpeg_cmd))

    try:
        subprocess.run(ffmpeg_cmd, check=True)
        print(f"\nDone. Output created at: {output_path_resolved}")
        return 0
    except FileNotFoundError:
        print("Error: ffmpeg was not found in PATH.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"Error: ffmpeg failed with exit code {e.returncode}.", file=sys.stderr)
        return e.returncode
    finally:
        try:
            concat_file.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
