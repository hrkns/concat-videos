from __future__ import annotations

import io
import queue
import subprocess
import threading
from pathlib import Path

import pytest

from concat_core import ConcatCancelled, ConcatError, ConcatJob, ConcatProgress


class ProbeProcess:
    def __init__(self, output: str, returncode: int = 0) -> None:
        self.returncode = returncode
        self._output = output

    def poll(self) -> int:
        return self.returncode

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        return self._output, ""


class StreamProcess:
    def __init__(
        self,
        progress_output: str,
        stderr_output: str = "",
        returncode: int = 0,
    ) -> None:
        self.stdout = io.StringIO(progress_output)
        self.stderr = io.StringIO(stderr_output)
        self.returncode = returncode

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


class BlockingProbeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.started = threading.Event()
        self.terminate_called = threading.Event()

    def poll(self) -> int | None:
        return self.returncode

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.started.set()
        if self.returncode is None:
            raise subprocess.TimeoutExpired("ffprobe", timeout)
        return "", "cancelled"

    def terminate(self) -> None:
        self.terminate_called.set()
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("ffprobe", timeout)
        return self.returncode


class BlockingLineStream:
    def __init__(self, initial_lines: list[str] | None = None) -> None:
        self._lines: queue.Queue[str] = queue.Queue()
        for line in initial_lines or []:
            self._lines.put(line)

    def readline(self) -> str:
        return self._lines.get()

    def finish(self) -> None:
        self._lines.put("")


class CancellableStreamProcess:
    def __init__(self) -> None:
        self.stdout = BlockingLineStream(
            [
                "out_time_us=2000000\n",
                "speed=1x\n",
                "progress=continue\n",
            ]
        )
        self.stderr = BlockingLineStream()
        self.returncode: int | None = None
        self.terminate_called = threading.Event()

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminate_called.set()
        self.returncode = -15
        self.stdout.finish()
        self.stderr.finish()

    def kill(self) -> None:
        self.returncode = -9
        self.stdout.finish()
        self.stderr.finish()

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return self.returncode


def make_video(path: Path) -> Path:
    path.write_bytes(b"source-video")
    return path.resolve()


def test_duplicate_inputs_are_probed_once_but_counted_each_time(
    tmp_path: Path,
) -> None:
    first = make_video(tmp_path / "first.mp4")
    second = make_video(tmp_path / "second.mp4")
    output = tmp_path / "joined.mp4"
    probe_calls: list[Path] = []
    updates: list[ConcatProgress] = []

    def probe_factory(command: list[str], **_kwargs: object) -> ProbeProcess:
        video = Path(command[-1])
        probe_calls.append(video)
        return ProbeProcess("4.0\n" if video == first else "6.0\n")

    def ffmpeg_factory(command: list[str], **_kwargs: object) -> StreamProcess:
        Path(command[-1]).write_bytes(b"joined")
        return StreamProcess(
            "out_time_us=5000000\n"
            "speed=2.0x\n"
            "progress=continue\n"
            "out_time_us=14000000\n"
            "speed=2.0x\n"
            "progress=end\n"
        )

    job = ConcatJob(
        [first, second, first],
        output,
        popen_factory=ffmpeg_factory,
        probe_popen_factory=probe_factory,
    )

    assert job.run(progress_callback=updates.append) == output.resolve()
    assert probe_calls == [first, second]
    assert updates[0] == ConcatProgress(0.0, 14.0, 0.0, None, None)
    assert updates[1].processed_seconds == 5.0
    assert updates[1].fraction == pytest.approx(5 / 14)
    assert updates[1].eta_seconds == pytest.approx(4.5)
    assert updates[-2].fraction == job._MAX_ACTIVE_FRACTION
    assert updates[-1].fraction == 1.0
    assert updates[-1].eta_seconds == 0.0


def test_unknown_duration_keeps_active_progress_indeterminate(
    tmp_path: Path,
) -> None:
    source = make_video(tmp_path / "source.mp4")
    output = tmp_path / "joined.mp4"
    updates: list[ConcatProgress] = []
    logs: list[str] = []

    def probe_factory(_command: list[str], **_kwargs: object) -> ProbeProcess:
        return ProbeProcess("N/A\n")

    def ffmpeg_factory(command: list[str], **_kwargs: object) -> StreamProcess:
        Path(command[-1]).write_bytes(b"joined")
        return StreamProcess(
            "out_time_ms=2500000\n"
            "speed=N/A\n"
            "progress=continue\n"
            "out_time_us=invalid\n"
            "out_time=00:00:03.500000\n"
            "speed=4x\n"
            "progress=end\n"
        )

    job = ConcatJob(
        [source],
        output,
        popen_factory=ffmpeg_factory,
        probe_popen_factory=probe_factory,
    )
    job.run(log_callback=logs.append, progress_callback=updates.append)

    active = updates[:-1]
    assert [update.processed_seconds for update in active] == [0.0, 2.5, 3.5]
    assert all(update.total_seconds is None for update in active)
    assert all(update.fraction is None for update in active)
    assert all(update.eta_seconds is None for update in active)
    assert updates[-1].fraction == 1.0
    assert any("without a percentage or ETA" in line for line in logs)


