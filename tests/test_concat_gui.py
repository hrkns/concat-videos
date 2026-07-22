from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QAbstractItemView, QApplication

import concat_gui
from concat_gui import ConcatWindow


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def window(qapp: QApplication) -> ConcatWindow:
    widget = ConcatWindow()
    yield widget
    if widget._running:
        widget._set_running(False)
    widget.close()
    qapp.processEvents()


def make_video(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"video")
    return path.resolve()


def test_repeated_appends_preserve_order_and_duplicates(
    window: ConcatWindow, tmp_path: Path
) -> None:
    first = make_video(tmp_path / "first.mp4")
    second = make_video(tmp_path / "second.mp4")

    window._append_video_paths([first, second])
    window._append_video_paths([first])

    assert window._video_paths() == [first, second, first]
    assert window.count_label.text() == "3 videos"


def test_move_and_multi_remove_update_the_order(
    window: ConcatWindow, tmp_path: Path
) -> None:
    videos = [make_video(tmp_path / f"{name}.mp4") for name in "abcd"]
    window._append_video_paths(videos)

    window.video_list.item(1).setSelected(True)
    window.video_list.item(2).setSelected(True)
    window._move_selected(-1)

    assert window._video_paths() == [videos[1], videos[2], videos[0], videos[3]]

    window.video_list.clearSelection()
    window.video_list.item(1).setSelected(True)
    window.video_list.item(3).setSelected(True)
    window._remove_selected()

    assert window._video_paths() == [videos[1], videos[0]]
    assert window.count_label.text() == "2 videos"


def test_running_and_cancelling_lock_every_mutating_action(
    window: ConcatWindow, tmp_path: Path
) -> None:
    source = make_video(tmp_path / "source.mp4")
    window._append_video_paths([source])
    window.output_edit.setText(str(tmp_path / "joined.mp4"))
    window.video_list.item(0).setSelected(True)

    class FakeJob:
        cancel_called = False

        def cancel(self) -> bool:
            self.cancel_called = True
            return True

    fake_job = FakeJob()
    window._job = fake_job  # type: ignore[assignment]
    window._set_running(True)

    mutating_controls = [
        window.add_folder_button,
        window.add_videos_button,
        window.move_up_button,
        window.move_down_button,
        window.remove_button,
        window.clear_button,
        window.output_edit,
        window.output_browse_button,
        window.start_button,
    ]
    assert all(not control.isEnabled() for control in mutating_controls)
    assert window.video_list.dragDropMode() == QAbstractItemView.DragDropMode.NoDragDrop
    assert not window.video_list.dragEnabled()
    assert not window.video_list.acceptDrops()
    assert window.cancel_button.isEnabled()

    window._cancel_concat()

    assert fake_job.cancel_called
    assert not window.cancel_button.isEnabled()
    assert window.status_label.text() == "Cancelling…"

    window._job = None
    window._set_running(False)
    assert window.video_list.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
    assert window.video_list.dragEnabled()
    assert window.video_list.acceptDrops()
    assert window.start_button.isEnabled()
    assert not window.cancel_button.isEnabled()


def test_dropped_folders_are_sorted_and_repeated_drops_append(
    window: ConcatWindow, tmp_path: Path
) -> None:
    folder = tmp_path / "folder"
    alpha = make_video(folder / "Alpha.MP4")
    zulu = make_video(folder / "zulu.mp4")
    make_video(folder / "output.mp4")
    (folder / "notes.txt").write_text("not a video", encoding="utf-8")
    standalone = make_video(tmp_path / "elsewhere" / "middle.mp4")

    window._add_dropped_paths([str(folder)])
    window._add_dropped_paths([str(standalone), str(folder), str(folder / "notes.txt")])

    assert window._video_paths() == [alpha, zulu, standalone, alpha, zulu]
    assert Path(window.output_edit.text()).resolve() == (folder / "output.mp4").resolve()
    assert window.count_label.text() == "5 videos"
    assert "ignored 1 unsupported" in window.status_label.text().lower()


def test_worker_uses_visible_order_and_restores_controls_after_success(
    window: ConcatWindow,
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    videos = [make_video(tmp_path / f"{name}.mp4") for name in ("second", "first")]
    output = tmp_path / "joined.mp4"
    captured: dict[str, object] = {}

    class SuccessfulJob:
        def __init__(self, video_paths: list[Path], output_path: Path) -> None:
            captured["videos"] = list(video_paths)
            captured["output"] = output_path

        def run(self, log_callback: object = None) -> Path:
            if callable(log_callback):
                log_callback("fake FFmpeg completed")
            return output

        def cancel(self) -> bool:
            return True

    monkeypatch.setattr(concat_gui, "ConcatJob", SuccessfulJob)
    window._append_video_paths(videos)
    window.output_edit.setText(str(output))

    window._start_concat()
    deadline = time.monotonic() + 2
    while window._running and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)

    assert not window._running
    assert captured["videos"] == videos
    assert captured["output"] == output.absolute()
    assert window.start_button.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert "Completed:" in window.status_label.text()
    assert "fake FFmpeg completed" in window.log_view.toPlainText()
