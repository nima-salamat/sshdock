from __future__ import annotations

import os
import posixpath
from typing import Callable

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QMenu, QPlainTextEdit,
    QFileDialog,
    QPushButton, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from app.files.browser import Entry, ServerFileBackend
from app.files.highlighter import CodeHighlighter


class _EditorWorkspace(QWidget):
    """Independent non-modal multi-tab editor (VSCode-style)."""

    def __init__(self, backend: ServerFileBackend, parent=None):
        super().__init__(None)  # true top-level window
        self.backend = backend
        self.setWindowTitle("Editor")
        self.resize(1000, 680)
        self.setMinimumSize(480, 320)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinMaxButtonsHint
        )
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._tabs: dict[str, tuple[QWidget, QPlainTextEdit, QLabel]] = {}

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

    def open_file(self, path: str, content: str, *, writable: bool = True):
        if path in self._tabs:
            widget, _view, _st = self._tabs[path]
            idx = self.tabs.indexOf(widget)
            if idx >= 0:
                self.tabs.setCurrentIndex(idx)
            self.show()
            self.raise_()
            return

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
        view.setPlainText(content[: 2 * 1024 * 1024])
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

        name = posixpath.basename(path) or path
        idx = self.tabs.addTab(page, name)
        self.tabs.setTabToolTip(idx, path)
        self.tabs.setCurrentIndex(idx)
        self._tabs[path] = (page, view, status)

        if save_btn is not None:
            def _save(p=path, v=view, st=status):
                st.setText("Saving…")
                text = v.toPlainText()

                async def _do():
                    try:
                        await self.backend.write_text(p, text)
                        st.setText("Saved")
                        self.global_status.setText(f"Saved {posixpath.basename(p)}")
                    except Exception as exc:
                        st.setText(f"Save failed: {exc}")
                        self.global_status.setText(f"Save failed: {str(exc)[:100]}")

                try:
                    from app.core.runtime import create_task
                    create_task(_do(), name="editor-save")
                except Exception:
                    pass

            save_btn.clicked.connect(_save)

        self.show()
        self.raise_()
        view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _close_tab(self, index: int):
        widget = self.tabs.widget(index)
        if widget is None:
            return
        path = None
        for p, (w, _v, _s) in list(self._tabs.items()):
            if w is widget:
                path = p
                break
        self.tabs.removeTab(index)
        if path is not None:
            self._tabs.pop(path, None)
        if self.tabs.count() == 0:
            self.hide()

    def closeEvent(self, event):
        event.ignore()
        self.hide()


