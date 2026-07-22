from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QAbstractItemView, QApplication

import concat_gui
from concat_core import ConcatProgress
from concat_gui import (
    LAST_INPUT_FOLDER_KEY,
    LAST_OUTPUT_FOLDER_KEY,
    ConcatWindow,
)


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def window(qapp: QApplication, tmp_path: Path) -> ConcatWindow:
    settings = QSettings(
        str(tmp_path / "settings.ini"), QSettings.Format.IniFormat
    )
    widget = ConcatWindow(settings=settings)
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
    cancelling_format = window.progress.format()

    window._update_progress(
        ConcatProgress(
            processed_seconds=9,
            total_seconds=10,
            fraction=0.9,
            eta_seconds=1,
            speed=1,
        )
    )
    assert window.status_label.text() == "Cancelling…"
    assert window.progress.format() == cancelling_format

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
    assert Path(str(window._settings.value(LAST_INPUT_FOLDER_KEY))).resolve() == folder.resolve()


def test_worker_uses_visible_order_and_restores_controls_after_success(
    window: ConcatWindow,
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    videos = [make_video(tmp_path / f"{name}.mp4") for name in ("second", "first")]
    output = tmp_path / "joined.mp4"
    captured: dict[str, object] = {}
    completion_alerts: list[Path] = []

    class SuccessfulJob:
        def __init__(self, video_paths: list[Path], output_path: Path) -> None:
            captured["videos"] = list(video_paths)
            captured["output"] = output_path

        def run(
            self,
            log_callback: object = None,
            progress_callback: object = None,
        ) -> Path:
            if callable(log_callback):
                log_callback("fake FFmpeg completed")
            if callable(progress_callback):
                progress_callback(
                    ConcatProgress(
                        processed_seconds=5,
                        total_seconds=10,
                        fraction=0.5,
                        eta_seconds=5,
                        speed=1,
                    )
                )
            return output

        def cancel(self) -> bool:
            return True

    monkeypatch.setattr(concat_gui, "ConcatJob", SuccessfulJob)
    monkeypatch.setattr(window, "_show_completion_alert", completion_alerts.append)
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
    assert completion_alerts == [output]
    assert Path(str(window._settings.value(LAST_OUTPUT_FOLDER_KEY))).resolve() == tmp_path.resolve()


def test_progress_displays_percentage_eta_and_unknown_fallback(
    window: ConcatWindow,
) -> None:
    window._set_running(True)
    window._update_progress(
        ConcatProgress(
            processed_seconds=25,
            total_seconds=100,
            fraction=0.25,
            eta_seconds=3661.1,
            speed=2,
        )
    )

    assert window.progress.minimum() == 0
    assert window.progress.maximum() == 100
    assert window.progress.value() == 25
    assert window.progress.format() == "%p% · 1:01:02 remaining"
    assert window.status_label.text() == "Concatenating videos…"

    # A delayed older update cannot move the displayed percentage backwards.
    window._update_progress(
        ConcatProgress(
            processed_seconds=10,
            total_seconds=100,
            fraction=0.1,
            eta_seconds=None,
            speed=None,
        )
    )
    assert window.progress.value() == 25

    window._set_running(False)
    window._set_running(True)
    window._update_progress(
        ConcatProgress(
            processed_seconds=2,
            total_seconds=None,
            fraction=None,
            eta_seconds=None,
            speed=None,
        )
    )
    assert window.progress.minimum() == 0
    assert window.progress.maximum() == 0
    assert window.progress.format() == "Time remaining unavailable"
    assert "time remaining unavailable" in window.status_label.text().lower()


def test_elapsed_time_updates_from_start_and_remains_after_finish(
    window: ConcatWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(concat_gui, "monotonic", lambda: clock[0])

    window._set_running(True)
    assert window.elapsed_label.isVisibleTo(window)
    assert window.elapsed_label.text() == "Elapsed 0:00"
    assert window._elapsed_timer.isActive()

    clock[0] = 165.9
    window._refresh_elapsed_time()
    assert window.elapsed_label.text() == "Elapsed 1:05"

    clock[0] = 3762.4
    window._refresh_elapsed_time()
    assert window.elapsed_label.text() == "Elapsed 1:01:02"

    window._set_running(False)
    assert not window._elapsed_timer.isActive()
    assert window.elapsed_label.isVisibleTo(window)
    assert window.elapsed_label.text() == "Elapsed 1:01:02"


@pytest.mark.parametrize(
    ("clicked_label", "expected_action"),
    [
        ("Close", None),
        ("Open Folder", "folder"),
        ("Play Video", "play"),
    ],
)
def test_completion_alert_sounds_and_offers_all_actions(
    window: ConcatWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clicked_label: str,
    expected_action: str | None,
) -> None:
    output = make_video(tmp_path / "joined.mp4")
    sounded: list[bool] = []
    actions: list[str] = []

    class FakeMessageBox:
        class Icon:
            Information = "information"

        class ButtonRole:
            AcceptRole = "accept"
            ActionRole = "action"

        created: "FakeMessageBox | None" = None

        def __init__(self, _parent: object) -> None:
            type(self).created = self
            self.buttons: dict[str, object] = {}
            self.clicked: object | None = None
            self.default_button: object | None = None

        def setIcon(self, _icon: object) -> None:
            pass

        def setWindowTitle(self, _title: str) -> None:
            pass

        def setText(self, _text: str) -> None:
            pass

        def setInformativeText(self, _text: str) -> None:
            pass

        def addButton(self, label: str, _role: object) -> object:
            button = object()
            self.buttons[label] = button
            return button

        def setDefaultButton(self, button: object) -> None:
            self.default_button = button

        def exec(self) -> None:
            self.clicked = self.buttons[clicked_label]

        def clickedButton(self) -> object | None:
            return self.clicked

    monkeypatch.setattr(concat_gui, "QMessageBox", FakeMessageBox)
    monkeypatch.setattr(
        window, "_play_completion_sound", lambda: sounded.append(True)
    )
    monkeypatch.setattr(
        window, "_open_output_folder", lambda _path: actions.append("folder")
    )
    monkeypatch.setattr(
        window, "_play_output_video", lambda _path: actions.append("play")
    )

    window._show_completion_alert(output)

    dialog = FakeMessageBox.created
    assert dialog is not None
    assert list(dialog.buttons) == ["Close", "Open Folder", "Play Video"]
    assert dialog.default_button is dialog.buttons["Close"]
    assert sounded == [True]
    assert actions == ([] if expected_action is None else [expected_action])


def test_completion_actions_use_system_folder_and_video_handlers(
    window: ConcatWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = make_video(tmp_path / "joined.mp4")
    opened_paths: list[Path] = []

    class FakeDesktopServices:
        @staticmethod
        def openUrl(url: object) -> bool:
            opened_paths.append(Path(url.toLocalFile()).resolve())  # type: ignore[attr-defined]
            return True

    monkeypatch.setattr(concat_gui, "QDesktopServices", FakeDesktopServices)

    window._open_output_folder(output)
    window._play_output_video(output)

    assert opened_paths == [tmp_path.resolve(), output.resolve()]


def test_last_input_and_output_folders_persist_across_windows(
    window: ConcatWindow,
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    input_folder = tmp_path / "remembered-input"
    output_folder = tmp_path / "remembered-output"
    input_folder.mkdir()
    output_folder.mkdir()

    window._remember_input_folder(input_folder)
    window._remember_output_folder(output_folder)

    reopened = ConcatWindow(settings=window._settings)
    assert Path(reopened._input_dialog_directory()).resolve() == input_folder.resolve()
    assert Path(reopened._output_dialog_directory()).resolve() == output_folder.resolve()

    reopened._set_default_output(input_folder)
    assert Path(reopened.output_edit.text()).resolve() == (
        output_folder / "output.mp4"
    ).resolve()
    reopened.close()
    qapp.processEvents()


def test_missing_remembered_folders_fall_back_to_existing_defaults(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    settings = QSettings(
        str(tmp_path / "missing-settings.ini"), QSettings.Format.IniFormat
    )
    settings.setValue(LAST_INPUT_FOLDER_KEY, str(tmp_path / "missing-input"))
    settings.setValue(LAST_OUTPUT_FOLDER_KEY, str(tmp_path / "missing-output"))
    settings.sync()

    fallback_window = ConcatWindow(settings=settings)
    source_folder = tmp_path / "source"
    source_folder.mkdir()

    assert Path(fallback_window._input_dialog_directory()).resolve() == Path.home().resolve()
    assert Path(fallback_window._output_dialog_directory()).resolve() == Path.home().resolve()
    fallback_window._set_default_output(source_folder)
    assert Path(fallback_window.output_edit.text()).resolve() == (
        source_folder / "output.mp4"
    ).resolve()
    fallback_window.close()
    qapp.processEvents()
