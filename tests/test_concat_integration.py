from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from concat_core import ConcatJob


FFMPEG = shutil.which("ffmpeg")


@pytest.mark.skipif(FFMPEG is None, reason="FFmpeg is not installed")
def test_real_ffmpeg_concat_smoke(tmp_path: Path) -> None:
    inputs: list[Path] = []
    for name, color in (("first clip's.mp4", "red"), ("second.mp4", "blue")):
        video = tmp_path / name
        subprocess.run(
            [
                FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=32x32:d=0.2",
                "-an",
                "-c:v",
                "mpeg4",
                "-q:v",
                "5",
                "-y",
                str(video),
            ],
            check=True,
        )
        inputs.append(video)

    output = tmp_path / "joined.mp4"
    job = ConcatJob(inputs, output)

    assert job.run() == output.resolve()
    assert output.is_file()
    assert output.stat().st_size > 0
    assert all(video.is_file() for video in inputs)
    assert job.manifest_path is not None and not job.manifest_path.exists()
    assert job.staging_path is not None and not job.staging_path.exists()
