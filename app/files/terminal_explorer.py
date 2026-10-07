from __future__ import annotations

import asyncio
import inspect
import shlex
import shutil
import tempfile
import time

import os
import posixpath
from typing import Callable

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QFileDialog, QApplication,
    QInputDialog, QMessageBox, QPushButton, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QProgressBar,
)

from app.files.browser import Entry, ServerFileBackend
from app.files.highlighter import CodeHighlighter
from app.files.file_types import classify, color_for

try:
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    HAS_MEDIA = True
except Exception:
    QUrl = QAudioOutput = QMediaPlayer = QVideoWidget = None
    HAS_MEDIA = False


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".mpeg", ".mpg", ".ts", ".3gp"}
AUDIO_EXTS = {
    ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".oga", ".opus",
    ".wma", ".aiff", ".aif", ".alac", ".amr", ".ac3", ".mka", ".mid", ".midi",
}


def _queue_icon(name: str, is_dir: bool = False) -> QIcon:
    app = QApplication.instance()
    theme = str(app.property("sshdockTheme") if app is not None else "dark").lower()
    if str(name).startswith("fa5s."):
        try:
            import qtawesome as qta
            return qta.icon(str(name), color="#64748b" if theme == "light" else "#aebccc")
        except Exception:
            return QIcon()
    file_type = classify(name, is_dir=is_dir)
    try:
        import qtawesome as qta
        return qta.icon(file_type.icon, color=color_for(file_type.key, theme))
    except Exception:
        return QIcon()


