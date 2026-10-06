from __future__ import annotations

from app.core.runtime import create_task
import asyncio
import datetime as dt
import os
import posixpath
import shlex
import stat
import tempfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Awaitable

from PySide6.QtCore import Qt, QSize, QTimer, QStringListModel
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
    QCompleter, QDialog, QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget, QTabWidget
)

from app.docker.manager import DockerManager
from app.files.highlighter import CodeHighlighter
from app.files.file_types import classify, color_for
from app.ui.dialogs import ErrorDialog


def _fmt_size(size: int | None) -> str:
    if size is None:
        return "—"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return "—"


def _fmt_mtime(value: int | float | None) -> str:
    if not value:
        return "—"
    try:
        return dt.datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


@dataclass
class Entry:
    name: str
    path: str
    is_dir: bool
    size: int | None = None
    mtime: int | float | None = None
    mode: str = ""


class ServerFileBackend:
    def __init__(self, ssh_getter: Callable):
        self.ssh_getter = ssh_getter
        self._sftp = None

    async def list(self, path: str, show_hidden: bool = True) -> list[Entry]:
        if self._sftp is None:
            self._sftp = await self.ssh_getter().sftp()
        sftp = self._sftp
        entries: list[Entry] = []
        async for item in sftp.scandir(path):
            name = str(item.filename)
            if name in (".", ".."):
                continue
            if not show_hidden and name.startswith("."):
                continue
            perms = int(getattr(item.attrs, "permissions", 0) or 0)
            is_lnk = stat.S_ISLNK(perms)
            is_dir = stat.S_ISDIR(perms)
            # For symlinks, try to resolve whether target is a directory
            full = posixpath.join(path, name)
            if is_lnk and not is_dir:
                try:
                    st = await sftp.stat(full)
                    tperms = int(getattr(st, "permissions", 0) or 0)
                    is_dir = stat.S_ISDIR(tperms)
                except Exception:
                    is_dir = False
            entries.append(Entry(
                name=name,
                path=full,
                is_dir=is_dir,
                size=None if is_dir else getattr(item.attrs, "size", None),
                mtime=getattr(item.attrs, "mtime", None),
                mode=("link " if is_lnk else "") + (oct(perms & 0o7777) if perms else ""),
            ))
        return entries

    async def stat(self, path: str):
        if self._sftp is None:
            self._sftp = await self.ssh_getter().sftp()
        return await self._sftp.stat(path)

    async def read_text(self, path: str, limit: int = 2 * 1024 * 1024) -> str:
        if self._sftp is None:
            self._sftp = await self.ssh_getter().sftp()
        sftp = self._sftp
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
            tmp_path = tmp.name
        try:
            await sftp.get(path, tmp_path)
            with open(tmp_path, "rb") as fh:
                data = fh.read(limit)
            return data.decode("utf-8", "replace")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def write_text(self, path: str, content: str) -> None:
        if self._sftp is None:
            self._sftp = await self.ssh_getter().sftp()
        sftp = self._sftp
        data = (content or "").encode("utf-8")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            await sftp.put(tmp_path, path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def download(self, path: str, local: str, progress_handler=None):
        if self._sftp is None:
            self._sftp = await self.ssh_getter().sftp()
        sftp = self._sftp
        try:
            st = await sftp.stat(path)
            perms = int(getattr(st, "permissions", 0) or 0)
            recurse = stat.S_ISDIR(perms)
        except Exception:
            recurse = False
        await sftp.get(
            path,
            local,
            recurse=recurse,
            follow_symlinks=False,
            progress_handler=progress_handler,
        )

    async def upload(self, local: str, remote: str, progress_handler=None):
        if self._sftp is None:
            self._sftp = await self.ssh_getter().sftp()
        await self._sftp.put(
            local,
            remote,
            recurse=os.path.isdir(local),
            follow_symlinks=False,
            progress_handler=progress_handler,
        )

    async def mkdir(self, path: str):
        if self._sftp is None:
            self._sftp = await self.ssh_getter().sftp()
        await self._sftp.mkdir(path)

    async def rename(self, old: str, new: str):
        if self._sftp is None:
            self._sftp = await self.ssh_getter().sftp()
        await self._sftp.rename(old, new)

    async def delete(self, path: str, is_dir: bool):
        if self._sftp is None:
            self._sftp = await self.ssh_getter().sftp()
        sftp = self._sftp
        if is_dir:
            await sftp.rmtree(path)
        else:
            await sftp.remove(path)


    async def close(self):
        """Release the SFTP channel when the explorer is closed."""
        sftp = self._sftp
        self._sftp = None
        if sftp is not None:
            try:
                sftp.exit()
            except Exception:
                pass
            try:
                await sftp.wait_closed()
            except Exception:
                pass

class ContainerFileBackend:

    def __init__(self, ssh, container: str):
        self.ssh = ssh
        self.container = container
        self.dm = DockerManager(ssh)

    async def _exec(self, script: str, args: list[str], timeout: float = 90):
        return await self.dm.run(["exec", self.container, "sh", "-lc", script, "--", *args], timeout=timeout)

    async def list(self, path: str, show_hidden: bool = True) -> list[Entry]:
        path = posixpath.normpath(path or "/")
        # POSIX shell globbing handles normal directories without requiring python/perl/find.
        script = r'''p="$1"; [ -d "$p" ] || exit 2
for e in "$p"/* "$p"/.[!.]* "$p"/..?*; do
  [ -e "$e" ] || continue
  n="$(basename "$e")"
  if [ -d "$e" ]; then t=d; else t=f; fi
  sz="$(stat -c '%s' -- "$e" 2>/dev/null || stat -f '%z' -- "$e" 2>/dev/null || printf '')"
  mt="$(stat -c '%Y' -- "$e" 2>/dev/null || stat -f '%m' -- "$e" 2>/dev/null || printf '')"
  md="$(stat -c '%A' -- "$e" 2>/dev/null || stat -f '%Sp' -- "$e" 2>/dev/null || printf '')"
  printf '%s\t%s\t%s\t%s\t%s\n' "$n" "$t" "$sz" "$mt" "$md"
done'''
        result = await self._exec(script, [path])
        if result.exit_code != 0:
            raise RuntimeError(result.stderr.strip() or f"Unable to list {path}")
        out: list[Entry] = []
        for line in result.stdout.splitlines():
            parts = line.split("\t", 4)
            if len(parts) < 5:
                continue
            name, kind, size, mtime, mode = parts
            if not show_hidden and name.startswith('.'):
                continue
            out.append(Entry(
                name=name,
                path=posixpath.join(path, name),
                is_dir=kind == "d",
                size=int(size) if size.isdigit() else None,
                mtime=int(mtime) if mtime.isdigit() else None,
                mode=mode,
            ))
        return out

    async def read_text(self, path: str, limit: int = 2 * 1024 * 1024) -> str:
        script = 'p="$1"; [ -f "$p" ] || exit 4; head -c ' + str(limit) + ' -- "$p"'
        result = await self._exec(script, [path], timeout=90)
        if result.exit_code != 0:
            raise RuntimeError(result.stderr.strip() or "Unable to open file")
        return result.stdout

    async def write_text(self, path: str, content: str) -> None:
        """Write text into a container file via docker exec + base64-safe heredoc fallback."""
        import base64
        raw = (content or "").encode("utf-8")
        b64 = base64.b64encode(raw).decode("ascii")
        # chunk if huge
        script = 'p="$1"; b64="$2"; echo "$b64" | base64 -d > "$p"'
        result = await self._exec(script, [path, b64], timeout=120)
        if result.exit_code != 0:
            raise RuntimeError(result.stderr.strip() or "Unable to save file")

    async def _remote_tmp(self, suffix: str = "") -> str:
        result = await self.dm.run(["exec", self.container, "sh", "-lc", "mktemp /tmp/sshdock.XXXXXX"], timeout=30)
        if result.exit_code != 0:
            raise RuntimeError(result.stderr.strip() or "Unable to allocate temporary file")
        return result.stdout.strip() + suffix

    async def download(self, path: str, local: str, progress_handler=None):
        if not os.path.basename(path):
            raise RuntimeError("Invalid container path")
        remote_tmp = f"/tmp/sshdock_dl_{os.getpid()}_{abs(hash(path))}"
        try:
            cp = await self.ssh.run(
                f"docker cp {shlex.quote(self.container + ':' + path)} {shlex.quote(remote_tmp)}",
                timeout=180,
            )
            if cp.exit_code != 0:
                raise RuntimeError(cp.stderr or "docker cp failed")
            sftp = await self.ssh.sftp()
            await sftp.get(remote_tmp, local)
        finally:
            try:
                await self.ssh.run(f"rm -f {shlex.quote(remote_tmp)}", timeout=20)
            except Exception:
                pass

    async def upload(self, local: str, remote: str, progress_handler=None):
        remote_tmp = f"/tmp/sshdock_ul_{os.getpid()}_{abs(hash(remote))}"
        try:
            await (await self.ssh.sftp()).put(local, remote_tmp)
            target = f"{self.container}:{remote}"
            cp = await self.ssh.run(
                f"docker cp {shlex.quote(remote_tmp)} {shlex.quote(target)}",
                timeout=180,
            )
            if cp.exit_code != 0:
                raise RuntimeError(cp.stderr or "docker cp failed")
        finally:
            try:
                await self.ssh.run(f"rm -f {shlex.quote(remote_tmp)}", timeout=20)
            except Exception:
                pass

    async def mkdir(self, path: str):
        result = await self._exec('mkdir -p -- "$1"', [path])
        if result.exit_code != 0:
            raise RuntimeError(result.stderr.strip() or "mkdir failed")

    async def rename(self, old: str, new: str):
        result = await self._exec('mv -- "$1" "$2"', [old, new])
        if result.exit_code != 0:
            raise RuntimeError(result.stderr.strip() or "rename failed")

    async def delete(self, path: str, is_dir: bool):
        cmd = 'rm -rf -- "$1"' if is_dir else 'rm -f -- "$1"'
        result = await self._exec(cmd, [path])
        if result.exit_code != 0:
            raise RuntimeError(result.stderr.strip() or "delete failed")


    async def close(self):
        """Container FS uses docker exec — nothing long-lived to close."""
        return

class FileBrowserPane(QWidget):
    """Professional remote explorer used for both host SFTP and Docker filesystems."""

    def __init__(self, backend, title: str = "Files", parent=None):
        super().__init__(parent)
        self.backend = backend
        self.title_text = title
        self.current_path = "/"
        self.entries: list[Entry] = []
        self.loading = False
        self._build()

    def _build(self):
        try:
            import qtawesome as qta
        except ImportError:
            qta = None
        self._qta = qta

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(4)

        self.setObjectName("fileBrowserPane")


        def _icon_btn(tip: str, icon_name: str, fallback: str, *, checkable: bool = False) -> QToolButton:
            b = QToolButton()
            b.setObjectName("feNav")
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAutoRaise(True)
            b.setCheckable(checkable)
            if qta:
                try:
                    theme = str(QApplication.instance().property("sshdockTheme") or "dark").lower()
                    b.setIcon(qta.icon(icon_name, color="#475569" if theme == "light" else "#c5d0de"))
                    b.setProperty("sshdockIconName", icon_name)
                    b.setIconSize(QSize(14, 14))
                    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                except Exception:
                    b.setText(fallback)
            else:
                b.setText(fallback)
            return b

        # Single compact toolbar: nav | path | filter | eye | refresh
        bar = QHBoxLayout()
        bar.setSpacing(2)
        bar.setContentsMargins(0, 0, 0, 0)

        self.back_btn = _icon_btn("Back", "fa5s.arrow-left", "←")
        self.forward_btn = _icon_btn("Forward", "fa5s.arrow-right", "→")
        self.up_btn = _icon_btn("Parent folder", "fa5s.level-up-alt", "↑")
        self.home_btn = _icon_btn("Root /", "fa5s.home", "/")
        for b in (self.back_btn, self.forward_btn, self.up_btn, self.home_btn):
            bar.addWidget(b)

        self.path_edit = QLineEdit("/")
        self.path_edit.setObjectName("fePath")
        self.path_edit.setPlaceholderText("Path")
        bar.addWidget(self.path_edit, 1)
        self._path_model = QStringListModel(self)
        self._path_completer = QCompleter(self._path_model, self)
        self._path_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._path_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._path_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.path_edit.setCompleter(self._path_completer)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("feSearch")
        self.search_edit.setPlaceholderText("Filter")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMaximumWidth(160)
        self.search_edit.setMinimumWidth(100)
        bar.addWidget(self.search_edit)

        self.hidden_btn = _icon_btn("Show hidden files", "fa5s.eye", "·", checkable=True)
        self.hidden_btn.setChecked(True)
        self.refresh_btn = _icon_btn("Refresh", "fa5s.sync-alt", "↻")
        bar.addWidget(self.hidden_btn)
        bar.addWidget(self.refresh_btn)
        root.addLayout(bar)

        # Icon-only actions — one thin row, no fat buttons
        acts = QHBoxLayout()
        acts.setSpacing(0)
        acts.setContentsMargins(0, 0, 0, 0)

        def _act(tip: str, icon_name: str, fallback: str) -> QToolButton:
            b = QToolButton()
            b.setObjectName("feAct")
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAutoRaise(True)
            if qta:
                try:
                    theme = str(QApplication.instance().property("sshdockTheme") or "dark").lower()
                    b.setIcon(qta.icon(icon_name, color="#475569" if theme == "light" else "#c5d0de"))
                    b.setProperty("sshdockIconName", icon_name)
                    b.setIconSize(QSize(14, 14))
                    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                except Exception:
                    b.setText(fallback)
            else:
                b.setText(fallback)
            return b

        self.btn_open = _act("Open", "fa5s.folder-open", "Open")
        self.btn_download = _act("Download", "fa5s.download", "DL")
        self.btn_upload = _act("Upload", "fa5s.upload", "Up")
        self.btn_mkdir = _act("New folder", "fa5s.folder-plus", "Mk")
        self.btn_rename = _act("Rename", "fa5s.pen", "Rn")
        self.btn_delete = _act("Delete", "fa5s.trash", "Del")
        for b in (self.btn_open, self.btn_download, self.btn_upload, self.btn_mkdir, self.btn_rename, self.btn_delete):
            acts.addWidget(b)
        acts.addStretch()
        self.status = QLabel("Ready")
        self.status.setObjectName("feStatus")
        acts.addWidget(self.status)
        root.addLayout(acts)

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("feTable")
        self.table.setHorizontalHeaderLabels(["Name", "Type", "Size", "Modified", "Mode"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        # All columns Interactive so the user can resize every column,
        # including the Name column (previously Stretch, which blocked
        # user dragging).
        self.table.horizontalHeader().setStretchLastSection(False)
        for c in range(self.table.columnCount()):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(40)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 88)
        self.table.setColumnWidth(3, 128)
        self.table.setColumnWidth(4, 72)
        self.table.setIconSize(QSize(16, 16))
        root.addWidget(self.table, 1)

        self.btn_open.clicked.connect(self.open_selected)
        self.btn_download.clicked.connect(self.download_selected)
        self.btn_upload.clicked.connect(self.upload)
        self.btn_mkdir.clicked.connect(self.new_folder)
        self.btn_rename.clicked.connect(self.rename_selected)
        self.btn_delete.clicked.connect(self.delete_selected)

        self.path_edit.returnPressed.connect(self._goto_path)
        self.refresh_btn.clicked.connect(self.refresh)
        self.up_btn.clicked.connect(self.go_up)
        self.home_btn.clicked.connect(lambda: self._set_path("/"))
        self.hidden_btn.toggled.connect(self.refresh)
        self.search_edit.textChanged.connect(self._apply_filter)
        self.back_btn.clicked.connect(self.go_back)
        self.forward_btn.clicked.connect(self.go_forward)
        self.table.cellDoubleClicked.connect(lambda row, _c: self._spawn(self._double_click(row)))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.itemSelectionChanged.connect(self._selection_changed)

        self._back_stack: list[str] = []
        self._forward_stack: list[str] = []

    def apply_theme(self, theme: str | None = None) -> None:
        name = str(theme or QApplication.instance().property("sshdockTheme") or "dark").lower()
        for button in self.findChildren(QToolButton):
            icon_name = button.property("sshdockIconName")
            if self._qta is not None and icon_name:
                try:
                    button.setIcon(self._qta.icon(str(icon_name), color="#475569" if name == "light" else "#c5d0de"))
                except Exception:
                    pass
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _set_path(self, path: str, push_history: bool = True):
        path = posixpath.normpath(path or "/")
        if not path.startswith("/"):
            path = "/" + path
        if push_history and path != self.current_path:
            self._back_stack.append(self.current_path)
            self._forward_stack.clear()
        self.current_path = path
        self.path_edit.setText(path)
        self._notify_path_changed()

    def refresh(self):
        if self.loading:
            return
        self._spawn(self._refresh_async())

    async def _refresh_async(self):
        self.loading = True; self.refresh_btn.setEnabled(False); self.status.setText("Loading…")
        try:
            entries = await self.backend.list(self.current_path, self.hidden_btn.isChecked())
            self.entries = sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))
            self._render()
            self.status.setText(f"{len(self.entries)} items")
        except Exception as exc:
            message = str(exc)
            if "SSH connection is not established" in message:
                self.status.setText("Waiting for SSH connection…")
            else:
                self.status.setText(f"Load failed: {message[:180]}")
            # Never open a modal Qt dialog from an async refresh task. A modal
            # nested event loop can re-enter qasync while another coroutine is
            # executing and can stop the main event loop unexpectedly.
        finally:
            self.loading = False; self.refresh_btn.setEnabled(True)

    def _render(self):
        query = self.search_edit.text().strip().lower()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        qta = getattr(self, "_qta", None)
        for ent in self.entries:
            if query and query not in ent.name.lower():
                continue
            r = self.table.rowCount()
            self.table.insertRow(r)
            is_link = (ent.mode or "").startswith("link")
            theme = str(QApplication.instance().property("sshdockTheme") or "dark").lower()
            ft = classify(ent.name, is_dir=ent.is_dir, is_symlink=is_link)
            kind = ("Folder" if ent.is_dir else "File") + (" link" if is_link else "")
            icon_name = ft.icon
            color = color_for(ft.key, theme)
            name = QTableWidgetItem(ent.name)
            name.setData(Qt.ItemDataRole.UserRole, ent)
            name.setToolTip(ent.path)
            if qta:
                try:
                    name.setIcon(qta.icon(icon_name, color=color))
                except Exception:
                    pass
            self.table.setItem(r, 0, name)
            kind_item = QTableWidgetItem(kind)
            kind_item.setForeground(Qt.GlobalColor.gray)
            self.table.setItem(r, 1, kind_item)
            size_item = QTableWidgetItem("—" if ent.is_dir else _fmt_size(ent.size))
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(r, 2, size_item)
            self.table.setItem(r, 3, QTableWidgetItem(_fmt_mtime(ent.mtime)))
            mode_item = QTableWidgetItem(ent.mode or "—")
            mode_item.setForeground(Qt.GlobalColor.gray)
            self.table.setItem(r, 4, mode_item)
        self.table.setSortingEnabled(True)
        self._update_path_completer()

    def _update_path_completer(self):
        """Suggest current directory entries and full paths while typing in the address bar."""
        if not hasattr(self, "_path_model"):
            return
        names = [e.name for e in self.entries]
        full = [e.path for e in self.entries]
        # Also suggest joining typed prefix under current path
        base = self.current_path.rstrip("/") or ""
        joined = [f"{base}/{n}" if base else f"/{n}" for n in names]
        suggestions = sorted(set(names + full + joined + [self.current_path]))
        self._path_model.setStringList(suggestions)

    def _notify_path_changed(self):
        parent = self.parent()
        while parent is not None and not isinstance(parent, RemoteFileBrowser):
            parent = parent.parent()
        if isinstance(parent, RemoteFileBrowser):
            parent._sync_title()

    def set_status(self, message: str, clear_after_ms: int = 4500):
        self.status.setText(message)
        if clear_after_ms > 0:
            QTimer.singleShot(clear_after_ms, lambda: self._clear_status_if(message))

    def _clear_status_if(self, expected: str):
        if self.status.text() == expected:
            n = len(self.entries)
            sel = len(self._selected_entries())
            self.status.setText(f"{sel} selected" if sel else f"{n} items")

    def _apply_filter(self):
        self._render()

    async def _double_click(self, row: int):
        item = self.table.item(row, 0)
        if not item: return
        ent: Entry = item.data(Qt.ItemDataRole.UserRole)
        if ent.is_dir:
            self._set_path(ent.path)
            await self._refresh_async()
        else:
            await self._preview(ent)

    def open_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows: return
        self._spawn(self._open_selected_async(rows[0].row()))

    async def _open_selected_async(self, row: int):
        item = self.table.item(row, 0)
        if item:
            ent: Entry = item.data(Qt.ItemDataRole.UserRole)
            if ent.is_dir:
                self._set_path(ent.path); await self._refresh_async()
            else:
                await self._preview(ent)

    async def _preview(self, ent: Entry):
        self.status.setText(f"Opening {ent.name}…")
        try:
            content = await self.backend.read_text(ent.path)
            sample = content[:4096]
            # Binary if replacement-heavy after strict UTF-8 decode of raw-ish content
            # or classic NUL detection via codepoints
            nul = any(ord(ch) == 0 for ch in sample)
            if nul:
                self.status.setText(f"{ent.name}: binary file")
                ErrorDialog.show_error(
                    self,
                    "Binary file",
                    f"«{ent.name}» contains binary data and cannot be shown as text.",
                    "Use Download from the toolbar or context menu.",
                )
                return
            parent = self.parent()
            while parent is not None and not isinstance(parent, RemoteFileBrowser):
                parent = parent.parent()
            if isinstance(parent, RemoteFileBrowser):
                parent.open_file_tab(ent.path, ent.name, content)
                self.set_status(f"Opened {ent.name}")
            else:
                dlg = QDialog(self)
                dlg.setWindowTitle(ent.name)
                dlg.resize(800, 560)
                lay = QVBoxLayout(dlg)
                view = QPlainTextEdit()
                view.setReadOnly(True)
                view.setPlainText(content)
                lang = CodeHighlighter.language_for_path(ent.path)
                CodeHighlighter(view.document(), lang, path=ent.path)
                lay.addWidget(view)
                dlg.exec()
                self.set_status(f"Opened {ent.name}")
        except Exception as exc:
            self.status.setText(f"Open failed: {str(exc)[:120]}")
            ErrorDialog.show_error(
                self,
                "Open file",
                f"Could not open «{ent.name}».",
                str(exc),
            )

    def _selected_entries(self) -> list[Entry]:
        out=[]
        for idx in self.table.selectionModel().selectedRows():
            item=self.table.item(idx.row(),0)
            if item:
                ent=item.data(Qt.ItemDataRole.UserRole)
                if ent: out.append(ent)
        return out

    def download_selected(self):
        selected = self._selected_entries()
        entries = [e for e in selected if not e.is_dir]
        if not entries:
            if selected:
                QMessageBox.information(self, "Download", "Select a file (not a folder) to download.")
            return
        if len(entries) == 1:
            suggested = entries[0].name.replace(" →", "").strip() or "download.bin"
            local, _ = QFileDialog.getSaveFileName(self, "Download", suggested)
            if local:
                self._spawn(self._download(entries[0], local))
            return
        # Multi-file: pick a destination directory
        dest = QFileDialog.getExistingDirectory(self, "Download files to folder")
        if not dest:
            return
        self._spawn(self._download_many(entries, dest))

    async def _download_many(self, entries: list, dest_dir: str):
        ok = 0
        errors = []
        for ent in entries:
            local = os.path.join(dest_dir, ent.name.replace(" →", "").strip() or "download.bin")
            try:
                self.status.setText(f"Downloading {ent.name}…")
                await self.backend.download(ent.path, local)
                ok += 1
            except Exception as exc:
                errors.append(f"{ent.name}: {exc}")
        if errors:
            ErrorDialog.show_error(self, "Download", f"{ok} ok, {len(errors)} failed.", "\n".join(errors))
            self.set_status(f"Downloaded {ok}, {len(errors)} failed", 6000)
        else:
            self.set_status(f"Downloaded {ok} file(s)")

    async def _download(self, ent: Entry, local: str):
        try:
            self.status.setText("Downloading…"); await self.backend.download(ent.path, local); self.set_status(f"Downloaded {ent.name}")
        except Exception as exc: ErrorDialog.show_error(self, "Download", str(exc)); self.set_status("Download failed", 6000)

    def upload(self):
        local,_=QFileDialog.getOpenFileName(self, "Upload file")
        if not local: return
        remote=posixpath.join(self.current_path, os.path.basename(local)); self._spawn(self._upload(local, remote))

    async def _upload(self, local: str, remote: str):
        try:
            self.status.setText("Uploading…"); await self.backend.upload(local, remote); await self._refresh_async(); self.set_status("Upload complete")
        except Exception as exc: ErrorDialog.show_error(self, "Upload", str(exc)); self.set_status("Upload failed", 6000)

    def new_folder(self):
        name,ok=QInputDialog.getText(self,"New folder","Folder name:")
        if ok and name.strip(): self._spawn(self._mkdir(posixpath.join(self.current_path,name.strip())))

    async def _mkdir(self,path):
        try: await self.backend.mkdir(path); await self._refresh_async()
        except Exception as exc: ErrorDialog.show_error(self, "New folder", str(exc))

    def rename_selected(self):
        rows=self._selected_entries()
        if len(rows)!=1:return
        name,ok=QInputDialog.getText(self,"Rename","New name:",text=rows[0].name)
        if ok and name.strip(): self._spawn(self._rename(rows[0],name.strip()))

    async def _rename(self,ent,new_name):
        try: await self.backend.rename(ent.path,posixpath.join(self.current_path,new_name)); await self._refresh_async()
        except Exception as exc: ErrorDialog.show_error(self, "Rename", str(exc))

    def delete_selected(self):
        rows=self._selected_entries()
        if not rows:return
        if QMessageBox.question(self,"Delete",f"Delete {len(rows)} selected item(s)?") != QMessageBox.StandardButton.Yes:return
        self._spawn(self._delete(rows))

    async def _delete(self, rows):
        try:
            for ent in rows: await self.backend.delete(ent.path,ent.is_dir)
            await self._refresh_async()
        except Exception as exc: ErrorDialog.show_error(self, "Delete", str(exc))

    def go_up(self):
        self._set_path(posixpath.dirname(self.current_path.rstrip('/')) or '/'); self.refresh()

    def go_back(self):
        if not self._back_stack:return
        self._forward_stack.append(self.current_path); self._set_path(self._back_stack.pop(),False); self.refresh()

    def go_forward(self):
        if not self._forward_stack:return
        self._back_stack.append(self.current_path); self._set_path(self._forward_stack.pop(),False); self.refresh()

    def _goto_path(self):
        self._set_path(self.path_edit.text().strip() or '/'); self.refresh()

    def _selection_changed(self):
        count=len(self._selected_entries())
        self.status.setText(f"{count} selected" if count else f"{len(self.entries)} items")

    def _context_menu(self, pos):
        item = self.table.itemAt(pos)
        if item is not None:
            # Select the row under the cursor so actions target the right entry
            self.table.selectRow(item.row())
        entries = self._selected_entries()
        menu = QMenu(self)
        open_a = menu.addAction("Open")
        open_a.setEnabled(bool(entries))
        dl = menu.addAction("Download…")
        dl.setEnabled(bool(entries) and any(not e.is_dir for e in entries))
        menu.addSeparator()
        upload_a = menu.addAction("Upload here…")
        new_folder_a = menu.addAction("New folder…")
        rename_a = menu.addAction("Rename…")
        rename_a.setEnabled(len(entries) == 1)
        delete_a = menu.addAction("Delete…")
        delete_a.setEnabled(bool(entries))
        menu.addSeparator()
        copy_name = menu.addAction("Copy name")
        copy_path = menu.addAction("Copy path")
        copy_name.setEnabled(len(entries) == 1)
        copy_path.setEnabled(len(entries) == 1)
        menu.addSeparator()
        refresh_a = menu.addAction("Refresh")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is open_a:
            self.open_selected()
        elif chosen is dl:
            self.download_selected()
        elif chosen is upload_a:
            self.upload()
        elif chosen is new_folder_a:
            self.new_folder()
        elif chosen is rename_a:
            self.rename_selected()
        elif chosen is delete_a:
            self.delete_selected()
        elif chosen is copy_name and entries:
            QApplication.clipboard().setText(entries[0].name)
        elif chosen is copy_path and entries:
            QApplication.clipboard().setText(entries[0].path)
        elif chosen is refresh_a:
            self.refresh()

    def _spawn(self, coro):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = create_task(coro)
        active = getattr(self, "_active_tasks", set())
        self._active_tasks = active
        active.add(task)

        def _done(t):
            active.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                self.status.setText(f"Error: {str(exc)[:160]}")

        task.add_done_callback(_done)