def test_one_hundred_percent_is_reported_only_after_atomic_commit(
    tmp_path: Path,
) -> None:
    source = make_video(tmp_path / "source.mp4")
    output = tmp_path / "joined.mp4"
    observations: list[tuple[float | None, bool]] = []

    def ffmpeg_factory(command: list[str], **_kwargs: object) -> StreamProcess:
        Path(command[-1]).write_bytes(b"joined")
        return StreamProcess(
            "out_time_us=9000000\n"
            "speed=1x\n"
            "progress=end\n"
        )

    job = ConcatJob(
        [source],
        output,
        popen_factory=ffmpeg_factory,
        probe_popen_factory=lambda *_args, **_kwargs: ProbeProcess("5\n"),
    )
    job.run(
        progress_callback=lambda progress: observations.append(
            (progress.fraction, output.exists())
        )
    )

    assert observations[-2] == (job._MAX_ACTIVE_FRACTION, False)
    assert observations[-1] == (1.0, True)
    assert all(fraction != 1.0 for fraction, _exists in observations[:-1])


def test_split_stderr_is_preserved_for_failure_detail(tmp_path: Path) -> None:
    source = make_video(tmp_path / "source.mp4")

    def ffmpeg_factory(_command: list[str], **_kwargs: object) -> StreamProcess:
        return StreamProcess(
            "out_time_us=1000000\nprogress=end\n",
            "first diagnostic\nfinal diagnostic\n",
            returncode=23,
        )

    job = ConcatJob(
        [source],
        tmp_path / "joined.mp4",
        popen_factory=ffmpeg_factory,
        probe_popen_factory=lambda *_args, **_kwargs: ProbeProcess("2\n"),
    )

    with pytest.raises(ConcatError, match="final diagnostic") as error:
        job.run(progress_callback=lambda _progress: None)
    assert error.value.exit_code == 23


def test_cancel_during_duration_probe_never_starts_ffmpeg(tmp_path: Path) -> None:
    source = make_video(tmp_path / "source.mp4")
    probe = BlockingProbeProcess()
    ffmpeg_started = threading.Event()

    def ffmpeg_factory(_command: list[str], **_kwargs: object) -> StreamProcess:
        ffmpeg_started.set()
        return StreamProcess("")

    job = ConcatJob(
        [source],
        tmp_path / "joined.mp4",
        popen_factory=ffmpeg_factory,
        probe_popen_factory=lambda *_args, **_kwargs: probe,
    )
    result: dict[str, BaseException] = {}

    def run_job() -> None:
        try:
            job.run(progress_callback=lambda _progress: None)
        except BaseException as error:
            result["error"] = error

    worker = threading.Thread(target=run_job)
    worker.start()
    assert probe.started.wait(timeout=2)
    assert job.cancel()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert isinstance(result.get("error"), ConcatCancelled)
    assert probe.terminate_called.is_set()
    assert not ffmpeg_started.is_set()
    assert job.manifest_path is None
    assert job.staging_path is None


def test_cancel_after_streamed_progress_stops_readers_without_completing(
    tmp_path: Path,
) -> None:
    source = make_video(tmp_path / "source.mp4")
    output = tmp_path / "joined.mp4"
    process = CancellableStreamProcess()
    progress_seen = threading.Event()
    updates: list[ConcatProgress] = []
    result: dict[str, BaseException] = {}

    def ffmpeg_factory(command: list[str], **_kwargs: object) -> CancellableStreamProcess:
        Path(command[-1]).write_bytes(b"incomplete")
        return process

    def record_progress(progress: ConcatProgress) -> None:
        updates.append(progress)
        if progress.processed_seconds > 0:
            progress_seen.set()

    job = ConcatJob(
        [source],
        output,
        popen_factory=ffmpeg_factory,
        probe_popen_factory=lambda *_args, **_kwargs: ProbeProcess("10\n"),
    )

    def run_job() -> None:
        try:
            job.run(progress_callback=record_progress)
        except BaseException as error:
            result["error"] = error

    worker = threading.Thread(target=run_job)
    worker.start()
    assert progress_seen.wait(timeout=2)
    assert job.cancel()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert isinstance(result.get("error"), ConcatCancelled)
    assert process.terminate_called.is_set()
    assert all(update.fraction != 1.0 for update in updates)
    assert not output.exists()
    assert source.read_bytes() == b"source-video"
    assert job.manifest_path is not None and not job.manifest_path.exists()
    assert job.staging_path is not None and not job.staging_path.exists()
