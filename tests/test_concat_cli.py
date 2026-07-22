from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


CLI = Path(__file__).parents[1] / "concat.py"


def test_noninteractive_cli_refuses_to_replace_existing_output(
    tmp_path: Path,
) -> None:
    (tmp_path / "source.mp4").write_bytes(b"source")
    output = tmp_path / "output.mp4"
    output.write_bytes(b"keep-existing")

    result = subprocess.run(
        [sys.executable, str(CLI), str(tmp_path)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert "--overwrite" in result.stderr
    assert output.read_bytes() == b"keep-existing"


def test_cli_rejects_an_output_symlink_that_targets_a_source(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-must-survive")
    output_link = tmp_path / "output.mp4"
    try:
        output_link.symlink_to(source)
    except OSError as error:
        pytest.skip(f"Creating symlinks is unavailable: {error}")

    result = subprocess.run(
        [sys.executable, str(CLI), str(tmp_path), "--overwrite"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 1
    assert "output file cannot" in result.stderr
    assert source.read_bytes() == b"source-must-survive"
    assert output_link.is_symlink()