class _QueueRowWidget(QWidget):
    removed = Signal()

    def __init__(self, name: str, path: str, icon: QIcon, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(8)

        icon_label = QLabel()
        icon_label.setFixedSize(20, 20)
        try:
            icon_label.setPixmap(icon.pixmap(QSize(18, 18)))
        except Exception:
            pass
        layout.addWidget(icon_label)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(0)
        name_label = QLabel(name)
        name_label.setObjectName("queueItemName")
        path_label = QLabel(path)
        path_label.setObjectName("queueItemPath")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_layout.addWidget(name_label)
        text_layout.addWidget(path_label)
        layout.addLayout(text_layout, 1)

        remove_btn = QToolButton()
        remove_btn.setAutoRaise(True)
        remove_btn.setFixedSize(24, 24)
        remove_btn.setToolTip("Remove from list")
        app = QApplication.instance()
        theme = str(app.property("sshdockTheme") if app is not None else "dark").lower()
        remove_color = "#64748b" if theme == "light" else "#aebccc"
        try:
            import qtawesome as qta
            remove_btn.setIcon(qta.icon("fa5s.times", color=remove_color))
        except Exception:
            remove_btn.setIcon(_queue_icon("fa5s.times"))
        remove_btn.setIconSize(QSize(12, 12))
        remove_btn.clicked.connect(self.removed.emit)
        layout.addWidget(remove_btn)


class TransferCancelled(Exception):
    pass


def _human_size(value):
    if value is None:
        return "—"
    value = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return "—"


def _human_eta(seconds):
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


class _TransferDialog(QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 190)
        self.setModal(False)
        self._cancel_callback = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(8)

        self.item_label = QLabel("Preparing…")
        root.addWidget(self.item_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        root.addWidget(self.progress)
        self.stats = QLabel("0 B / 0 B · 0 B/s · ETA —")
        root.addWidget(self.stats)
        self.status = QLabel("")
        root.addWidget(self.status)

        row = QHBoxLayout()
        row.addStretch()
        self.cancel_btn = QPushButton("Cancel")
        self.close_btn = QPushButton("Close")
        self.close_btn.hide()
        self.cancel_btn.clicked.connect(self._cancel)
        self.close_btn.clicked.connect(self.close)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.close_btn)
        root.addLayout(row)

    def set_cancel_callback(self, callback):
        self._cancel_callback = callback

    def _cancel(self):
        self.cancel_btn.setEnabled(False)
        self.status.setText("Cancelling…")
        if callable(self._cancel_callback):
            try:
                self._cancel_callback()
            except Exception:
                pass

    def update_transfer(self, label, done, total, rate, eta):
        self.item_label.setText(label)
        self.progress.setValue(int(done * 100 / total) if total else 0)
        self.stats.setText(
            f"{_human_size(done)} / {_human_size(total)} · "
            f"{_human_size(rate)}/s · ETA {_human_eta(eta)}"
        )

    def completed(self, message):
        self.progress.setValue(100)
        self.status.setText(message)
        self.cancel_btn.hide()
        self.close_btn.show()

    def cancelled(self):
        self.status.setText("Transfer cancelled")
        self.cancel_btn.hide()
        self.close_btn.show()

    def failed(self, message):
        self.status.setText(message)
        self.cancel_btn.hide()
        self.close_btn.show()




class _EditorWorkspace(QWidget):
    """Independent non-modal multi-tab editor with media preview support."""

    def __init__(self, backend, parent=None):
        super().__init__(None)
        self.backend = backend
        self.setWindowTitle("Editor")
        self.resize(1100, 760)
        self.setMinimumSize(520, 360)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinMaxButtonsHint
        )
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._tabs = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.setObjectName("editorWorkspace")
        self.tabs.setObjectName("editorTabs")
        root.addWidget(self.tabs, 1)

        foot = QHBoxLayout()
        foot.setContentsMargins(10, 4, 10, 6)
        self.global_status = QLabel("")
        self.global_status.setObjectName("editorStatus")
        foot.addWidget(self.global_status, 1)
        root.addLayout(foot)

    @staticmethod
    def _text_editor(path, content, writable, backend):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(10, 6, 10, 6)
        path_lbl = QLabel(path)
        path_lbl.setObjectName("editorPath")
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        toolbar.addWidget(path_lbl, 1)

        save_btn = None
        if writable:
            save_btn = QPushButton("Save")
            save_btn.setObjectName("editorSaveButton")
            toolbar.addWidget(save_btn)
        lay.addLayout(toolbar)

        view = QPlainTextEdit()
        view.setReadOnly(not writable)
        view.setPlainText((content or "")[: 2 * 1024 * 1024])
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Cascadia Code")
        if not font.exactMatch():
            font = QFont("Consolas")
        font.setPointSize(13)
        view.setFont(font)
        view.setObjectName("editorView")
        CodeHighlighter(view.document(), CodeHighlighter.language_for_path(path), path=path)
        lay.addWidget(view, 1)

        status = QLabel("Ready" if writable else "Read-only")
        status.setObjectName("editorStatus")
        status.setContentsMargins(10, 2, 10, 6)
        lay.addWidget(status)

        return page, view, status, save_btn

    def open_file(self, path, content, *, writable=True):
        if path in self._tabs:
            idx = self.tabs.indexOf(self._tabs[path]["widget"])
            if idx >= 0:
                self.tabs.setCurrentIndex(idx)
            self.show()
            self.raise_()
            return

        page, view, status, save_btn = self._text_editor(path, content, writable, self.backend)
        toolbar = page.layout().itemAt(0).layout()
        name = posixpath.basename(path) or path
        idx = self.tabs.addTab(page, name)
        self.tabs.setTabToolTip(idx, path)
        self.tabs.setCurrentIndex(idx)
        record = {
            "widget": page, "view": view, "status": status,
            "task": None, "cleanup": None, "player": None, "temp": None,
        }
        self._tabs[path] = record

        if save_btn is not None:
            def _save(p=path, v=view, st=status):
                st.setText("Saving…")
                value = v.toPlainText()
                async def _do():
                    try:
                        await self.backend.write_text(p, value)
                        st.setText("Saved")
                        self.global_status.setText(f"Saved {posixpath.basename(p)}")
                    except Exception as exc:
                        st.setText(f"Save failed: {exc}")
                        self.global_status.setText(f"Save failed: {str(exc)[:120]}")
                try:
                    from app.core.runtime import create_task
                    create_task(_do(), name="editor-save")
                except Exception:
                    pass
            save_btn.clicked.connect(_save)

        self.show()
        self.raise_()
        view.setFocus(Qt.FocusReason.OtherFocusReason)

    def open_media(self, path, *, is_video, is_audio=False):
        if path in self._tabs:
            idx = self.tabs.indexOf(self._tabs[path]["widget"])
            if idx >= 0:
                self.tabs.setCurrentIndex(idx)
            self.show()
            self.raise_()
            return self._tabs[path].get("task")

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(10, 6, 10, 6)
        path_lbl = QLabel(path)
        path_lbl.setObjectName("editorPath")
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        toolbar.addWidget(path_lbl, 1)
        transfer_status = QLabel("Preparing…")
        transfer_status.setObjectName("editorStatus")
        toolbar.addWidget(transfer_status)
        stop_download = QPushButton("Stop download")
        toolbar.addWidget(stop_download)
        save_media_btn = QPushButton("Save As")
        save_media_btn.setEnabled(False)
        toolbar.addWidget(save_media_btn)
        lay.addLayout(toolbar)

        inner = QTabWidget()
        inner.setDocumentMode(True)
        preview = QWidget()
        preview_lay = QVBoxLayout(preview)
        preview_lay.setContentsMargins(10, 10, 10, 10)
        preview_lay.setSpacing(8)
        preview_status = QLabel("Downloading…")
        preview_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_lay.addWidget(preview_status)

        player = None
        audio_output = None
        video_widget = None
        image_label = None
        media_title = None
        if (is_video or is_audio) and HAS_MEDIA:
            if is_video:
                video_widget = QVideoWidget()
                preview_lay.addWidget(video_widget, 1)
            else:
                media_title = QLabel("Audio preview")
                media_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
                media_title.setObjectName("mediaPreviewTitle")
                preview_lay.addWidget(media_title, 1)

            controls = QHBoxLayout()
            play_btn = QPushButton("Play / Pause")
            stop_btn = QPushButton("Stop")
            controls.addWidget(play_btn)
            controls.addWidget(stop_btn)
            controls.addStretch()
            preview_lay.addLayout(controls)

            player = QMediaPlayer()
            audio_output = QAudioOutput()
            audio_output.setVolume(1.0)
            player.setAudioOutput(audio_output)
            if video_widget is not None:
                player.setVideoOutput(video_widget)

            play_btn.clicked.connect(
                lambda: player.pause()
                if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                else player.play()
            )
            stop_btn.clicked.connect(player.stop)

            def _media_error(*_):
                if is_video:
                    preview_status.setText(
                        "Video is still downloading; playback will retry as data arrives."
                    )
                else:
                    preview_status.setText(
                        "Audio is still downloading; playback will retry as data arrives."
                    )
                try:
                    if QUrl is not None:
                        player.setSource(QUrl())
                except Exception:
                    pass

            player.errorOccurred.connect(_media_error)
        elif is_video:
            note = QLabel(
                "Qt Multimedia is unavailable, but the file can still be downloaded "
                "and opened as source."
            )
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview_lay.addWidget(note, 1)
        elif is_audio:
            note = QLabel(
                "Qt Multimedia is unavailable, but the audio file can still be "
                "downloaded and opened as source."
            )
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview_lay.addWidget(note, 1)
        else:
            image_label = QLabel()
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview_lay.addWidget(image_label, 1)

        inner.addTab(preview, "Preview")

        source_page, source_view, source_status, _ = self._text_editor(
            path, "", False, self.backend
        )
        source_view.setPlaceholderText("Loading binary source preview…")
        inner.addTab(source_page, "Source")
        inner.currentChanged.connect(
            lambda idx, p=path, v=source_view, st=source_status:
                self._load_media_source(p, v, st) if idx == 1 and not v.toPlainText() else None
        )
        lay.addWidget(inner, 1)

        name = posixpath.basename(path) or path
        idx = self.tabs.addTab(page, name)
        self.tabs.setTabToolTip(idx, path)
        self.tabs.setCurrentIndex(idx)

        record = {
            "widget": page, "view": source_view, "status": transfer_status,
            "task": None, "source_task": None, "cleanup": None, "player": player,
            "audio_output": audio_output, "temp": None, "stop": None,
            "save_as": save_media_btn,
        }
        self._tabs[path] = record

        async def _download():
            suffix = posixpath.splitext(path)[1]
            temp = tempfile.NamedTemporaryFile(
                prefix="sshdock-media-", suffix=suffix, delete=False
            )
            temp_path = temp.name
            temp.close()
            record["temp"] = temp_path
            started = time.monotonic()
            last_ui = [0.0]

            def progress(_src, _dst, done, total):
                now = time.monotonic()
                if now - last_ui[0] < 0.12 and done != total:
                    return
                last_ui[0] = now
                elapsed = max(0.001, now - started)
                rate = done / elapsed
                percent = int(done * 100 / total) if total else 0
                text = f"Downloading · {percent}% · {_human_size(done)} / {_human_size(total)} · {_human_size(rate)}/s"
                transfer_status.setText(text)
                preview_status.setText(text)
                if player is not None:
                    _try_media()
                if image_label is not None and os.path.exists(temp_path):
                    try:
                        pixmap = QPixmap(temp_path)
                        if not pixmap.isNull():
                            image_label.setPixmap(
                                pixmap.scaled(
                                    image_label.size(),
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation,
                                )
                            )
                    except Exception:
                        pass

            def _try_media():
                if player is None or QUrl is None or not os.path.exists(temp_path):
                    return
                try:
                    size = os.path.getsize(temp_path)
                except OSError:
                    return
                if size < 64 * 1024:
                    return
                try:
                    if player.source().isEmpty():
                        player.setSource(QUrl.fromLocalFile(temp_path))
                    if player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                        player.play()
                        preview_status.setText(
                            "Playing while downloading…"
                            if is_video else "Playing audio while downloading…"
                        )
                except Exception:
                    pass

            try:
                await self.backend.download(path, temp_path, progress_handler=progress)
                if player is not None:
                    _try_media()
                if image_label is not None and os.path.exists(temp_path):
                    try:
                        pixmap = QPixmap(temp_path)
                        if not pixmap.isNull():
                            image_label.setPixmap(
                                pixmap.scaled(
                                    image_label.size(),
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation,
                                )
                            )
                    except Exception:
                        pass
                transfer_status.setText("Ready")
                preview_status.setText("Download complete")
                save_media_btn.setEnabled(True)
                stop_download.setText("Clear media")
                try:
                    stop_download.clicked.disconnect()
                except Exception:
                    pass
                stop_download.clicked.connect(lambda: self._cleanup_record(path))
                if player is not None:
                    _try_video()
            except asyncio.CancelledError:
                transfer_status.setText("Stopped")
                preview_status.setText("Download stopped")
                save_media_btn.setEnabled(False)
                stop_download.setText("Clear media")
                try:
                    stop_download.clicked.disconnect()
                except Exception:
                    pass
                stop_download.clicked.connect(lambda: self._cleanup_record(path))
                raise
            except Exception as exc:
                transfer_status.setText("Failed")
                preview_status.setText(f"Download failed: {str(exc)[:160]}")
                save_media_btn.setEnabled(False)
                stop_download.setText("Clear media")
                try:
                    stop_download.clicked.disconnect()
                except Exception:
                    pass
                stop_download.clicked.connect(lambda: self._cleanup_record(path))
            finally:
                pass

        async def _source_loader():
            source_status.setText("Loading source…")
            try:
                value = await self.backend.read_text(path, limit=2 * 1024 * 1024)
                source_view.setPlainText(value)
                source_status.setText("Read-only source preview")
            except asyncio.CancelledError:
                source_status.setText("Source load stopped")
                raise
            except Exception as exc:
                source_status.setText(f"Source unavailable: {str(exc)[:120]}")

        def _cleanup():
            task = record.get("task")
            if task is not None and not task.done():
                task.cancel()
            source_task = record.get("source_task")
            if source_task is not None and not source_task.done():
                source_task.cancel()
            try:
                if player is not None:
                    player.stop()
                    if QUrl is not None:
                        player.setSource(QUrl())
            except Exception:
                pass
            temp_path = record.get("temp")
            if temp_path:
                try:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                except OSError:
                    pass

        def _save_media_as():
            temp_path = record.get("temp")
            if not temp_path or not os.path.exists(temp_path):
                return
            destination = QFileDialog.getSaveFileName(
                self, "Save media", posixpath.basename(path)
            )[0]
            if not destination:
                return
            try:
                shutil.copy2(temp_path, destination)
                self.global_status.setText(f"Saved {os.path.basename(destination)}")
            except Exception as exc:
                self.global_status.setText(f"Save failed: {str(exc)[:140]}")

        save_media_btn.clicked.connect(_save_media_as)
        record["cleanup"] = _cleanup
        self._media_source_loaders = getattr(self, "_media_source_loaders", {})
        self._media_source_loaders[path] = _source_loader
        self.show()
        self.raise_()

        try:
            from app.core.runtime import create_task
            task = create_task(_download(), name="editor-media-download")
            record["task"] = task
        except Exception:
            record["task"] = None

        def _stop_download():
            current = record.get("task")
            if current is not None and not current.done():
                current.cancel()

        record["stop"] = _stop_download
        stop_download.clicked.connect(_stop_download)
        return record["task"]

    def _load_media_source(self, path, view, status):
        record = self._tabs.get(path)
        if record is None:
            return
        if record.get("source_task") is not None and not record["source_task"].done():
            return
        loader = getattr(self, "_media_source_loaders", {}).get(path)
        if loader is None:
            return
        try:
            from app.core.runtime import create_task
            record["source_task"] = create_task(loader(), name="editor-media-source")
        except Exception:
            record["source_task"] = None

    def _cleanup_record(self, path):
        record = self._tabs.get(path)
        if record is None:
            return
        cleanup = record.get("cleanup")
        if callable(cleanup):
            cleanup()

    def _close_tab(self, index):
        widget = self.tabs.widget(index)
        if widget is None:
            return
        path = next((p for p, rec in self._tabs.items() if rec.get("widget") is widget), None)
        self.tabs.removeTab(index)
        if path is not None:
            record = self._tabs.pop(path, {})
            cleanup = record.get("cleanup")
            if callable(cleanup):
                cleanup()
            getattr(self, "_media_source_loaders", {}).pop(path, None)
        if self.tabs.count() == 0:
            self.hide()

    def closeEvent(self, event):
        event.ignore()
        self.hide()