class FileViewerPage(QWidget):
    """File tab: view / edit / save, compact chrome matching the explorer."""

    def __init__(self, path: str, name: str, content: str, backend=None, parent=None, spawn=None):
        super().__init__(parent)
        self.file_path = path
        self.file_name = name
        self.backend = backend
        self._spawn = spawn
        self._dirty = False
        self._original = content if content is not None else ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 4)
        lay.setSpacing(2)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(2)

        self.path_lbl = QLabel(path)
        self.path_lbl.setObjectName("fileViewerPath")
        self.path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_lbl.setToolTip(path)
        bar.addWidget(self.path_lbl, 1)

        self.meta_lbl = QLabel("")
        self.meta_lbl.setObjectName("fileViewerMeta")
        bar.addWidget(self.meta_lbl)

        try:
            import qtawesome as qta
        except ImportError:
            qta = None

        def _tb(tip: str, icon_name: str, text_fb: str) -> QToolButton:
            b = QToolButton()
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if qta:
                try:
                    theme = str(QApplication.instance().property("sshdockTheme") or "dark").lower()
                    b.setIcon(qta.icon(icon_name, color="#475569" if theme == "light" else "#c5d0de"))
                    b.setProperty("sshdockIconName", icon_name)
                    b.setIconSize(QSize(14, 14))
                    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                except Exception:
                    b.setText(text_fb)
            else:
                b.setText(text_fb)
            return b

        self.edit_btn = _tb("Toggle edit mode", "fa5s.pen", "Edit")
        self.edit_btn.setCheckable(True)
        self.save_btn = _tb("Save file", "fa5s.save", "Save")
        self.save_btn.setEnabled(False)
        self.copy_sel_btn = _tb("Copy selection", "fa5s.i-cursor", "Sel")
        self.copy_btn = _tb("Copy all", "fa5s.copy", "Copy")
        for b in (self.edit_btn, self.save_btn, self.copy_sel_btn, self.copy_btn):
            bar.addWidget(b)
        lay.addLayout(bar)

        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.viewer.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.viewer.setObjectName("fileViewerEditor")
        mono = self.viewer.font()
        mono.setFamily("Consolas")
        mono.setStyleHint(mono.StyleHint.Monospace)
        mono.setPointSize(11)
        self.viewer.setFont(mono)
        self.viewer.setPlainText(self._original)
        lay.addWidget(self.viewer, 1)

        self._update_meta()
        self._hl = None
        chars = len(self._original)
        lang = CodeHighlighter.language_for_path(path)
        if chars <= 400_000:
            try:
                self._hl = CodeHighlighter(self.viewer.document(), lang, path=path)
            except Exception:
                self._hl = None

        self.copy_btn.clicked.connect(self._copy_all)
        self.copy_sel_btn.clicked.connect(self._copy_sel)
        self.edit_btn.toggled.connect(self._toggle_edit)
        self.save_btn.clicked.connect(self._save)
        self.viewer.textChanged.connect(self._on_text_changed)

    def _update_meta(self, extra: str = "") -> None:
        lines = self.viewer.document().blockCount()
        chars = len(self.viewer.toPlainText())
        lang = CodeHighlighter.language_for_path(self.file_path)
        dirty = " · modified" if self._dirty else ""
        mode = " · editing" if not self.viewer.isReadOnly() else ""
        base = f"{lang}  ·  {lines} lines  ·  {chars:,} chars{mode}{dirty}"
        self.meta_lbl.setText(extra or base)

    def _on_text_changed(self) -> None:
        if self.viewer.isReadOnly():
            return
        self._dirty = self.viewer.toPlainText() != self._original
        self.save_btn.setEnabled(self._dirty and self.backend is not None)
        self._update_meta()

    def _toggle_edit(self, on: bool) -> None:
        self.viewer.setReadOnly(not on)
        if on:
            self.viewer.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextEditorInteraction
            )
        else:
            self.viewer.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
        self._update_meta()

    def _save(self) -> None:
        if self.backend is None or not hasattr(self.backend, "write_text"):
            self._update_meta("Save not available")
            QTimer.singleShot(1200, self._update_meta)
            return
        content = self.viewer.toPlainText()

        async def _do():
            try:
                await self.backend.write_text(self.file_path, content)
                self._original = content
                self._dirty = False
                self.save_btn.setEnabled(False)
                self._update_meta("Saved")
                QTimer.singleShot(1200, self._update_meta)
            except Exception as exc:
                self._update_meta(f"Save failed: {exc}")
                ErrorDialog.show_error(self, "Save file", f"Could not save «{self.file_name}».", str(exc))

        if self._spawn:
            self._spawn(_do())
        else:
            try:
                loop = asyncio.get_running_loop()
                create_task(_do())
            except Exception as exc:
                ErrorDialog.show_error(self, "Save file", str(exc), "")

    def _copy_all(self) -> None:
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(self.viewer.toPlainText())
        self._update_meta("Copied all")
        QTimer.singleShot(1000, self._update_meta)

    def _copy_sel(self) -> None:
        from PySide6.QtGui import QGuiApplication
        sel = self.viewer.textCursor().selectedText().replace("\u2029", "\n")
        if not sel:
            self._update_meta("Select text first")
            QTimer.singleShot(1000, self._update_meta)
            return
        QGuiApplication.clipboard().setText(sel)
        self._update_meta("Copied selection")
        QTimer.singleShot(1000, self._update_meta)



