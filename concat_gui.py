#!/usr/bin/env python3

"""Desktop interface for arranging and concatenating MP4 videos."""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from PySide6.QtCore import QDir, QObject, QThread, QTimer, Qt, Signal, Slot
    from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDragMoveEvent, QDropEvent, QPainter, QPalette
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QFileDialog,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QSplitter,
        QVBoxLayout,
        QWidget,
    )
except ModuleNotFoundError as exc:
    if exc.name and (exc.name == "PySide6" or exc.name.startswith("PySide6.")):
        raise SystemExit(
            "PySide6 is required for the graphical interface. "
            "Install it with: python -m pip install -r requirements.txt"
        ) from None
    raise

from concat_core import ConcatCancelled, ConcatError, ConcatJob, discover_videos


VIDEO_SUFFIX = ".mp4"
PATH_ROLE = Qt.ItemDataRole.UserRole


def _native_path(path: Path) -> str:
    return QDir.toNativeSeparators(str(path))


def _canonical_path(path: Path) -> str:
    """Return a comparison key without requiring the path to exist."""

    return os.path.normcase(os.path.abspath(os.fspath(path)))


class VideoListWidget(QListWidget):
    """A reorderable list that also accepts files and folders from the OS."""

    paths_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._editing_enabled = True
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setUniformItemSizes(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.set_editing_enabled(True)

    def set_editing_enabled(self, enabled: bool) -> None:
        self._editing_enabled = enabled
        self.setDragEnabled(enabled)
        self.setAcceptDrops(enabled)
        mode = (
            QAbstractItemView.DragDropMode.InternalMove
            if enabled
            else QAbstractItemView.DragDropMode.NoDragDrop
        )
        self.setDragDropMode(mode)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._editing_enabled and event.mimeData().hasUrls():
            if any(url.isLocalFile() for url in event.mimeData().urls()):
                event.acceptProposedAction()
                return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._editing_enabled and event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if self._editing_enabled and event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if paths:
                event.setDropAction(Qt.DropAction.CopyAction)
                event.accept()
                self.paths_dropped.emit(paths)
                return
        super().dropEvent(event)

    def paintEvent(self, event: object) -> None:
        super().paintEvent(event)
        if self.count():
            return

        painter = QPainter(self.viewport())
        painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
        painter.drawText(
            self.viewport().rect().adjusted(24, 24, -24, -24),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            "Drop MP4 files or folders here\nYou can add from more than one location",
        )

    def move_selection(self, offset: int) -> bool:
        if offset not in (-1, 1) or not self.selectedItems():
            return False

        moved_any = False
        if offset < 0:
            rows = range(1, self.count())
        else:
            rows = range(self.count() - 2, -1, -1)

        for row in rows:
            item = self.item(row)
            adjacent = self.item(row + offset)
            if item.isSelected() and not adjacent.isSelected():
                moved_item = self.takeItem(row)
                self.insertItem(row + offset, moved_item)
                moved_item.setSelected(True)
                moved_any = True

        if moved_any:
            selected = self.selectedItems()
            if selected:
                self.scrollToItem(selected[0])
        return moved_any


class ConcatWorker(QObject):
    """Run a blocking ConcatJob away from the GUI thread."""

    log_message = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)
    finished = Signal()

    def __init__(self, job: ConcatJob) -> None:
        super().__init__()
        self._job = job

    def _relay_log(self, message: object) -> None:
        self.log_message.emit(str(message))

    @Slot()
    def run(self) -> None:
        try:
            output_path = self._job.run(log_callback=self._relay_log)
        except ConcatCancelled as exc:
            self.cancelled.emit(str(exc) or "Concatenation cancelled.")
        except ConcatError as exc:
            self.failed.emit(str(exc) or "Concatenation failed.")
        except Exception as exc:  # Keep an unexpected worker error from taking down Qt.
            self.failed.emit(f"Unexpected error: {exc}")
        else:
            self.succeeded.emit(output_path)
        finally:
            self.finished.emit()


class ConcatWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._cancel_requested = False
        self._close_after_cancel = False
        self._job: ConcatJob | None = None
        self._thread: QThread | None = None
        self._worker: ConcatWorker | None = None
        self._pending_outcome: tuple[str, str] | None = None

        self.setWindowTitle("Concatenate Videos")
        self.setMinimumSize(760, 600)
        self.resize(920, 720)
        self._build_ui()
        self._connect_signals()
        self._refresh_actions()

    def _build_ui(self) -> None:
        central = QWidget(self)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(18, 18, 18, 12)
        root_layout.setSpacing(12)

        title = QLabel("Concatenate videos")
        title_font = title.font()
        title_font.setPointSize(title_font.pointSize() + 6)
        title_font.setBold(True)
        title.setFont(title_font)
        subtitle = QLabel(
            "Add a folder or individual videos, arrange them in playback order, "
            "then choose where to save the result."
        )
        subtitle.setWordWrap(True)
        root_layout.addWidget(title)
        root_layout.addWidget(subtitle)

        inputs_group = QGroupBox("Input videos")
        inputs_layout = QVBoxLayout(inputs_group)

        add_row = QHBoxLayout()
        self.add_folder_button = QPushButton("Add Folder…")
        self.add_videos_button = QPushButton("Add Videos…")
        self.count_label = QLabel("0 videos")
        add_row.addWidget(self.add_folder_button)
        add_row.addWidget(self.add_videos_button)
        add_row.addStretch(1)
        add_row.addWidget(self.count_label)
        inputs_layout.addLayout(add_row)

        self.video_list = VideoListWidget()
        self.video_list.setMinimumHeight(210)
        self.video_list.setToolTip(
            "Drop MP4 files or folders here. Drag rows to reorder them. Duplicates are allowed."
        )
        inputs_layout.addWidget(self.video_list, 1)

        edit_row = QHBoxLayout()
        self.move_up_button = QPushButton("Move Up")
        self.move_down_button = QPushButton("Move Down")
        self.remove_button = QPushButton("Remove")
        self.clear_button = QPushButton("Clear")
        self.move_up_button.setShortcut("Alt+Up")
        self.move_down_button.setShortcut("Alt+Down")
        self.remove_button.setShortcut("Delete")
        edit_row.addWidget(self.move_up_button)
        edit_row.addWidget(self.move_down_button)
        edit_row.addStretch(1)
        edit_row.addWidget(self.remove_button)
        edit_row.addWidget(self.clear_button)
        inputs_layout.addLayout(edit_row)

        output_group = QGroupBox("Output")
        output_layout = QHBoxLayout(output_group)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Choose an output MP4 file")
        self.output_edit.setClearButtonEnabled(True)
        self.output_browse_button = QPushButton("Browse…")
        output_layout.addWidget(self.output_edit, 1)
        output_layout.addWidget(self.output_browse_button)

        log_group = QGroupBox("Activity")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setPlaceholderText("FFmpeg output and errors will appear here.")
        log_layout.addWidget(self.log_view)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(inputs_group)
        splitter.addWidget(log_group)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setCollapsible(0, False)
        root_layout.addWidget(splitter, 1)
        root_layout.addWidget(output_group)

        run_row = QHBoxLayout()
        run_row.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.start_button = QPushButton("Start Concatenation")
        self.start_button.setDefault(True)
        run_row.addWidget(self.cancel_button)
        run_row.addWidget(self.start_button)
        root_layout.addLayout(run_row)

        self.status_label = QLabel("Ready — add one or more videos.")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(140)
        self.progress.hide()
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress)

        self.setCentralWidget(central)

    def _connect_signals(self) -> None:
        self.add_folder_button.clicked.connect(self._choose_folder)
        self.add_videos_button.clicked.connect(self._choose_videos)
        self.output_browse_button.clicked.connect(self._choose_output)
        self.video_list.paths_dropped.connect(self._add_dropped_paths)
        self.video_list.itemSelectionChanged.connect(self._refresh_actions)
        self.video_list.model().rowsMoved.connect(self._refresh_actions)
        self.output_edit.textChanged.connect(self._refresh_actions)
        self.move_up_button.clicked.connect(lambda: self._move_selected(-1))
        self.move_down_button.clicked.connect(lambda: self._move_selected(1))
        self.remove_button.clicked.connect(self._remove_selected)
        self.clear_button.clicked.connect(self._clear_videos)
        self.start_button.clicked.connect(self._start_concat)
        self.cancel_button.clicked.connect(self._cancel_concat)

    def _output_path_or_none(self) -> Path | None:
        value = self.output_edit.text().strip()
        return Path(value).expanduser() if value else None

    def _first_video_path(self) -> Path | None:
        if not self.video_list.count():
            return None
        return Path(self.video_list.item(0).data(PATH_ROLE))

    def _dialog_directory(self) -> str:
        output = self._output_path_or_none()
        if output is not None:
            return str(output.parent)
        first_video = self._first_video_path()
        if first_video is not None:
            return str(first_video.parent)
        return str(Path.home())

    def _set_default_output(self, folder: Path) -> None:
        if not self.output_edit.text().strip():
            self.output_edit.setText(_native_path(folder / "output.mp4"))

    @Slot()
    def _choose_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Add a folder of MP4 videos", self._dialog_directory()
        )
        if not selected:
            return

        folder = Path(selected).resolve()
        excluded_output = self._output_path_or_none() or (folder / "output.mp4")
        try:
            videos = discover_videos(folder, excluded_output=excluded_output)
        except (ConcatError, OSError, ValueError) as exc:
            self._show_input_error(str(exc))
            return

        if not videos:
            self.status_label.setText(f"No MP4 videos found in {_native_path(folder)}.")
            return

        self._set_default_output(folder)
        self._append_video_paths(videos)
        self.status_label.setText(
            f"Added {len(videos)} video{'s' if len(videos) != 1 else ''} from {_native_path(folder)}."
        )

    @Slot()
    def _choose_videos(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "Add MP4 videos",
            self._dialog_directory(),
            "MP4 videos (*.mp4 *.MP4)",
        )
        if not selected:
            return

        videos = [
            Path(value).resolve()
            for value in selected
            if Path(value).is_file() and Path(value).suffix.lower() == VIDEO_SUFFIX
        ]
        if not videos:
            self.status_label.setText("No MP4 videos were selected.")
            return

        self._set_default_output(videos[0].parent)
        self._append_video_paths(videos)
        self.status_label.setText(
            f"Added {len(videos)} video{'s' if len(videos) != 1 else ''}."
        )

    @Slot()
    def _choose_output(self) -> None:
        current = self.output_edit.text().strip()
        if not current:
            current = str(Path(self._dialog_directory()) / "output.mp4")
        selected, _ = QFileDialog.getSaveFileName(
            self, "Choose output video", current, "MP4 video (*.mp4)"
        )
        if not selected:
            return
        output = Path(selected)
        if not output.suffix:
            output = output.with_suffix(VIDEO_SUFFIX)
        self.output_edit.setText(_native_path(output))

    @Slot(list)
    def _add_dropped_paths(self, dropped_paths: list[str]) -> None:
        videos: list[Path] = []
        ignored = 0
        errors: list[str] = []

        for raw_path in dropped_paths:
            path = Path(raw_path).expanduser()
            if path.is_dir():
                folder = path.resolve()
                excluded_output = self._output_path_or_none() or (folder / "output.mp4")
                try:
                    discovered = discover_videos(folder, excluded_output=excluded_output)
                except (ConcatError, OSError, ValueError) as exc:
                    errors.append(str(exc))
                    continue
                if discovered:
                    self._set_default_output(folder)
                    videos.extend(discovered)
                else:
                    ignored += 1
            elif path.is_file() and path.suffix.lower() == VIDEO_SUFFIX:
                resolved = path.resolve()
                self._set_default_output(resolved.parent)
                videos.append(resolved)
            else:
                ignored += 1

        if videos:
            self._append_video_paths(videos)
        for error in errors:
            self._append_log(f"Could not add dropped item: {error}")

        parts: list[str] = []
        if videos:
            parts.append(f"added {len(videos)} video{'s' if len(videos) != 1 else ''}")
        if ignored:
            parts.append(f"ignored {ignored} unsupported or empty item{'s' if ignored != 1 else ''}")
        if errors:
            parts.append(f"could not read {len(errors)} item{'s' if len(errors) != 1 else ''}")
        self.status_label.setText((", ".join(parts).capitalize() + ".") if parts else "Nothing was added.")

    def _append_video_paths(self, paths: list[Path]) -> None:
        for source_path in paths:
            resolved = Path(source_path).expanduser().resolve()
            item = QListWidgetItem(_native_path(resolved))
            item.setData(PATH_ROLE, str(resolved))
            item.setToolTip(_native_path(resolved))
            self.video_list.addItem(item)
        self._refresh_actions()

    def _show_input_error(self, message: str) -> None:
        self.status_label.setText("Could not add videos.")
        QMessageBox.warning(self, "Could not add videos", message)

    def _move_selected(self, offset: int) -> None:
        if self.video_list.move_selection(offset):
            self.status_label.setText("Updated video order.")
        self._refresh_actions()

    @Slot()
    def _remove_selected(self) -> None:
        rows = sorted(
            {index.row() for index in self.video_list.selectedIndexes()}, reverse=True
        )
        for row in rows:
            self.video_list.takeItem(row)
        if rows:
            self.status_label.setText(
                f"Removed {len(rows)} video{'s' if len(rows) != 1 else ''}."
            )
        self._refresh_actions()

    @Slot()
    def _clear_videos(self) -> None:
        count = self.video_list.count()
        self.video_list.clear()
        if count:
            self.status_label.setText("Cleared the input list.")
        self._refresh_actions()

    def _video_paths(self) -> list[Path]:
        return [
            Path(self.video_list.item(row).data(PATH_ROLE))
            for row in range(self.video_list.count())
        ]

    @Slot()
    def _start_concat(self) -> None:
        if self._running:
            return

        videos = self._video_paths()
        output = self._output_path_or_none()
        if not videos or output is None:
            self._refresh_actions()
            return
        output = Path(os.path.abspath(os.fspath(output)))

        if output.suffix.lower() != VIDEO_SUFFIX:
            QMessageBox.warning(
                self,
                "Invalid output",
                "The output file must use the .mp4 extension.",
            )
            return
        if any(_canonical_path(video) == _canonical_path(output) for video in videos):
            QMessageBox.warning(
                self,
                "Invalid output",
                "The output file is also in the input list. Choose a different output file.",
            )
            return
        if output.exists():
            answer = QMessageBox.question(
                self,
                "Replace existing output?",
                f"The file already exists:\n\n{_native_path(output)}\n\nReplace it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            job = ConcatJob(videos, output)
        except (ConcatError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot start concatenation", str(exc))
            return

        self.output_edit.setText(_native_path(output))
        self.log_view.clear()
        self._append_log(f"Starting concatenation of {len(videos)} video{'s' if len(videos) != 1 else ''}.")
        self._append_log(f"Output: {_native_path(output)}")

        self._job = job
        self._pending_outcome = None
        self._cancel_requested = False
        self._set_running(True)

        thread = QThread(self)
        worker = ConcatWorker(job)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log_message.connect(self._append_log)
        worker.succeeded.connect(self._job_succeeded)
        worker.failed.connect(self._job_failed)
        worker.cancelled.connect(self._job_cancelled)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._worker_thread_finished)
        thread.finished.connect(thread.deleteLater)

        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot()
    def _cancel_concat(self) -> None:
        if not self._running or self._cancel_requested:
            return
        self._cancel_requested = True
        self.status_label.setText("Cancelling…")
        self._append_log("Cancellation requested. Cleaning up generated files…")
        self._refresh_actions()

        if self._job is not None:
            try:
                accepted = self._job.cancel()
            except Exception as exc:
                self._append_log(f"Could not signal cancellation: {exc}")
            else:
                if not accepted:
                    self._append_log("The FFmpeg process has already stopped.")

    @Slot(str)
    def _append_log(self, message: str) -> None:
        text = str(message).rstrip()
        if text:
            self.log_view.appendPlainText(text)

    @Slot(object)
    def _job_succeeded(self, output_path: object) -> None:
        message = f"Completed: {_native_path(Path(output_path))}"
        self._pending_outcome = ("success", message)
        self._append_log(message)

    @Slot(str)
    def _job_failed(self, message: str) -> None:
        self._pending_outcome = ("failure", message)
        self._append_log(f"Error: {message}")

    @Slot(str)
    def _job_cancelled(self, message: str) -> None:
        message = message or "Concatenation cancelled."
        self._pending_outcome = ("cancelled", message)
        self._append_log(message)

    @Slot()
    def _worker_thread_finished(self) -> None:
        outcome, message = self._pending_outcome or (
            "failure",
            "The concatenation worker stopped without reporting a result.",
        )

        self._thread = None
        self._worker = None
        self._job = None
        self._set_running(False)

        if outcome == "success":
            self.status_label.setText(message)
        elif outcome == "cancelled":
            self.status_label.setText("Concatenation cancelled. Generated files were removed.")
        else:
            self.status_label.setText("Concatenation failed. See the activity log for details.")
            if not self._close_after_cancel:
                QMessageBox.critical(self, "Concatenation failed", message)

        if self._close_after_cancel:
            QTimer.singleShot(0, self.close)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.progress.setVisible(running)
        if running:
            self.progress.setRange(0, 0)
            self.status_label.setText("Concatenating videos…")
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self._cancel_requested = False
        self._refresh_actions()

    @Slot()
    def _refresh_actions(self) -> None:
        count = self.video_list.count()
        selected_rows = sorted({index.row() for index in self.video_list.selectedIndexes()})
        self.count_label.setText(f"{count} video{'s' if count != 1 else ''}")

        if self._running:
            self.add_folder_button.setEnabled(False)
            self.add_videos_button.setEnabled(False)
            self.move_up_button.setEnabled(False)
            self.move_down_button.setEnabled(False)
            self.remove_button.setEnabled(False)
            self.clear_button.setEnabled(False)
            self.output_edit.setEnabled(False)
            self.output_browse_button.setEnabled(False)
            self.video_list.set_editing_enabled(False)
            self.start_button.setEnabled(False)
            self.cancel_button.setEnabled(not self._cancel_requested)
            return

        self.add_folder_button.setEnabled(True)
        self.add_videos_button.setEnabled(True)
        self.move_up_button.setEnabled(bool(selected_rows) and min(selected_rows) > 0)
        self.move_down_button.setEnabled(
            bool(selected_rows) and max(selected_rows) < count - 1
        )
        self.remove_button.setEnabled(bool(selected_rows))
        self.clear_button.setEnabled(count > 0)
        self.output_edit.setEnabled(True)
        self.output_browse_button.setEnabled(True)
        self.video_list.set_editing_enabled(True)
        self.start_button.setEnabled(count > 0 and bool(self.output_edit.text().strip()))
        self.cancel_button.setEnabled(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._running:
            event.accept()
            return
        if self._close_after_cancel:
            event.ignore()
            return

        answer = QMessageBox.question(
            self,
            "Cancel concatenation?",
            "A concatenation is still running. Cancel it and close after cleanup?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._close_after_cancel = True
            self._cancel_concat()
        event.ignore()


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Concatenate Videos")
    app.setOrganizationName("concat-videos")
    window = ConcatWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