class _UploadSelectionDialog(QDialog):
    """Small local picker that can accumulate both files and folders in one upload."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Upload files and folders")
        self.resize(620, 430)
        self.selected_paths: list[str] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(8)

        title = QLabel("Choose files and folders to upload")
        title.setObjectName("panelTitle")
        root.addWidget(title)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setAlternatingRowColors(False)
        root.addWidget(self.list, 1)

        actions = QHBoxLayout()
        add_files = QPushButton("Add files")
        add_files.setIcon(self._icon("fa5s.file"))
        add_files.clicked.connect(self._add_files)
        add_folder = QPushButton("Add folder")
        add_folder.setIcon(self._icon("fa5s.folder-open"))
        add_folder.clicked.connect(self._add_folder)
        actions.addWidget(add_files)
        actions.addWidget(add_folder)
        actions.addStretch()
        root.addLayout(actions)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Upload")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    @staticmethod
    def _icon(name: str):
        try:
            import qtawesome as qta
            return qta.icon(name)
        except Exception:
            return QIcon()

    def _add_paths(self, paths: list[str]):
        known = {os.path.normcase(os.path.abspath(p)) for p in self.selected_paths}
        for path in paths:
            normalized = os.path.abspath(path)
            key = os.path.normcase(normalized)
            if key in known:
                continue
            known.add(key)
            self.selected_paths.append(normalized)
            label = os.path.basename(os.path.normpath(normalized)) or normalized
            item = QListWidgetItem()
            item.setToolTip(normalized)
            item.setData(Qt.ItemDataRole.UserRole, normalized)
            self.list.addItem(item)
            row = _QueueRowWidget(
                label,
                normalized,
                _queue_icon(label, is_dir=os.path.isdir(normalized)),
                self.list,
            )
            item.setSizeHint(row.sizeHint())
            row.removed.connect(lambda it=item: self._remove_item(it))
            self.list.setItemWidget(item, row)

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select files to upload")
        self._add_paths(files)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder to upload")
        if folder:
            self._add_paths([folder])

    def _remove_item(self, item: QListWidgetItem):
        row = self.list.row(item)
        if row >= 0:
            self.list.takeItem(row)
        self.selected_paths = [
            self.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
        ]

    def _accept(self):
        self.selected_paths = [
            self.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
        ]
        if not self.selected_paths:
            return
        self.accept()


class _ExtractDialog(QDialog):
    def __init__(self, archive_path: str, destination_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Extract archive")
        self.resize(620, 210)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(8)

        archive_label = QLabel(f"Archive: {posixpath.basename(archive_path)}")
        archive_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(archive_label)

        root.addWidget(QLabel("Extract into folder:"))
        self.name_edit = QLineEdit(destination_name)
        self.name_edit.setPlaceholderText("Folder name")
        root.addWidget(self.name_edit)

        root.addWidget(QLabel("Destination: current explorer directory"))

        self.error = QLabel("")
        root.addWidget(self.error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Extract")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @property
    def destination_name(self) -> str:
        return self.name_edit.text().strip()

    def _accept(self):
        name = self.destination_name
        if not TerminalFileExplorer._valid_child_name(name):
            self.error.setText("Enter a valid folder name without '/' or '\\'.")
            self.name_edit.setFocus()
            return
        self.accept()


class TerminalFileExplorer(QWidget):
    """Compact remote filesystem explorer coupled to one interactive SSH shell."""

    closed_requested = Signal()

    def __init__(
        self,
        backend: ServerFileBackend,
        change_dir: Callable[[str], None],
        run_command: Callable[[str], object] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.backend = backend
        self.change_dir = change_dir
        self.run_command = run_command
        self.current_path = "/"
        self._loading_paths: set[str] = set()
        self._loaded_paths: set[str] = set()
        self._items_by_path: dict[str, QTreeWidgetItem] = {}
        self._editor: _EditorWorkspace | None = None
        self._build()

    def _build(self):
        self.setObjectName("terminalExplorer")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 6)
        root.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("EXPLORER")
        title.setObjectName("terminalExplorerTitle")
        header.addWidget(title)
        header.addStretch()
        # Search and reload stay together in the top action row.
        self.search_btn = self._button("fa5s.search", "Search files", self._toggle_search)
        self.search_btn.setCheckable(True)
        self.search_btn.setAutoRaise(True)
        self.refresh_btn = self._button("fa5s.sync-alt", "Reload", self.refresh)
        self.new_folder_btn = self._button("fa5s.folder-plus", "New folder", self.new_folder)
        self.new_file_btn = self._button("fa5s.file", "New file", self.new_file)
        self.close_btn = self._button("fa5s.times", "Close explorer", self.closed_requested.emit)
        header.addWidget(self.search_btn)
        header.addWidget(self.refresh_btn)
        header.addWidget(self.new_folder_btn)
        header.addWidget(self.new_file_btn)
        header.addWidget(self.close_btn)
        root.addLayout(header)

        path_row = QHBoxLayout()
        self.path_label = QLabel("/")
        self.path_label.setObjectName("terminalExplorerPathLabel")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_label.setToolTip("/")
        self.path_label.setSizePolicy(
            self.path_label.sizePolicy().horizontalPolicy(),
            self.path_label.sizePolicy().verticalPolicy(),
        )
        path_row.addWidget(self.path_label, 1)
        root.addLayout(path_row)

        self.search = QLineEdit()
        self.search.setObjectName("terminalExplorerSearch")
        self.search.setPlaceholderText("Search files by name, path or type…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.search.setVisible(False)
        root.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setObjectName("terminalExplorerTree")
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.itemExpanded.connect(self._expanded)
        self.tree.itemDoubleClicked.connect(self._activated)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        root.addWidget(self.tree, 1)

        footer = QHBoxLayout()
        self.status = QLabel("Waiting for shell directory…")
        self.status.setObjectName("terminalExplorerStatus")
        footer.addWidget(self.status, 1)
        self.delete_btn = self._button(
            "fa5s.trash", "Delete selected files/folders", self.delete_selected
        )
        self.upload_btn = self._button(
            "fa5s.upload", "Upload files and folders", self.upload_files
        )
        self.download_btn = self._button(
            "fa5s.download", "Download selected files/folders", self.download_selected
        )
        footer.addWidget(self.delete_btn)
        footer.addWidget(self.upload_btn)
        footer.addWidget(self.download_btn)
        root.addLayout(footer)

        # Shared application stylesheet owns the explorer colors.

    def _button(self, icon_name: str, tooltip: str, callback):
        b = QToolButton()
        b.setObjectName("feNav")
        b.setAutoRaise(True)
        b.setFixedSize(26, 26)
        b.setToolTip(tooltip)
        b.setProperty("sshdockIconName", icon_name)
        try:
            import qtawesome as qta
            theme = str(QApplication.instance().property("sshdockTheme") or "dark").lower()
            b.setIcon(qta.icon(icon_name, color="#475569" if theme == "light" else "#aebccc"))
            b.setIconSize(QSize(12, 12))
        except Exception:
            b.setText("•")
        def invoke_callback():
            try:
                result = callback()
                if inspect.isawaitable(result):
                    self._spawn(result)
            except Exception as exc:
                self.status.setText(f"Action failed: {str(exc)[:120]}")

        b.clicked.connect(lambda _=False: invoke_callback())
        return b

    def _icon_for(self, icon_name: str, color: str):
        try:
            import qtawesome as qta
            return qta.icon(icon_name, color=color)
        except Exception:
            return QIcon()
    
    def apply_theme(self, theme: str | None = None) -> None:
        name = str(theme or QApplication.instance().property("sshdockTheme") or "dark").lower()
        try:
            import qtawesome as qta
            color = "#475569" if name == "light" else "#aebccc"
            for button in self.findChildren(QToolButton):
                icon_name = button.property("sshdockIconName")
                if icon_name:
                    button.setIcon(qta.icon(str(icon_name), color=color))
        except Exception:
            pass
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _selected_entries(self) -> list[Entry]:
        entries: list[Entry] = []
        seen: set[str] = set()
        for item in self.tree.selectedItems():
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, Entry):
                key = data.path.rstrip("/") or "/"
                if key not in seen:
                    seen.add(key)
                    entries.append(data)

        # A selected directory owns its descendants. Do not delete/download a
        # child separately when its parent directory is already selected.
        entries.sort(key=lambda ent: (ent.path.count("/"), ent.path))
        result: list[Entry] = []
        selected_dirs: list[str] = []
        for ent in entries:
            if any(
                ent.path == parent
                or ent.path.startswith(parent.rstrip("/") + "/")
                for parent in selected_dirs
            ):
                continue
            result.append(ent)
            if ent.is_dir:
                selected_dirs.append(ent.path)
        return result

    def _make_transfer_dialog(self, title: str):
        dialog = _TransferDialog(title, self)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def _upload_total_bytes(self, sources: list[str]) -> int:
        total = 0
        for source in sources:
            try:
                if os.path.isdir(source):
                    for root, _dirs, files in os.walk(source):
                        for name in files:
                            try:
                                total += os.path.getsize(os.path.join(root, name))
                            except OSError:
                                pass
                else:
                    total += os.path.getsize(source)
            except OSError:
                pass
        return total

    async def _run_transfer_batch(self, entries, dialog: _TransferDialog, *, upload: bool):
        cancel_event = asyncio.Event()
        total_bytes = self._upload_total_bytes([e.path for e in entries]) if upload else 0
        if not upload:
            total_bytes = sum(int(e.size or 0) for e in entries if not e.is_dir)

        overall_done = 0
        file_done: dict[str, int] = {}
        started = time.monotonic()
        current_task = asyncio.current_task()

        def cancel():
            cancel_event.set()
            if current_task is not None and not current_task.done():
                current_task.cancel()

        dialog.set_cancel_callback(cancel)

        def progress(src, _dst, done, total):
            nonlocal overall_done
            if cancel_event.is_set():
                raise TransferCancelled("Transfer cancelled")
            key = os.path.normcase(os.path.abspath(str(src)))
            previous = file_done.get(key, 0)
            overall_done += max(0, done - previous)
            file_done[key] = done

            now = time.monotonic()
            elapsed = max(0.001, now - started)
            rate = overall_done / elapsed
            eta = (
                (total_bytes - overall_done) / rate
                if rate > 0 and total_bytes > overall_done
                else None
            )
            label = os.path.basename(str(src)) or "transfer"
            dialog.update_transfer(
                ("Uploading " if upload else "Downloading ") + label,
                overall_done,
                total_bytes or total,
                rate,
                eta,
            )

        completed_items = 0
        try:
            for ent in entries:
                if cancel_event.is_set():
                    raise TransferCancelled("Transfer cancelled")
                if upload:
                    name = os.path.basename(os.path.normpath(ent.path))
                    target = posixpath.join(self.current_path, name)
                    await self.backend.upload(
                        ent.path, target, progress_handler=progress
                    )
                else:
                    target_dir = str(dialog.property("targetDir") or "")
                    await self.backend.download(
                        ent.path,
                        os.path.join(target_dir, ent.name),
                        progress_handler=progress,
                    )
                completed_items += 1

            dialog.completed(
                f"{'Uploaded' if upload else 'Downloaded'} "
                f"{completed_items}/{len(entries)} item(s)"
            )
        except (TransferCancelled, asyncio.CancelledError):
            dialog.cancelled()
        except Exception as exc:
            dialog.failed(f"Transfer failed: {str(exc)[:180]}")
        finally:
            if upload:
                self._show_path()

    def _transfer_progress(self, direction: str, done: int, total: int):
        if total <= 0:
            self.status.setText(f"{direction}: {done:,} bytes")
            return
        percent = max(0, min(100, int(done * 100 / total)))
        self.status.setText(f"{direction}: {percent}% · {done:,}/{total:,} bytes")

    async def upload_files(self):
        chooser = _UploadSelectionDialog(self)
        if chooser.exec() != QDialog.DialogCode.Accepted:
            return
        sources = list(chooser.selected_paths)
        if not sources:
            return
        entries = [
            Entry(
                name=os.path.basename(os.path.normpath(local)),
                path=local,
                is_dir=os.path.isdir(local),
            )
            for local in sources
        ]

        dialog = self._make_transfer_dialog("Upload")
        async def _run():
            try:
                await self._run_transfer_batch(entries, dialog, upload=True)
            except asyncio.CancelledError:
                pass
        self._spawn(_run())

    async def new_folder(self):
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if not ok:
            return
        name = name.strip()
        if not self._valid_child_name(name):
            self.status.setText("Enter a valid folder name (without /).")
            return
        path = posixpath.join(self.current_path, name)
        try:
            await self.backend.stat(path)
            self.status.setText(f"Folder already exists: {name}")
            return
        except Exception:
            pass
        try:
            await self.backend.mkdir(path)
            self.status.setText(f"Created folder {name}")
            self._show_path()
        except Exception as exc:
            self.status.setText(f"Create folder failed: {str(exc)[:140]}")

    async def new_file(self):
        name, ok = QInputDialog.getText(self, "New file", "File name:")
        if not ok:
            return
        name = name.strip()
        if not self._valid_child_name(name):
            self.status.setText("Enter a valid file name (without /).")
            return
        path = posixpath.join(self.current_path, name)
        try:
            await self.backend.stat(path)
            self.status.setText(f"File already exists: {name}")
            return
        except Exception:
            pass
        try:
            await self.backend.write_text(path, "")
            self.status.setText(f"Created file {name}")
            self._show_path()
            self._spawn(self._open_file(Entry(name=name, path=path, is_dir=False)))
        except Exception as exc:
            self.status.setText(f"Create file failed: {str(exc)[:140]}")

    @staticmethod
    def _valid_child_name(name: str) -> bool:
        return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name

    async def download_selected(self):
        entries = self._selected_entries()
        if not entries:
            self.status.setText("Select one or more files/folders to download.")
            return
        target = QFileDialog.getExistingDirectory(self, "Download to")
        if not target:
            return

        dialog = self._make_transfer_dialog("Download")
        dialog.setProperty("targetDir", target)
        async def _run():
            try:
                await self._run_transfer_batch(entries, dialog, upload=False)
            except asyncio.CancelledError:
                pass
        self._spawn(_run())

    async def delete_selected(self):
        entries = self._selected_entries()
        if not entries:
            self.status.setText("Select one or more files/folders to delete.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Delete selected items")
        dialog.resize(560, 420)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(8)

        root.addWidget(QLabel("Delete these selected items?"))
        listing = QListWidget()
        for ent in entries:
            kind = "Folder" if ent.is_dir else "File"
            row = QListWidgetItem()
            row.setToolTip(ent.path)
            row.setData(Qt.ItemDataRole.UserRole, ent)
            listing.addItem(row)
            widget = _QueueRowWidget(
                ent.name,
                f"{kind} · {ent.path}",
                _queue_icon(ent.name, is_dir=ent.is_dir),
                listing,
            )
            row.setSizeHint(widget.sizeHint())
            widget.removed.connect(lambda it=row: listing.takeItem(listing.row(it)))
            listing.setItemWidget(row, widget)
        root.addWidget(listing, 1)

        warning = QLabel("This action is permanent and cannot be undone.")
        root.addWidget(warning)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Yes
        )
        buttons.button(QDialogButtonBox.StandardButton.Yes).setText("Delete")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        root.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        entries = [
            listing.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(listing.count())
        ]
        entries = [ent for ent in entries if isinstance(ent, Entry)]
        if not entries:
            self.status.setText("Nothing to delete.")
            return

        deleted = 0
        failed = []
        for ent in entries:
            try:
                await self.backend.delete(ent.path, ent.is_dir)
                deleted += 1
            except Exception as exc:
                failed.append(f"{ent.name}: {exc}")

        self._show_path()
        if failed:
            self.status.setText(
                f"Deleted {deleted}/{len(entries)} item(s); {len(failed)} failed"
            )
        else:
            self.status.setText(f"Deleted {deleted} item(s)")

    def _normalize(self, path: str) -> str:
        path = posixpath.normpath(path or "/")
        return path if path.startswith("/") else "/" + path

    def set_cwd(self, path: str, *, refresh: bool = True):
        normalized = self._normalize(path)
        changed = normalized != self.current_path
        self.current_path = normalized
        self.path_label.setText(normalized)
        self.path_label.setToolTip(normalized)
        if changed or refresh:
            self._show_path()

    def _show_path(self):
        self.tree.clear()
        self._loaded_paths.clear()
        self._items_by_path.clear()
        self._loading_paths.clear()
        self.status.setText("Loading…")

        # Use QTreeWidget's invisible root as the current-directory container.
        # Its children become the visible top-level filesystem entries.
        root = self.tree.invisibleRootItem()
        self._items_by_path[self.current_path] = root
        if self.current_path not in self._loading_paths and self.current_path not in self._loaded_paths:
            self._spawn(self._load_directory(self.current_path))

    def _make_dir_item(self, path: str, display: str | None = None) -> QTreeWidgetItem:
        item = QTreeWidgetItem([display if display is not None else posixpath.basename(path) or path])
        item.setData(0, Qt.ItemDataRole.UserRole, {"path": path, "dir": True})
        item.setToolTip(0, path)
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        placeholder = QTreeWidgetItem(["…"])
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        item.addChild(placeholder)
        self._items_by_path[path] = item
        return item

    async def _load_directory(self, path: str):
        if path in self._loading_paths:
            return
        self._loading_paths.add(path)
        item = self._items_by_path.get(path)
        if item is not None:
            item.takeChildren()
            loading = QTreeWidgetItem(["Loading…"])
            loading.setFlags(Qt.ItemFlag.NoItemFlags)
            item.addChild(loading)
        try:
            entries = await self.backend.list(path, True)
            entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
            item = self._items_by_path.get(path)
            if item is None:
                return
            item.takeChildren()
            visible = 0

            parent = posixpath.dirname(path.rstrip("/")) or "/"
            if path != "/":
                parent_item = QTreeWidgetItem([".."])
                parent_item.setData(0, Qt.ItemDataRole.UserRole, {
                    "parent": True,
                    "path": parent,
                })
                parent_item.setToolTip(0, f"Parent directory: {parent}")
                theme = str(QApplication.instance().property("sshdockTheme") or "dark").lower()
                parent_item.setIcon(
                    0,
                    self._icon_for(
                        "fa5s.level-up-alt",
                        "#475569" if theme == "light" else "#aebccc",
                    ),
                )
                item.addChild(parent_item)

            for ent in entries:
                if ent.is_dir:
                    child = self._make_dir_item(ent.path, display=ent.name)
                    theme = str(QApplication.instance().property("sshdockTheme") or "dark").lower()
                    ft = classify(ent.name, is_dir=True)
                    child.setIcon(0, self._icon_for(ft.icon, color_for(ft.key, theme)))
                    item.addChild(child)
                else:
                    child = QTreeWidgetItem([ent.name])
                    child.setData(0, Qt.ItemDataRole.UserRole, ent)
                    child.setToolTip(0, ent.path)
                    theme = str(QApplication.instance().property("sshdockTheme") or "dark").lower()
                    ft = classify(ent.name)
                    child.setIcon(0, self._icon_for(ft.icon, color_for(ft.key, theme)))
                    child.setChildIndicatorPolicy(
                        QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator
                    )
                    item.addChild(child)
                visible += 1
            self._loaded_paths.add(path)
            if path == self.current_path:
                self.status.setText(f"{visible} items · shell cwd")
            self._filter()
        except Exception as exc:
            if item is not None:
                item.takeChildren()
                err = QTreeWidgetItem([f"Error: {str(exc)[:60]}"])
                err.setFlags(Qt.ItemFlag.NoItemFlags)
                item.addChild(err)
            self.status.setText(f"Explorer error: {str(exc)[:120]}")
        finally:
            self._loading_paths.discard(path)

    def _expanded(self, item: QTreeWidgetItem):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if isinstance(data, dict) and data.get("dir"):
            path = data.get("path")
        elif isinstance(data, Entry) and data.is_dir:
            path = data.path
        else:
            return
        if path and path not in self._loaded_paths:
            self._spawn(self._load_directory(path))

    def _activated(self, item: QTreeWidgetItem, _column: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, Entry):
            if data.is_dir:
                self.change_dir(data.path)
                return
            self._spawn(self._open_file(data))
        elif isinstance(data, dict):
            if data.get("parent"):
                self.change_dir(data.get("path") or "/")
                return
            if data.get("dir"):
                path = data.get("path") or self.current_path
                if path != self.current_path:
                    self.change_dir(path)

    def go_up(self):
        target = posixpath.dirname(self.current_path.rstrip("/")) or "/"
        self.change_dir(target)

    def _toggle_search(self):
        opened = self.search_btn.isChecked()
        self.search.setVisible(opened)
        if opened:
            self.search.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.search.clear()

    def refresh(self):
        self._show_path()

    def _filter(self):
        query = self.search.text().strip().lower()
        root = self._items_by_path.get(self.current_path)
        if root is None:
            root = self.tree.invisibleRootItem()

        def matches(data, name: str) -> bool:
            if not query:
                return True
            parts = [name]
            if isinstance(data, Entry):
                parts.extend([
                    str(data.path or ""),
                    "directory" if data.is_dir else "file",
                ])
                try:
                    ft = classify(data.name, is_dir=data.is_dir)
                    parts.extend([str(ft.key or ""), str(ft.icon or "")])
                except Exception:
                    pass
            elif isinstance(data, dict):
                parts.extend([
                    str(data.get("path") or ""),
                    "directory" if data.get("dir") else "",
                ])
            haystack = " ".join(parts).lower()
            return query in haystack

        def walk(node: QTreeWidgetItem) -> bool:
            any_child = False
            for i in range(node.childCount()):
                child = node.child(i)
                data = child.data(0, Qt.ItemDataRole.UserRole)
                name = (child.text(0) or "").strip().lower()
                match = matches(data, name)
                is_dir = (isinstance(data, dict) and data.get("dir")) or (
                    isinstance(data, Entry) and data.is_dir
                )
                if is_dir:
                    descendant = walk(child)
                    child.setHidden(bool(query and not match and not descendant))
                    any_child = any_child or match or descendant
                else:
                    child.setHidden(bool(query and not match))
                    any_child = any_child or match
            return any_child

        walk(root)
        # Keep ".." visible as navigation even while searching.
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            data = top.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("parent"):
                top.setHidden(False)

    async def _open_file(self, ent: Entry):
        self.status.setText(f"Opening {ent.name}…")
        try:
            ext = posixpath.splitext(ent.name.lower())[1]
            if ext in IMAGE_EXTS or ext in VIDEO_EXTS or ext in AUDIO_EXTS:
                if self._editor is None:
                    self._editor = _EditorWorkspace(self.backend, parent=None)
                self._editor.open_media(
                    ent.path,
                    is_video=ext in VIDEO_EXTS,
                    is_audio=ext in AUDIO_EXTS,
                )
                self.status.setText(f"Opened {ent.name} in media preview")
                return

            content = await self.backend.read_text(ent.path)
            writable = True
            try:
                if hasattr(self.backend, "stat"):
                    st = await self.backend.stat(ent.path)
                    if st is not None:
                        import stat as _stat
                        mode = int(getattr(st, "permissions", 0) or getattr(st, "st_mode", 0) or 0)
                        writable = bool(mode & _stat.S_IWUSR)
            except Exception:
                writable = True

            if self._editor is None:
                self._editor = _EditorWorkspace(self.backend, parent=None)
            self._editor.open_file(ent.path, content, writable=writable)
            self.status.setText(f"Opened {ent.name}" + (" (editable)" if writable else " (read-only)"))
        except Exception as exc:
            self.status.setText(f"Open failed: {str(exc)[:120]}")

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        if isinstance(data, Entry):
            open_action = menu.addAction("Open / Edit" if not data.is_dir else "Open folder")
            extract_action = None
            if data.is_dir:
                cd_action = menu.addAction("cd in terminal")
                download_action = None
            else:
                cd_action = None
                download_action = menu.addAction("Download")
                extract_action = (
                    menu.addAction("Extract archive…")
                    if classify(data.name).key == "archive"
                    else None
                )
            menu.addSeparator()
            copy_action = menu.addAction("Copy path")
            upload_action = menu.addAction("Upload files and folders here")
            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is open_action:
                self._activated(item, 0)
            elif chosen is copy_action:
                from PySide6.QtWidgets import QApplication
                QApplication.clipboard().setText(data.path)
            elif cd_action is not None and chosen is cd_action:
                self.change_dir(data.path)
            elif download_action is not None and chosen is download_action:
                self._spawn(self.download_one(data))
            elif extract_action is not None and chosen is extract_action:
                self._spawn(self.extract_archive(data))
            elif chosen is upload_action:
                self._spawn(self.upload_files())
        elif isinstance(data, dict) and data.get("dir"):
            cd_action = menu.addAction("cd in terminal")
            copy_action = menu.addAction("Copy path")
            upload_action = menu.addAction("Upload files and folders here")
            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is cd_action:
                self.change_dir(data["path"])
            elif chosen is copy_action:
                from PySide6.QtWidgets import QApplication
                QApplication.clipboard().setText(data["path"])
            elif chosen is upload_action:
                self._spawn(self.upload_files())

    @staticmethod
    def _archive_default_dir(name: str) -> str:
        lower = (name or "").lower()
        for suffix in (
            ".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz4",
            ".tgz", ".tbz", ".tbz2", ".txz", ".tzst",
            ".zip", ".7z", ".rar", ".cab", ".iso", ".jar", ".war", ".ear", ".apk",
            ".gz", ".bz2", ".xz", ".zst", ".lz4", ".lzma",
        ):
            if lower.endswith(suffix):
                value = name[:-len(suffix)]
                return value or "archive"
        value = posixpath.splitext(name or "")[0]
        return value or "archive"

    async def _unique_extract_name(self, archive_name: str) -> str:
        base = self._archive_default_dir(archive_name)
        candidate = base
        counter = 1
        while True:
            try:
                await self.backend.stat(posixpath.join(self.current_path, candidate))
            except Exception:
                return candidate
            candidate = f"{base}_{counter}"
            counter += 1
            if counter > 10000:
                raise RuntimeError("Could not find a free extraction directory name")

    async def _detect_extractor(self, archive_path: str) -> tuple[str, str] | None:
        ssh = self.backend.ssh_getter()
        name = posixpath.basename(archive_path).lower()
        probe = (
            "name=" + shlex.quote(name) + "; "
            'case "$name" in '
            "*.zip) command -v 7z || command -v 7zz || command -v unzip ;; "
            "*.rar) command -v 7z || command -v 7zz || command -v unrar || command -v rar ;; "
            "*.7z|*.cab|*.iso|*.jar|*.war|*.ear|*.apk) command -v 7z || command -v 7zz ;; "
            "*.tar|*.tar.*|*.tgz|*.tbz|*.tbz2|*.txz|*.tzst) command -v 7z || command -v 7zz || command -v tar ;; "
            "*.gz) command -v 7z || command -v 7zz || command -v gzip ;; "
            "*.bz2) command -v 7z || command -v 7zz || command -v bzip2 ;; "
            "*.xz) command -v 7z || command -v 7zz || command -v xz ;; "
            "*.zst) command -v 7z || command -v 7zz || command -v zstd || command -v unzstd ;; "
            "*.lz4) command -v 7z || command -v 7zz || command -v lz4 ;; "
            "*.lzma) command -v 7z || command -v 7zz || command -v xz ;; "
            "*) command -v 7z || command -v 7zz ;; esac"
        )
        result = await ssh.run(probe, timeout=8)
        lines = (result.stdout or "").strip().splitlines()
        tool = lines[-1].strip() if lines else ""
        if not tool:
            return None
        base = posixpath.basename(tool)
        if base in {"7z", "7zz"}:
            kind = "7zip"
        elif base == "unzip":
            kind = "unzip"
        elif base in {"unrar", "rar"}:
            kind = "unrar"
        elif base in {"gzip", "bzip2"}:
            kind = base
        elif base in {"zstd", "unzstd"}:
            kind = "zstd"
        elif base == "lz4":
            kind = "lz4"
        elif base == "xz":
            kind = "xz"
        else:
            kind = "tar"
        return tool, kind

    async def extract_archive(self, ent: Entry):
        if ent.is_dir or classify(ent.name).key != "archive":
            return

        default_name = await self._unique_extract_name(ent.name)
        dialog = _ExtractDialog(ent.path, default_name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        destination_name = dialog.destination_name
        destination = posixpath.join(self.current_path, destination_name)

        try:
            await self.backend.stat(destination)
        except Exception:
            pass
        else:
            self.status.setText(f"Destination already exists: {destination_name}")
            return

        try:
            detected = await self._detect_extractor(ent.path)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Extractor check failed",
                f"Could not check archive tools: {str(exc)[:180]}",
            )
            return

        if detected is None:
            QMessageBox.warning(
                self,
                "Extractor not installed",
                "No suitable extractor is installed on the server for this archive.\n\n"
                "Install 7-Zip (7z/7zz), or the archive-specific tool such as unzip/unrar, "
                "then run Extract again. The extraction itself is normally run in the visible terminal.",
            )
            self.status.setText("No suitable archive extractor is installed.")
            return

        tool, kind = detected
        archive_q = shlex.quote(ent.path)
        destination_q = shlex.quote(destination)
        tool_q = shlex.quote(tool)
        script = (
            "set -u; "
            f"archive={archive_q}; dest={destination_q}; tool={tool_q}; "
            'printf "\n[SSHDock] Extracting: %s\\n" "$archive"; '
            'printf "[SSHDock] Destination: %s\\n" "$dest"; '
            'printf "[SSHDock] Extractor: %s\\n" "$tool"; '
            'mkdir -p -- "$dest" || exit 1; '
            f'if [ "{kind}" = "7zip" ]; then "$tool" x -y "-o$dest" -- "$archive"; '
            f'elif [ "{kind}" = "unzip" ]; then "$tool" "$archive" -d "$dest"; '
            f'elif [ "{kind}" = "unrar" ]; then "$tool" x -o+ "$archive" "$dest/"; '
            f'elif [ "{kind}" = "tar" ]; then "$tool" -xf "$archive" -C "$dest"; '
            f'elif [ "{kind}" = "gzip" ]; then out="$dest/$(basename -- "$archive" .gz)"; "$tool" -dc -- "$archive" > "$out"; '
            f'elif [ "{kind}" = "bzip2" ]; then out="$dest/$(basename -- "$archive" .bz2)"; "$tool" -dc -- "$archive" > "$out"; '
            f'elif [ "{kind}" = "zstd" ]; then out="$dest/$(basename -- "$archive" .zst)"; "$tool" -dc -- "$archive" > "$out"; '
            f'elif [ "{kind}" = "lz4" ]; then out="$dest/$(basename -- "$archive" .lz4)"; "$tool" -dc -- "$archive" > "$out"; '
            f'elif [ "{kind}" = "xz" ]; then out="$dest/$(basename -- "$archive" .xz)"; "$tool" -dc -- "$archive" > "$out"; '
            'else exit 2; fi; '
            'rc=$?; '
            'if [ "$rc" -eq 0 ]; then '
            'printf "[SSHDock] Extract completed successfully: %s\\n" "$dest"; '
            'else printf "[SSHDock] Extract failed with exit code %s\\n" "$rc"; fi'
        )

        if not callable(self.run_command):
            QMessageBox.warning(
                self,
                "Terminal unavailable",
                "The active SSH terminal is not available, so SSHDock cannot run the extraction command visibly.",
            )
            return

        try:
            result = self.run_command(f"sh -c {shlex.quote(script)}")
            if inspect.isawaitable(result):
                self._spawn(result)
            self.status.setText(f"Extract command sent to terminal → {destination_name}")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1500, self.refresh)
            QTimer.singleShot(5000, self.refresh)
        except Exception as exc:
            QMessageBox.warning(self, "Extract failed", str(exc)[:180])

    async def download_one(self, ent: Entry):
        target = QFileDialog.getSaveFileName(self, "Download file", ent.name)[0]
        if not target:
            return
        try:
            await self.backend.download(ent.path, target)
            self.status.setText(f"Downloaded {ent.name}")
        except Exception as exc:
            self.status.setText(f"Download failed: {str(exc)[:100]}")


    def _spawn(self, coro):
        try:
            from app.core.runtime import create_task
            return create_task(coro, name="terminal-explorer")
        except Exception:
            return None
