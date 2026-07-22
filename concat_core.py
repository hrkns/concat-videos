#!/usr/bin/env python3

"""Reusable FFmpeg concat logic shared by the CLI and desktop GUI."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path


class ConcatError(RuntimeError):
    """Raised when a concat job cannot be completed."""

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class ConcatCancelled(ConcatError):
    """Raised when a concat job is cancelled before its output is committed."""


LogCallback = Callable[[str], None]


def absolute_path(path: Path | str) -> Path:
    """Make a path absolute without following its final symlink."""

    return Path(os.path.abspath(os.fspath(Path(path).expanduser())))


def resolve_output_path(source_folder: Path | str, output_arg: str | None) -> Path:
    """Resolve CLI-style output arguments while preserving the existing behavior."""

    folder = Path(source_folder).expanduser().resolve()
    if not output_arg:
        return folder / "output.mp4"

    output = Path(output_arg).expanduser()
    if output.parent == Path("."):
        return absolute_path(folder / output.name)
    return absolute_path(output)


def paths_refer_to_same_file(first: Path | str, second: Path | str) -> bool:
    """Compare paths safely, including existing symlink/hard-link aliases."""

    first_path = Path(first).expanduser()
    second_path = Path(second).expanduser()

    try:
        if first_path.exists() and second_path.exists():
            return first_path.samefile(second_path)
    except OSError:
        pass

    first_normalized = os.path.normcase(os.fspath(absolute_path(first_path)))
    second_normalized = os.path.normcase(os.fspath(absolute_path(second_path)))
    return first_normalized == second_normalized


def paths_have_same_location(first: Path | str, second: Path | str) -> bool:
    """Compare lexical absolute locations without following aliases."""

    return os.path.normcase(os.fspath(absolute_path(first))) == os.path.normcase(
        os.fspath(absolute_path(second))
    )


def discover_videos(
    folder: Path | str,
    excluded_output: Path | str | None = None,
) -> list[Path]:
    """Return immediate MP4 children in deterministic alphabetical order."""

    source_folder = Path(folder).expanduser().resolve()
    if not source_folder.is_dir():
        raise ValueError(f"'{source_folder}' is not a valid folder.")

    videos = [
        path.resolve()
        for path in source_folder.iterdir()
        if path.is_file()
        and path.suffix.casefold() == ".mp4"
        and (
            excluded_output is None
            or not paths_have_same_location(path, excluded_output)
        )
    ]
    return sorted(videos, key=lambda path: (path.name.casefold(), path.name))


def _validated_video_paths(video_paths: Iterable[Path | str]) -> tuple[Path, ...]:
    videos: list[Path] = []
    for candidate in video_paths:
        video = Path(candidate).expanduser().resolve()
        if not video.is_file():
            raise ValueError(f"Input video does not exist or is not a file: '{video}'.")
        if video.suffix.casefold() != ".mp4":
            raise ValueError(f"Only MP4 input files are supported: '{video}'.")
        if "\r" in os.fspath(video) or "\n" in os.fspath(video):
            raise ValueError(
                f"Input paths cannot contain line breaks because FFmpeg manifests are line-based: '{video}'."
            )
        videos.append(video)

    if not videos:
        raise ValueError("Add at least one MP4 video before concatenating.")
    return tuple(videos)


def escape_concat_path(path: Path | str) -> str:
    """Quote a path for an FFmpeg concat-demuxer manifest."""

    return str(Path(path).resolve()).replace("'", r"'\''")


def write_concat_manifest(
    video_paths: Sequence[Path | str], destination: Path | str
) -> Path:
    """Write an ffconcat input list in the supplied order."""

    manifest = Path(destination)
    with manifest.open("w", encoding="utf-8", newline="\n") as handle:
        for video in video_paths:
            handle.write(f"file '{escape_concat_path(video)}'\n")
    return manifest


def build_ffmpeg_command(
    manifest: Path | str,
    staging_output: Path | str,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """Build the stream-copy command used by all Python entry points."""

    return [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(manifest),
        "-c",
        "copy",
        str(staging_output),
    ]


class ConcatJob:
    """A single cancellable concat operation with owned-artifact cleanup.

    FFmpeg always writes to a unique staging video beside the destination. The
    staging file is atomically promoted only after FFmpeg succeeds and no cancel
    request is pending, so cancellation cannot damage a pre-existing output.
    """

    _POLL_SECONDS = 0.1
    _TERMINATE_GRACE_SECONDS = 3.0

    def __init__(
        self,
        video_paths: Iterable[Path | str],
        output_path: Path | str,
        ffmpeg: str = "ffmpeg",
        *,
        popen_factory: Callable[..., subprocess.Popen[str]] | None = None,
    ) -> None:
        self.video_paths = _validated_video_paths(video_paths)
        # Keep the visible destination entry. Resolving the final symlink could
        # otherwise redirect os.replace() to a source video or unrelated file.
        self.output_path = absolute_path(output_path)
        if any(
            paths_refer_to_same_file(video, self.output_path)
            for video in self.video_paths
        ):
            raise ValueError("The output file cannot also be one of the input videos.")

        self.ffmpeg = ffmpeg
        self._popen_factory = popen_factory or subprocess.Popen
        self._cancel_requested = threading.Event()
        self._state_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._started = False
        self._finished = False
        self._committed = False
        self._temp_dir: Path | None = None
        self._manifest_path: Path | None = None
        self._staging_path: Path | None = None

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._started and not self._finished

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    @property
    def staging_path(self) -> Path | None:
        return self._staging_path

    @property
    def manifest_path(self) -> Path | None:
        return self._manifest_path

    def cancel(self) -> bool:
        """Request cancellation and terminate FFmpeg if it is already running."""

        with self._state_lock:
            if self._finished or self._committed:
                return False
            self._cancel_requested.set()
            process = self._process

        if process is not None and process.poll() is None:
            self._terminate_process(process)
        return True

    def run(self, log_callback: LogCallback | None = None) -> Path:
        """Run FFmpeg synchronously; call this method from a GUI worker thread."""

        log = log_callback or (lambda _message: None)
        process: subprocess.Popen[str] | None = None
        cleanup_is_safe = True

        with self._state_lock:
            if self._started:
                raise RuntimeError("This concat job has already been started.")
            self._started = True

        try:
            if self._cancel_requested.is_set():
                raise ConcatCancelled("Concatenation cancelled.")

            self._prepare_artifacts()
            assert self._manifest_path is not None
            assert self._staging_path is not None
            write_concat_manifest(self.video_paths, self._manifest_path)

            command = build_ffmpeg_command(
                self._manifest_path, self._staging_path, self.ffmpeg
            )
            log("Running FFmpeg:\n" + subprocess.list2cmdline(command))

            try:
                process = self._popen_factory(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            except FileNotFoundError as error:
                raise ConcatError(
                    "FFmpeg was not found. Install it and make sure 'ffmpeg' is in PATH."
                ) from error
            except OSError as error:
                raise ConcatError(f"Could not start FFmpeg: {error}") from error

            with self._state_lock:
                self._process = process
                cancellation_won_race = self._cancel_requested.is_set()

            if cancellation_won_race and process.poll() is None:
                self._terminate_process(process)

            output = self._communicate_until_exit(process)
            if output.strip():
                log(output.rstrip())

            if self._cancel_requested.is_set():
                raise ConcatCancelled("Concatenation cancelled.")
            if process.returncode != 0:
                detail = self._failure_detail(output)
                raise ConcatError(
                    f"FFmpeg failed with exit code {process.returncode}.{detail}",
                    exit_code=process.returncode,
                )
            if not self._staging_path.is_file():
                raise ConcatError("FFmpeg finished without creating an output file.")

            # This lock makes the final cancel-vs-commit decision atomic.
            with self._state_lock:
                if self._cancel_requested.is_set():
                    raise ConcatCancelled("Concatenation cancelled.")
                try:
                    os.replace(self._staging_path, self.output_path)
                except OSError as error:
                    raise ConcatError(
                        f"Could not finalize output '{self.output_path}': {error}"
                    ) from error
                self._committed = True
                self._finished = True

            log(f"Output created: {self.output_path}")
            return self.output_path
        except BaseException as error:
            if process is not None and process.poll() is None:
                self._cancel_requested.set()
                if not self._stop_process(process):
                    cleanup_is_safe = False
                    raise ConcatError(
                        "FFmpeg could not be stopped. Temporary job files were retained "
                        "because deleting them while FFmpeg is running could corrupt data."
                    ) from error
            raise
        finally:
            with self._state_lock:
                self._process = None
            cleanup_error: ConcatError | None = None
            if cleanup_is_safe:
                try:
                    self._cleanup_artifacts()
                except ConcatError as error:
                    cleanup_error = error
            with self._state_lock:
                self._finished = True
            if cleanup_error is not None:
                raise cleanup_error

    def _prepare_artifacts(self) -> None:
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self._temp_dir = Path(tempfile.mkdtemp(prefix="concat-videos-"))
            self._manifest_path = self._temp_dir / "inputs.ffconcat"
            suffix = self.output_path.suffix or ".mp4"
            unique = uuid.uuid4().hex
            self._staging_path = self.output_path.parent / (
                f".{self.output_path.stem}.concat-{unique}{suffix}"
            )
        except OSError as error:
            raise ConcatError(f"Could not prepare temporary files: {error}") from error

    def _communicate_until_exit(self, process: subprocess.Popen[str]) -> str:
        termination_deadline: float | None = None
        kill_deadline: float | None = None
        while True:
            try:
                output, _ = process.communicate(timeout=self._POLL_SECONDS)
                return output or ""
            except subprocess.TimeoutExpired:
                if not self._cancel_requested.is_set():
                    continue
                if termination_deadline is None:
                    self._terminate_process(process)
                    termination_deadline = time.monotonic() + self._TERMINATE_GRACE_SECONDS
                elif kill_deadline is None and time.monotonic() >= termination_deadline:
                    self._kill_process(process)
                    kill_deadline = time.monotonic() + self._TERMINATE_GRACE_SECONDS
                elif kill_deadline is not None and time.monotonic() >= kill_deadline:
                    raise ConcatError("FFmpeg did not exit after it was killed.")

    def _stop_process(self, process: subprocess.Popen[str]) -> bool:
        self._terminate_process(process)
        try:
            process.wait(timeout=self._TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            self._kill_process(process)
            try:
                process.wait(timeout=self._TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                return process.poll() is not None
        return process.poll() is not None

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        try:
            process.terminate()
        except (OSError, ProcessLookupError):
            pass

    @staticmethod
    def _kill_process(process: subprocess.Popen[str]) -> None:
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass

    def _cleanup_artifacts(self) -> None:
        failures: list[str] = []

        # Retry exact paths because Windows may release FFmpeg handles late.
        if self._staging_path is not None:
            last_error: OSError | None = None
            for _attempt in range(20):
                try:
                    self._staging_path.unlink(missing_ok=True)
                    last_error = None
                    break
                except OSError as error:
                    last_error = error
                    time.sleep(0.1)
            if last_error is not None and self._staging_path.exists():
                failures.append(f"'{self._staging_path}': {last_error}")

        if self._temp_dir is not None:
            last_error = None
            for _attempt in range(20):
                try:
                    shutil.rmtree(self._temp_dir)
                    last_error = None
                    break
                except FileNotFoundError:
                    last_error = None
                    break
                except OSError as error:
                    last_error = error
                    time.sleep(0.1)
            if last_error is not None and self._temp_dir.exists():
                failures.append(f"'{self._temp_dir}': {last_error}")

        if failures:
            raise ConcatError(
                "Could not remove generated temporary files:\n" + "\n".join(failures)
            )

    @staticmethod
    def _failure_detail(output: str) -> str:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            return ""
        return "\n\n" + "\n".join(lines[-12:])
