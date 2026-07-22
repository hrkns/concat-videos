from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from concat_core import ConcatJob, ConcatProgress


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None,
    reason="FFmpeg and FFprobe are required",
)
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
    progress_updates: list[ConcatProgress] = []

    assert job.run(progress_callback=progress_updates.append) == output.resolve()
    assert output.is_file()
    assert output.stat().st_size > 0
    assert all(video.is_file() for video in inputs)
    assert job.manifest_path is not None and not job.manifest_path.exists()
    assert job.staging_path is not None and not job.staging_path.exists()
    assert progress_updates[0].fraction == 0.0
    assert any(
        update.fraction is not None and 0 < update.fraction < 1
        for update in progress_updates
    )
    assert progress_updates[-1].fraction == 1.0
    assert progress_updates[-1].eta_seconds == 0.0