class RemoteFileBrowser(QWidget):
    """Tabbed file-browser workspace for host or container filesystems."""

    def __init__(self, backend, title: str = "Files", parent=None):
        super().__init__(parent)
        self.backend = backend
        try:
            import qtawesome as qta
        except ImportError:
            qta = None
        self._qta = qta
        self.title_text = title
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(lambda _: self._sync_title())

        self.tabs.setObjectName("fileBrowserTabs")


        # (+) in a dedicated column BESIDE the tab widget — never on top of tabs
        new_tab = QToolButton()
        new_tab.setObjectName("feNewTab")
        new_tab.setToolTip("New tab")
        new_tab.setCursor(Qt.CursorShape.PointingHandCursor)
        new_tab.setAutoRaise(True)
        new_tab.setFixedSize(26, 26)
        new_tab.setProperty("sshdockIconName", "fa5s.plus")
        try:
            import qtawesome as qta
            theme = str(QApplication.instance().property("sshdockTheme") or "dark").lower()
            new_tab.setIcon(qta.icon("fa5s.plus", color="#475569" if theme == "light" else "#c5d0de"))
            new_tab.setIconSize(QSize(12, 12))
            new_tab.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            new_tab.setText("")
        except Exception:
            new_tab.setText("+")
        new_tab.clicked.connect(lambda: self.new_tab())
        self._new_tab_btn = new_tab

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.tabs, 1)

        # Align (+) with the tab bar row (top), leave content area free
        side = QVBoxLayout()
        side.setContentsMargins(4, 4, 6, 0)  # top inset matches tab padding
        side.setSpacing(0)
        side.addWidget(new_tab, 0, Qt.AlignmentFlag.AlignTop)
        side.addStretch(1)
        row.addLayout(side)

        root.addLayout(row, 1)
        self.new_tab()



    def new_tab(self, path: str = "/"):
        pane = FileBrowserPane(self.backend, self.title_text)
        pane._set_path(path, False)
        self.tabs.addTab(pane, path if path != "/" else "/")
        self.tabs.setCurrentWidget(pane)
        self._spawn(pane._refresh_async())
        return pane

    def apply_theme(self, theme: str | None = None) -> None:
        name = str(theme or QApplication.instance().property("sshdockTheme") or "dark").lower()
        color = "#475569" if name == "light" else "#c5d0de"
        if self._qta is not None:
            for button in self.findChildren(QToolButton):
                icon_name = button.property("sshdockIconName")
                if icon_name:
                    try:
                        button.setIcon(self._qta.icon(str(icon_name), color=color))
                    except Exception:
                        pass
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if hasattr(widget, "apply_theme"):
                widget.apply_theme(name)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _close_tab(self, index: int):
        widget = self.tabs.widget(index)
        # Allow closing file-viewer tabs freely; keep at least one browser pane
        is_pane = isinstance(widget, FileBrowserPane)
        if is_pane and sum(1 for i in range(self.tabs.count()) if isinstance(self.tabs.widget(i), FileBrowserPane)) <= 1:
            return
        self.tabs.removeTab(index)
        if widget is not None:
            widget.deleteLater()

    def closeEvent(self, event):
        # Release shared SFTP when the whole explorer is destroyed
        try:
            loop = asyncio.get_running_loop()
            if hasattr(self.backend, "close"):
                create_task(self.backend.close())
        except Exception:
            pass
        super().closeEvent(event)

    def _sync_title(self):
        pane = self.current_pane()
        if not pane:
            return
        path = pane.current_path or "/"
        # Tab title: last path segment (or /)
        if path in ("", "/"):
            title = "/"
        else:
            title = path.rstrip("/").rsplit("/", 1)[-1] or "/"
        idx = self.tabs.currentIndex()
        if idx >= 0:
            self.tabs.setTabText(idx, title)
            self.tabs.setTabToolTip(idx, path)

    def current_pane(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, FileBrowserPane) else None

    def refresh(self):
        pane = self.current_pane()
        if pane:
            pane.refresh()

    async def _refresh_async(self):
        pane = self.current_pane()
        if pane:
            await pane._refresh_async()
            self._sync_title()

    def open_file_tab(self, path: str, name: str, content: str):
        """Open (or focus) a file viewer tab — compact, stable layout."""
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, FileViewerPage) and getattr(w, "file_path", None) == path:
                self.tabs.setCurrentIndex(i)
                return

        page = FileViewerPage(
            path, name, content,
            backend=getattr(self, "backend", None),
            parent=self,
            spawn=self._spawn,
        )
        idx = self.tabs.addTab(page, name)
        try:
            import qtawesome as qta
            lang = CodeHighlighter.language_for_path(path)
            icon_map = {
                "python": "fa5b.python",
                "json": "fa5s.code",
                "yaml": "fa5s.code",
                "shell": "fa5s.terminal",
                "markdown": "fa5s.book",
                "js": "fa5b.js",
                "log": "fa5s.file-alt",
            }
            self.tabs.setTabIcon(idx, qta.icon(icon_map.get(lang, "fa5s.file"), color="#9fb0c3"))
        except Exception:
            pass
        self.tabs.setTabToolTip(idx, path)
        self.tabs.setCurrentIndex(idx)


    def _spawn(self, coro):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = create_task(coro)
        active = getattr(self, "_active_tasks", set())
        self._active_tasks = active
        active.add(task)
        def done(t):
            active.discard(t)
            if not t.cancelled():
                try:
                    t.result()
                except Exception:
                    pass
        task.add_done_callback(done)
