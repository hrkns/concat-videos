from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from concat_core import (
    ConcatCancelled,
    ConcatError,
    ConcatJob,
    build_ffmpeg_command,
    discover_videos,
    write_concat_manifest,
)


class CompletedProcess:
    """Small deterministic Popen stand-in for completed FFmpeg runs."""

    def __init__(self, returncode: int, output: str = "") -> None:
        self.returncode = returncode
        self._output = output

    def poll(self) -> int:
        return self.returncode

    def communicate(self, timeout: float | None = None) -> tuple[str, None]:
        return self._output, None


class CancellableProcess:
    """Popen stand-in that runs until the job asks it to terminate."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminate_called = threading.Event()
        self.kill_called = threading.Event()

    def poll(self) -> int | None:
        return self.returncode

    def communicate(self, timeout: float | None = None) -> tuple[str, None]:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake-ffmpeg", timeout)
        return "cancelled", None

    def terminate(self) -> None:
        self.terminate_called.set()
        self.returncode = -15

    def kill(self) -> None:
        self.kill_called.set()
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake-ffmpeg", timeout)
        return self.returncode


class KillRequiredProcess(CancellableProcess):
    """A process that ignores terminate but exits when killed."""

    def terminate(self) -> None:
        self.terminate_called.set()


class NeverExitsProcess(KillRequiredProcess):
    """A pathological process used to verify that live artifacts are retained."""

    def kill(self) -> None:
        self.kill_called.set()


def make_video(path: Path, contents: bytes = b"source-video") -> Path:
    path.write_bytes(contents)
    return path


def assert_job_artifacts_removed(job: ConcatJob) -> None:
    assert job.manifest_path is not None
    assert job.staging_path is not None
    assert not job.manifest_path.exists()
    assert not job.manifest_path.parent.exists()
    assert not job.staging_path.exists()


def test_discover_videos_sorts_mp4s_and_excludes_output(tmp_path: Path) -> None:
    alpha = make_video(tmp_path / "Alpha.MP4")
    middle = make_video(tmp_path / "middle.mp4")
    zulu = make_video(tmp_path / "zulu.mp4")
    output = make_video(tmp_path / "output.mp4", b"old-output")
    make_video(tmp_path / "ignored.mov")
    nested = tmp_path / "nested"
    nested.mkdir()
    make_video(nested / "nested.mp4")

    assert discover_videos(tmp_path, excluded_output=output) == [
        alpha.resolve(),
        middle.resolve(),
        zulu.resolve(),
    ]


def test_output_alias_is_not_allowed_to_hide_or_replace_a_source(
    tmp_path: Path,
) -> None:
    source = make_video(tmp_path / "source.mp4")
    output_alias = tmp_path / "output.mp4"
    output_alias.hardlink_to(source)

    discovered = discover_videos(tmp_path, excluded_output=output_alias)

    assert discovered == [source.resolve()]
    with pytest.raises(ValueError, match="output file cannot"):
        ConcatJob(discovered, output_alias)


def test_manifest_preserves_order_and_escapes_apostrophes(tmp_path: Path) -> None:
    first = make_video(tmp_path / "first.mp4")
    quoted = make_video(tmp_path / "second clip's.mp4")
    manifest = tmp_path / "inputs.ffconcat"

    write_concat_manifest([quoted, first], manifest)

    escaped_quoted = str(quoted.resolve()).replace("'", r"'\''")
    expected = (
        f"file '{escaped_quoted}'\n"
        f"file '{first.resolve()}'\n"
    ).encode("utf-8")
    assert manifest.read_bytes() == expected


def test_build_ffmpeg_command_contains_required_concat_flags(tmp_path: Path) -> None:
    manifest = tmp_path / "inputs.ffconcat"
    staging = tmp_path / ".output.concat-token.mp4"

    assert build_ffmpeg_command(manifest, staging, ffmpeg="custom-ffmpeg") == [
        "custom-ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-progress",
        "pipe:1",
        "-nostats",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(manifest),
        "-c",
        "copy",
        str(staging),
    ]


def test_success_promotes_staging_output_and_cleans_artifacts(
    tmp_path: Path,
) -> None:
    source = make_video(tmp_path / "source.mp4")
    output = tmp_path / "joined.mp4"
    commands: list[list[str]] = []

    def successful_ffmpeg(command: list[str], **_kwargs: object) -> CompletedProcess:
        commands.append(command)
        Path(command[-1]).write_bytes(b"joined-video")
        return CompletedProcess(0, "ffmpeg completed")

    job = ConcatJob([source], output, popen_factory=successful_ffmpeg)

    assert job.run() == output.resolve()
    assert output.read_bytes() == b"joined-video"
    assert source.read_bytes() == b"source-video"
    assert len(commands) == 1
    assert commands[0][-1] != str(output.resolve())
    assert_job_artifacts_removed(job)


def test_success_replaces_output_symlink_entry_without_changing_its_target(
    tmp_path: Path,
) -> None:
    source = make_video(tmp_path / "source.mp4")
    old_target = make_video(tmp_path / "old-target.mp4", b"keep-target")
    output_link = tmp_path / "joined.mp4"
    try:
        output_link.symlink_to(old_target)
    except OSError as error:
        pytest.skip(f"Creating symlinks is unavailable: {error}")

    def successful_ffmpeg(command: list[str], **_kwargs: object) -> CompletedProcess:
        Path(command[-1]).write_bytes(b"joined-video")
        return CompletedProcess(0)

    job = ConcatJob([source], output_link, popen_factory=successful_ffmpeg)

    assert job.output_path == output_link.absolute()
    assert job.run() == output_link.absolute()
    assert not output_link.is_symlink()
    assert output_link.read_bytes() == b"joined-video"
    assert old_target.read_bytes() == b"keep-target"
    assert source.read_bytes() == b"source-video"


def test_nonzero_exit_cleans_partial_files_and_preserves_existing_output(
    tmp_path: Path,
) -> None:
    source = make_video(tmp_path / "source.mp4")
    output = make_video(tmp_path / "joined.mp4", b"keep-existing-output")

    def failing_ffmpeg(command: list[str], **_kwargs: object) -> CompletedProcess:
        Path(command[-1]).write_bytes(b"incomplete-output")
        return CompletedProcess(23, "first detail\nfinal failure detail")

    job = ConcatJob([source], output, popen_factory=failing_ffmpeg)

    with pytest.raises(ConcatError, match="exit code 23") as error:
        job.run()

    assert "final failure detail" in str(error.value)
    assert error.value.exit_code == 23
    assert output.read_bytes() == b"keep-existing-output"
    assert source.read_bytes() == b"source-video"
    assert_job_artifacts_removed(job)


def test_cancel_terminates_process_and_cleans_only_job_owned_files(
    tmp_path: Path,
) -> None:
    first = make_video(tmp_path / "first.mp4", b"first-source")
    second = make_video(tmp_path / "second.mp4", b"second-source")
    output = make_video(tmp_path / "joined.mp4", b"keep-existing-output")
    process_started = threading.Event()
    process = CancellableProcess()

    def long_running_ffmpeg(command: list[str], **_kwargs: object) -> CancellableProcess:
        Path(command[-1]).write_bytes(b"incomplete-output")
        process_started.set()
        return process

    job = ConcatJob([first, second], output, popen_factory=long_running_ffmpeg)
    result: dict[str, BaseException | Path] = {}

    def run_job() -> None:
        try:
            result["value"] = job.run()
        except BaseException as error:
            result["error"] = error

    worker = threading.Thread(target=run_job)
    worker.start()
    assert process_started.wait(timeout=2), "fake FFmpeg was not started"
    assert job.is_running
    assert job.manifest_path is not None and job.manifest_path.exists()
    assert job.staging_path is not None and job.staging_path.exists()

    assert job.cancel() is True
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert isinstance(result.get("error"), ConcatCancelled)
    assert process.terminate_called.is_set()
    assert not process.kill_called.is_set()
    assert output.read_bytes() == b"keep-existing-output"
    assert first.read_bytes() == b"first-source"
    assert second.read_bytes() == b"second-source"
    assert_job_artifacts_removed(job)


def test_cancel_escalates_to_kill_before_cleaning_artifacts(tmp_path: Path) -> None:
    source = make_video(tmp_path / "source.mp4")
    output = tmp_path / "joined.mp4"
    process_started = threading.Event()
    process = KillRequiredProcess()

    def stubborn_ffmpeg(command: list[str], **_kwargs: object) -> KillRequiredProcess:
        Path(command[-1]).write_bytes(b"incomplete-output")
        process_started.set()
        return process

    job = ConcatJob([source], output, popen_factory=stubborn_ffmpeg)
    job._TERMINATE_GRACE_SECONDS = 0.01
    result: dict[str, BaseException] = {}

    def run_job() -> None:
        try:
            job.run()
        except BaseException as error:
            result["error"] = error

    worker = threading.Thread(target=run_job)
    worker.start()
    assert process_started.wait(timeout=2)
    assert job.cancel()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert isinstance(result.get("error"), ConcatCancelled)
    assert process.terminate_called.is_set()
    assert process.kill_called.is_set()
    assert source.read_bytes() == b"source-video"
    assert not output.exists()
    assert_job_artifacts_removed(job)


def test_unstoppable_process_is_reported_and_live_artifacts_are_not_deleted(
    tmp_path: Path,
) -> None:
    source = make_video(tmp_path / "source.mp4")
    process_started = threading.Event()
    process = NeverExitsProcess()

    def unstoppable_ffmpeg(
        command: list[str], **_kwargs: object
    ) -> NeverExitsProcess:
        Path(command[-1]).write_bytes(b"live-incomplete-output")
        process_started.set()
        return process

    job = ConcatJob(
        [source], tmp_path / "joined.mp4", popen_factory=unstoppable_ffmpeg
    )
    job._TERMINATE_GRACE_SECONDS = 0.01
    result: dict[str, BaseException] = {}

    def run_job() -> None:
        try:
            job.run()
        except BaseException as error:
            result["error"] = error

    worker = threading.Thread(target=run_job)
    worker.start()
    assert process_started.wait(timeout=2)
    assert job.cancel()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert isinstance(result.get("error"), ConcatError)
    assert "retained" in str(result["error"])
    assert process.terminate_called.is_set()
    assert process.kill_called.is_set()
    assert job.manifest_path is not None and job.manifest_path.exists()
    assert job.staging_path is not None and job.staging_path.exists()
    assert source.read_bytes() == b"source-video"

    # The fake writer is now considered stopped, so clean its exact test artifacts.
    process.returncode = -9
    job._cleanup_artifacts()
    assert_job_artifacts_removed(job)