class TerminalFileExplorer(QWidget):
    """Compact remote filesystem explorer coupled to one interactive SSH shell."""

    closed_requested = Signal()

    def __init__(self, backend: ServerFileBackend, change_dir: Callable[[str], None], parent=None):
        super().__init__(parent)
        self.backend = backend
        self.change_dir = change_dir
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
        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")
        header.addWidget(self.count_label)
        header.addStretch()
        self.up_btn = self._button("fa5s.level-up-alt", "Parent directory", self.go_up)
        self.refresh_btn = self._button("fa5s.sync-alt", "Refresh", self.refresh)
        self.upload_btn = self._button("fa5s.upload", "Upload files here", self.upload_files)
        self.download_btn = self._button("fa5s.download", "Download selected files", self.download_selected)
        self.close_btn = self._button("fa5s.times", "Close explorer", self.closed_requested.emit)
        header.addWidget(self.up_btn)
        header.addWidget(self.refresh_btn)
        header.addWidget(self.upload_btn)
        header.addWidget(self.download_btn)
        header.addWidget(self.close_btn)
        root.addLayout(header)

        self.path_edit = QLineEdit()
        self.path_edit.setObjectName("terminalExplorerPath")
        self.path_edit.setPlaceholderText("Current shell directory")
        self.path_edit.returnPressed.connect(self._go_path_from_edit)
        root.addWidget(self.path_edit)

        self.search = QLineEdit()
        self.search.setObjectName("terminalExplorerSearch")
        self.search.setPlaceholderText("Filter files…")
        self.search.textChanged.connect(self._filter)
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

        self.status = QLabel("Waiting for shell directory…")
        self.status.setObjectName("terminalExplorerStatus")
        root.addWidget(self.status)

        self.setStyleSheet(
            """
            QWidget#terminalExplorer {
                background: #0c1118; border-right: 1px solid #232b35;
            }
            QLabel#terminalExplorerTitle {
                color: #dbe5f2; font-size: 11px; font-weight: 700; letter-spacing: 1px;
            }
            QLineEdit#terminalExplorerPath, QLineEdit {
                background: #090e14; border: 1px solid #252e39; border-radius: 5px;
                padding: 4px 7px; color: #dfe7f0; selection-background-color: #275e9e;
            }
            QTreeWidget#terminalExplorerTree {
                background: transparent; border: none; outline: none;
                color: #d9e1eb; font-size: 12px;
            }
            QTreeWidget#terminalExplorerTree::item { height: 24px; padding: 1px 0; }
            QTreeWidget#terminalExplorerTree::item:hover { background: #17202b; }
            QTreeWidget#terminalExplorerTree::item:selected { background: #1d3857; }
            """
        )

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
        b.clicked.connect(lambda _=False: callback())
        return b

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

    async def upload_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Upload files")
        if not files:
            return
        ok = 0
        for local in files:
            try:
                name = os.path.basename(local)
                await self.backend.upload(local, posixpath.join(self.current_path, name))
                ok += 1
            except Exception as exc:
                self.status.setText(f"Upload failed: {str(exc)[:100]}")
                break
        self.status.setText(f"Uploaded {ok}/{len(files)} file(s) to {self.current_path}")
        self._show_path()

    async def download_selected(self):
        items = self.tree.selectedItems()
        entries = []
        for item in items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, Entry):
                entries.append(data)
        if not entries:
            self.status.setText("Select one or more files to download.")
            return
        dirs = [e for e in entries if e.is_dir]
        if dirs:
            self.status.setText("Folder download will be added to the transfer manager; select files for now.")
        entries = [e for e in entries if not e.is_dir]
        if not entries:
            return
        target = QFileDialog.getExistingDirectory(self, "Download to")
        if not target:
            return
        ok = 0
        for ent in entries:
            try:
                await self.backend.download(ent.path, os.path.join(target, ent.name))
                ok += 1
            except Exception as exc:
                self.status.setText(f"Download failed: {str(exc)[:100]}")
                break
        self.status.setText(f"Downloaded {ok}/{len(entries)} file(s)")

    def _normalize(self, path: str) -> str:
        path = posixpath.normpath(path or "/")
        return path if path.startswith("/") else "/" + path

    def set_cwd(self, path: str, *, refresh: bool = True):
        normalized = self._normalize(path)
        changed = normalized != self.current_path
        self.current_path = normalized
        self.path_edit.setText(normalized)
        if changed or refresh:
            self._show_path()

    def _show_path(self):
        self.tree.clear()
        self._loaded_paths.clear()
        self._items_by_path.clear()
        self._loading_paths.clear()
        self.status.setText("Loading…")
        self.count_label.setText("")
        root = self._make_dir_item(self.current_path, display=self.current_path)
        self.tree.addTopLevelItem(root)
        root.setExpanded(True)
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
            for ent in entries:
                if ent.is_dir:
                    child = self._make_dir_item(ent.path, display=ent.name)
                    item.addChild(child)
                else:
                    child = QTreeWidgetItem([ent.name])
                    child.setData(0, Qt.ItemDataRole.UserRole, ent)
                    child.setToolTip(0, ent.path)
                    child.setChildIndicatorPolicy(
                        QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator
                    )
                    item.addChild(child)
                visible += 1
            self._loaded_paths.add(path)
            if path == self.current_path:
                self.count_label.setText(f"{visible} items")
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
        elif isinstance(data, dict) and data.get("dir"):
            path = data.get("path") or self.current_path
            if path != self.current_path:
                self.change_dir(path)

    def _go_path_from_edit(self):
        self.change_dir(self._normalize(self.path_edit.text()))

    def go_up(self):
        target = posixpath.dirname(self.current_path.rstrip("/")) or "/"
        self.change_dir(target)

    def refresh(self):
        self._show_path()

    def _filter(self):
        query = self.search.text().strip().lower()
        root = self.tree.topLevelItem(0)
        if root is None:
            return

        def walk(node: QTreeWidgetItem) -> bool:
            any_child = False
            for i in range(node.childCount()):
                child = node.child(i)
                data = child.data(0, Qt.ItemDataRole.UserRole)
                name = (child.text(0) or "").strip().lower()
                match = not query or query in name
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

    async def _open_file(self, ent: Entry):
        self.status.setText(f"Opening {ent.name}…")
        try:
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
            if data.is_dir:
                cd_action = menu.addAction("cd in terminal")
                download_action = None
            else:
                cd_action = None
                download_action = menu.addAction("Download")
            menu.addSeparator()
            copy_action = menu.addAction("Copy path")
            upload_action = menu.addAction("Upload here")
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
            elif chosen is upload_action:
                self._spawn(self.upload_files())
        elif isinstance(data, dict) and data.get("dir"):
            cd_action = menu.addAction("cd in terminal")
            copy_action = menu.addAction("Copy path")
            upload_action = menu.addAction("Upload here")
            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is cd_action:
                self.change_dir(data["path"])
            elif chosen is copy_action:
                from PySide6.QtWidgets import QApplication
                QApplication.clipboard().setText(data["path"])
            elif chosen is upload_action:
                self._spawn(self.upload_files())

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
