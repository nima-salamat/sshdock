"""Compose projects panel — extracted from MainWindow for maintainability."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Awaitable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget, QApplication, QStyle,
)

from app.docker.manager import DockerManager
from app.storage.settings import SettingsStore
from app.ui.notifications import NotificationBus
from app.ui.widgets import icon_button, AutoRefreshControl, TableAutoRefresh
from app.ui.job_tabs import MultiTabJobDialog

try:
    import qtawesome as qta
except ImportError:
    qta = None


class ComposePanel:
    """Owns the Compose page widgets and action handlers."""

    def __init__(
        self,
        *,
        get_server_id: Callable[[], str | None],
        get_ssh: Callable[[], Any],
        store: SettingsStore,
        spawn: Callable[[Awaitable], Any],
        parent: QWidget,
    ) -> None:
        self.get_server_id = get_server_id
        self.get_ssh = get_ssh
        self.store = store
        self.spawn = spawn
        self.parent = parent
        self._compose_rows: list[dict[str, Any]] = []
        self._log_proc = None
        self._auto_refresh = TableAutoRefresh(parent)
        self._auto_refresh_control = AutoRefreshControl(5)
        self.page = self._build_page()
        def refresh_tick():
            return self.spawn(self.load())
        self._auto_refresh.configure(
            self._auto_refresh_control.seconds,
            refresh_tick,
            allowed=lambda: bool(self.get_server_id()) and self.page.isVisible(),
        )
        self._auto_refresh_control.intervalChanged.connect(
            lambda seconds: self._auto_refresh.configure(
                seconds,
                refresh_tick,
                allowed=lambda: bool(self.get_server_id()) and self.page.isVisible(),
            )
        )

    # ── UI construction ───────────────────────────────────────
    def _build_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        page._layout_owner = lay  # type: ignore[attr-defined]
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(12)

        row = QHBoxLayout()
        title = QLabel("Compose projects")
        title.setObjectName("heroTitle")
        row.addWidget(title)
        row.addStretch()
        row.addWidget(self._auto_refresh_control)
        add_btn = icon_button(
            "Add project", "fa5s.plus", self.add_project_dialog,
            "Register a compose file / project so it stays visible even when not running",
        )
        row.addWidget(add_btn)
        refresh = icon_button("Refresh", "fa5s.sync-alt", lambda _=False: self.spawn(self.load()), "Refresh")
        row.addWidget(refresh)
        lay.addLayout(row)

        # Options row: env-file + env vars for up/start
        opts = QHBoxLayout()
        opts.setSpacing(8)
        self.env_file_edit = QLineEdit()
        self.env_file_edit.setPlaceholderText("--env-file path (optional, remote path)")
        self.env_file_edit.setMinimumWidth(200)
        self.profile_edit = QLineEdit()
        self.profile_edit.setPlaceholderText("--profile name (optional)")
        self.profile_edit.setMaximumWidth(160)
        opts.addWidget(QLabel("Env file"))
        opts.addWidget(self.env_file_edit, 1)
        opts.addWidget(QLabel("Profile"))
        opts.addWidget(self.profile_edit)
        self.env_vars_edit = QLineEdit()
        self.env_vars_edit.setPlaceholderText("KEY=val KEY2=val  (optional, space-separated)")
        opts.addWidget(QLabel("Env"))
        opts.addWidget(self.env_vars_edit, 1)
        lay.addLayout(opts)

        toolbar = QHBoxLayout()
        self.selected_label = QLabel("0 selected")
        self.selected_label.setObjectName("metricHeading")
        toolbar.addWidget(self.selected_label)
        sel_all = QPushButton("Select all")
        sel_all.clicked.connect(self.select_all)
        toolbar.addWidget(sel_all)
        desel = QPushButton("Deselect")
        desel.clicked.connect(self.deselect_all)
        toolbar.addWidget(desel)
        toolbar.addStretch()
        for text, action, icon in [
            ("Up", "up", "fa5s.play"),
            ("Start", "start", "fa5s.play-circle"),
            ("Stop", "stop", "fa5s.stop-circle"),
            ("Down", "down", "fa5s.stop"),
            ("Restart", "restart", "fa5s.redo"),
            ("Pull", "pull", "fa5s.download"),
            ("Build", "build", "fa5s.hammer"),
        ]:
            b = icon_button(text, icon, lambda _=False, a=action: self.spawn(self.selected_action(a)))
            b.setToolTip(f"{text} selected Compose projects")
            toolbar.addWidget(b)
        lay.addLayout(toolbar)

        panel = QFrame()
        panel.setObjectName("panel")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(12, 12, 12, 12)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "Project", "Status", "Config files", "Actions"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        # All columns Interactive so the user can resize every one of them.
        h = self.table.horizontalHeader()
        h.setStretchLastSection(False)
        for c in range(self.table.columnCount()):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        h.setMinimumSectionSize(40)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 260)
        self.table.setColumnWidth(4, 132)
        self.table.verticalHeader().setDefaultSectionSize(50)
        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.table.itemChanged.connect(self._on_check_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        pl.addWidget(self.table, 1)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(170)
        pl.addWidget(self.output)
        lay.addWidget(panel, 1)
        return page

    # ── selection helpers ─────────────────────────────────────
    def __init_selection_state(self) -> None:
        if not hasattr(self, "_syncing_selection"):
            self._syncing_selection = False

    def _update_selection(self) -> None:
        self.__init_selection_state()
        count = len(self._selected_projects())
        self.selected_label.setText(f"{count} selected")

    def _on_table_selection_changed(self) -> None:
        """Row highlight → checkbox ticks (and reverse for bulk UX)."""
        self.__init_selection_state()
        if self._syncing_selection:
            return
        self._syncing_selection = True
        try:
            selected = {idx.row() for idx in self.table.selectionModel().selectedRows()}
            self.table.blockSignals(True)
            for r in range(self.table.rowCount()):
                cell = self.table.item(r, 0)
                if cell is None:
                    continue
                want = Qt.CheckState.Checked if r in selected else Qt.CheckState.Unchecked
                if cell.checkState() != want:
                    cell.setCheckState(want)
            self.table.blockSignals(False)
        finally:
            self._syncing_selection = False
        self._update_selection()

    def _on_check_changed(self, item: QTableWidgetItem) -> None:
        self.__init_selection_state()
        if self._syncing_selection or item is None or item.column() != 0:
            return
        self._syncing_selection = True
        try:
            row = item.row()
            sm = self.table.selectionModel()
            from PySide6.QtCore import QItemSelectionModel
            index = self.table.model().index(row, 0)
            if item.checkState() == Qt.CheckState.Checked:
                sm.select(index, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            else:
                sm.select(index, QItemSelectionModel.SelectionFlag.Deselect | QItemSelectionModel.SelectionFlag.Rows)
        finally:
            self._syncing_selection = False
        self._update_selection()

    def select_all(self) -> None:
        self.__init_selection_state()
        self._syncing_selection = True
        try:
            self.table.selectAll()
            self.table.blockSignals(True)
            for r in range(self.table.rowCount()):
                cell = self.table.item(r, 0)
                if cell:
                    cell.setCheckState(Qt.CheckState.Checked)
            self.table.blockSignals(False)
        finally:
            self._syncing_selection = False
        self._update_selection()

    def deselect_all(self) -> None:
        self.__init_selection_state()
        self._syncing_selection = True
        try:
            self.table.clearSelection()
            self.table.blockSignals(True)
            for r in range(self.table.rowCount()):
                cell = self.table.item(r, 0)
                if cell:
                    cell.setCheckState(Qt.CheckState.Unchecked)
            self.table.blockSignals(False)
        finally:
            self._syncing_selection = False
        self._update_selection()

    def _selected_projects(self) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for r in range(self.table.rowCount()):
            check = self.table.item(r, 0)
            name_item = self.table.item(r, 1)
            if not name_item:
                continue
            name = name_item.text().strip()
            if not name:
                continue
            checked = check and check.checkState() == Qt.CheckState.Checked
            selected = False
            if self.table.selectionModel():
                selected = self.table.selectionModel().isRowSelected(r, self.table.rootIndex())
            if checked or selected:
                if name not in seen:
                    names.append(name)
                    seen.add(name)
        return names

    def _extra_compose_args(self) -> list[str]:
        """Build --env-file / -e args from the option row."""
        args: list[str] = []
        env_file = self.env_file_edit.text().strip()
        if env_file:
            args += ["--env-file", env_file]
        profile = getattr(self, "profile_edit", None)
        if profile is not None:
            for part in [x.strip() for x in profile.text().split(",") if x.strip()]:
                args += ["--profile", part]
        for token in self.env_vars_edit.text().split():
            token = token.strip()
            if not token or "=" not in token:
                continue
            args += ["-e", token]
        return args

    # ── load / actions ────────────────────────────────────────
    async def load(self) -> None:
        sid = self.get_server_id()
        if not sid:
            return
        ssh = self.get_ssh()
        if ssh is None:
            return
        dm = DockerManager(ssh)
        bin_cmd = await dm.detect_compose_bin()
        discovered = await dm.compose_ls()
        registered = self.store.load_compose_projects().get(sid, [])
        by_name: dict[str, dict[str, Any]] = {}
        for x in discovered:
            name = x.get("Name") or x.get("Project") or ""
            if not name:
                continue
            status = x.get("Status") or "—"
            # normalize empty / down statuses from compose ls -a
            status_l = str(status).lower()
            if not status or status == "—" or status_l in {"", "0", "exited"}:
                status = status if status and status != "—" else "stopped"
            cfg_raw = x.get("ConfigFiles") or x.get("ConfigFile") or ""
            by_name[name] = {
                "Name": name,
                "Status": status,
                "ConfigFiles": cfg_raw,
                "registered": False,
            }
            # Soft-remember newly discovered projects so they survive compose down
            try:
                if sid and cfg_raw and name not in {r.get("name") for r in registered}:
                    self.store.add_compose_project(sid, {
                        "name": name,
                        "config_files": [p.strip() for p in str(cfg_raw).split(",") if p.strip()],
                        "working_dir": "",
                        "note": "auto",
                    })
                    by_name[name]["registered"] = True
            except Exception:
                pass
        for reg in registered:
            name = reg.get("name") or ""
            if not name:
                continue
            if name in by_name:
                by_name[name]["registered"] = True
                if not by_name[name].get("ConfigFiles") and reg.get("config_files"):
                    by_name[name]["ConfigFiles"] = ",".join(reg.get("config_files") or [])
            else:
                by_name[name] = {
                    "Name": name,
                    "Status": "stopped · registered",
                    "ConfigFiles": ",".join(reg.get("config_files") or []) or reg.get("working_dir") or "—",
                    "registered": True,
                    "working_dir": reg.get("working_dir") or "",
                }
        self._compose_rows = list(by_name.values())
        selected = {
            str(
                self.table.item(r, 1).data(Qt.ItemDataRole.UserRole)
                or self.table.item(r, 1).text()
            )
            for r in range(self.table.rowCount())
            if self.table.item(r, 1) is not None
            and self.table.item(r, 0) is not None
            and self.table.item(r, 0).checkState() == Qt.CheckState.Checked
        }
        old_updates = self.table.updatesEnabled()
        old_signals = self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        try:
            self.table.setRowCount(0)
            for x in self._compose_rows:
                name = x.get("Name") or "—"
                status = x.get("Status") or "—"
                cfg = x.get("ConfigFiles") or "—"
                rr = self.table.rowCount()
                self.table.insertRow(rr)
                chk = QTableWidgetItem("")
                chk.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
                chk.setCheckState(
                    Qt.CheckState.Checked if name in selected else Qt.CheckState.Unchecked
                )
                chk.setData(Qt.ItemDataRole.UserRole, name)
                chk.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(rr, 0, chk)
                nm = QTableWidgetItem(name)
                nm.setData(Qt.ItemDataRole.UserRole, name)
                self.table.setItem(rr, 1, nm)
                self.table.setItem(rr, 2, QTableWidgetItem(str(status)))
                self.table.setItem(rr, 3, QTableWidgetItem(str(cfg)))
                self.table.setCellWidget(rr, 4, self._row_actions(name))
                self.table.setRowHeight(rr, 40)
        finally:
            self.table.blockSignals(old_signals)
            self.table.setSortingEnabled(False)
            self.table.setUpdatesEnabled(old_updates)
            self.table.viewport().update()
            self.table.update()
        bin_note = f" (using {bin_cmd})" if bin_cmd else " (compose binary not found)"
        self.output.setPlainText(
            f"{len(self._compose_rows)} Compose project(s){bin_note}. "
            f"Discovered: {len(discovered)}, registered: {len(registered)}."
        )

    def config_for(self, project: str) -> list[str]:
        for item in self._compose_rows:
            if item.get("Name") == project or item.get("Project") == project:
                cfg = item.get("ConfigFiles") or item.get("ConfigFile") or ""
                files = [x.strip() for x in str(cfg).split(",") if x.strip() and x.strip() != "—"]
                if files:
                    return files
        sid = self.get_server_id() or ""
        for reg in self.store.load_compose_projects().get(sid, []):
            if reg.get("name") == project:
                return list(reg.get("config_files") or [])
        return []

    def _remember_project(self, project: str, configs: list[str] | None = None) -> None:
        """Persist project so it stays visible after compose down."""
        sid = self.get_server_id()
        if not sid or not project:
            return
        configs = [c for c in (configs or self.config_for(project) or []) if c and c != "—"]
        # Keep existing working_dir/note if already registered
        existing = {}
        for reg in self.store.load_compose_projects().get(sid, []):
            if reg.get("name") == project:
                existing = dict(reg)
                break
        self.store.add_compose_project(sid, {
            "name": project,
            "config_files": configs or existing.get("config_files") or [],
            "working_dir": existing.get("working_dir") or "",
            "note": existing.get("note") or "",
        })

    async def action_for_project(self, action: str, project: str) -> str:
        # Long-running actions stream logs in a dialog with Force stop
        if action in {"up", "build", "pull"}:
            self.stream_action(action, project)
            return f"[{project}] streaming {action}…"

        configs = self.config_for(project)
        args = ["-p", project]
        for cfg in configs:
            if cfg and cfg != "—":
                args += ["-f", cfg]
        if action in {"up", "start", "run", "create"}:
            args += self._extra_compose_args()
        if action == "up":
            args += ["up", "-d"]
        elif action == "logs":
            args += ["logs", "--tail", "200", "--timestamps"]
        else:
            args.append(action)
        ssh = self.get_ssh()
        r = await DockerManager(ssh).compose(args, timeout=3600)
        out = r.stdout if r.exit_code == 0 else (r.stderr or r.stdout or f"exit {r.exit_code}")
        try:
            self._remember_project(project, configs)
        except Exception:
            pass
        bus = NotificationBus.instance()
        if r.exit_code == 0:
            bus.success(f"Compose {action}", project)
        else:
            bus.error(f"Compose {action} failed", project)
        self.output.setPlainText(f"[{project}]\n{out}")
        return f"[{project}]\n{out}"

    def stream_action(self, action: str, project: str) -> None:
        """Run compose up/build/pull with live logs.

        Uses a *non-modal* dialog so qasync can keep pumping asyncio tasks.
        Nested QDialog.exec() freezes the app with qasync (compose appears stuck
        and the process cannot exit cleanly).
        """
        dlg = QDialog(self.parent)
        dlg.setWindowTitle(f"Compose {action} — {project}")
        dlg.resize(820, 520)
        dlg.setModal(False)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        lay = QVBoxLayout(dlg)
        status = QLabel(f"Running «{action}» on {project}…")
        status.setObjectName("muted")
        lay.addWidget(status)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        mono = view.font()
        mono.setFamily("Consolas")
        mono.setStyleHint(mono.StyleHint.Monospace)
        view.setFont(mono)
        lay.addWidget(view, 1)
        btn_row = QHBoxLayout()
        force_btn = QPushButton("Force stop")
        force_btn.setProperty("danger", True)
        force_btn.setToolTip("Terminate the remote compose process")
        close_btn = QPushButton("Close")
        close_btn.setEnabled(False)
        btn_row.addWidget(force_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        state: dict[str, Any] = {"proc": None, "task": None, "stopped": False, "done": False}

        def _append(text: str) -> None:
            view.appendPlainText(text)
            cursor = view.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            view.setTextCursor(cursor)

        async def _kill_proc(proc) -> None:
            if proc is None:
                return
            for meth in ("terminate", "kill", "close"):
                try:
                    fn = getattr(proc, meth, None)
                    if callable(fn):
                        res = fn()
                        if asyncio.iscoroutine(res):
                            await res
                except Exception:
                    pass
            # Close streams so readline unblocks
            for stream_name in ("stdin", "stdout", "stderr"):
                try:
                    stream = getattr(proc, stream_name, None)
                    if stream is not None and hasattr(stream, "close"):
                        stream.close()
                except Exception:
                    pass

        async def _run() -> None:
            ssh = self.get_ssh()
            if ssh is None:
                _append("Not connected.")
                status.setText("Failed — not connected")
                close_btn.setEnabled(True)
                return
            configs = self.config_for(project)
            try:
                self._remember_project(project, configs)
            except Exception:
                pass
            parts: list[str] = ["-p", project]
            for cfg in configs:
                if cfg and cfg != "—":
                    parts += ["-f", cfg]
            if action in {"up", "start"}:
                parts += self._extra_compose_args()
            if action == "up":
                # -d detaches; avoid always --build (slow/hang on pull). User can Build separately.
                parts += ["up", "-d"]
            elif action == "build":
                parts += ["build", "--progress", "plain"]
            else:
                parts.append(action)
            dm = DockerManager(ssh)
            try:
                bin_cmd = await dm.detect_compose_bin()
            except Exception as exc:
                _append(f"[error] detect compose: {exc}")
                status.setText("Failed")
                close_btn.setEnabled(True)
                return
            if not bin_cmd:
                _append("Compose binary not available on the remote host.")
                status.setText("Failed")
                close_btn.setEnabled(True)
                return
            if bin_cmd == "docker compose":
                cmd = "docker compose " + " ".join(_shell_quote(p) for p in parts)
            else:
                cmd = "docker-compose " + " ".join(_shell_quote(p) for p in parts)
            # Merge stderr → stdout; run under sh so 2>&1 works reliably
            shell_cmd = f"sh -lc {_shell_quote(cmd + ' 2>&1')}"
            _append(f"$ {cmd}")
            status.setText(f"Running «{action}»…")
            force_btn.setEnabled(True)
            stream_ssh = None
            try:
                stream_ssh = await ssh.clone_transport()
                proc = await stream_ssh.start_command(shell_cmd)
                state["proc"] = proc
                got_any = False
                while not state["stopped"]:
                    try:
                        line = await asyncio.wait_for(proc.stdout.readline(), timeout=0.4)
                    except asyncio.TimeoutError:
                        # Process still running; check if exited without more output
                        try:
                            if hasattr(proc, "exit_status") and proc.exit_status is not None:
                                break
                        except Exception:
                            pass
                        continue
                    except (asyncio.CancelledError, Exception):
                        break
                    if not line:
                        break
                    got_any = True
                    text = line if isinstance(line, str) else line.decode("utf-8", "replace")
                    _append(text.rstrip("\n"))
                if state["stopped"]:
                    status.setText(f"Force-stopped «{action}» on {project}")
                    NotificationBus.instance().warning(f"Compose {action} stopped", project)
                else:
                    if not got_any:
                        _append("(no output from remote process)")
                    status.setText(f"Finished «{action}» on {project}")
                    NotificationBus.instance().success(f"Compose {action}", project)
            except asyncio.CancelledError:
                status.setText("Cancelled")
            except Exception as exc:
                _append(f"[error] {exc}")
                status.setText("Failed")
                NotificationBus.instance().error(f"Compose {action} failed", str(exc)[:120])
            finally:
                state["done"] = True
                force_btn.setEnabled(False)
                close_btn.setEnabled(True)
                proc = state.get("proc")
                state["proc"] = None
                await _kill_proc(proc)
                if stream_ssh is not None:
                    await stream_ssh.close()
                # Refresh list without blocking dialog teardown
                try:
                    await self.load()
                except Exception:
                    pass

        def force_stop() -> None:
            if state["done"]:
                return
            state["stopped"] = True
            status.setText("Stopping…")
            force_btn.setEnabled(False)
            task = state.get("task")
            # Schedule kill on the asyncio loop without deadlocking
            async def _stop() -> None:
                await _kill_proc(state.get("proc"))
                state["proc"] = None
                if task and not task.done():
                    task.cancel()
            try:
                self.spawn(_stop())
            except Exception:
                pass

        force_btn.clicked.connect(force_stop)
        close_btn.clicked.connect(dlg.close)

        def _on_close(*_args) -> None:
            if not state["done"]:
                force_stop()

        dlg.finished.connect(_on_close)
        state["task"] = self.spawn(_run())
        dlg.show()  # non-modal — do NOT exec()
        dlg.raise_()
        dlg.activateWindow()

    async def selected_action(self, action: str) -> None:
        projects = self._selected_projects()
        if not projects:
            QMessageBox.information(
                self.parent, "Compose",
                "Select one or more Compose projects first (click rows or use Select all).",
            )
            return
        self.run_multi_action(action, projects)

    def run_multi_action(self, action: str, projects: list[str]) -> None:
        """One window, one tab per project — browser-style."""
        dlg = MultiTabJobDialog(f"Compose {action} — {len(projects)} project(s)", self.parent)
        for proj in projects:
            dlg.add_tab(proj)
        dlg.show()
        dlg.raise_()

        async def _run_one(project: str) -> bool:
            dlg.set_running(project)
            try:
                ok = await self._stream_into_tab(dlg, action, project)
                if dlg._total > 1:
                    done = dlg._done
                    dlg.status.setText(f"Completed {done}/{dlg._total} · last: {project}")
                return ok
            except asyncio.CancelledError:
                dlg.append(project, "[cancelled]")
                dlg.mark_tab_done(project, ok=False)
                raise
            except Exception as exc:
                dlg.append(project, f"[error] {exc}")
                dlg.mark_tab_done(project, ok=False)
                return False

        async def _run_all() -> None:
            # Start all selected projects together. Compose binary detection is
            # single-flight, while the actual compose commands use independent
            # SSH channels so one slow project cannot delay the others.
            results = await asyncio.gather(
                *(_run_one(project) for project in projects),
                return_exceptions=True,
            )
            failed = sum(1 for result in results if isinstance(result, Exception) or result is False)
            completed = len(projects) - failed
            if failed:
                NotificationBus.instance().warning(
                    f"Compose {action} finished with errors",
                    f"{completed}/{len(projects)} projects completed",
                )
            else:
                NotificationBus.instance().success(
                    f"Compose {action} completed",
                    f"{len(projects)} project(s) completed",
                )
            try:
                await self.load()
            except Exception:
                pass

        self.spawn(_run_all())

    async def _compose_once(self, action: str, project: str) -> str:
        """Run a non-streaming compose action; return combined output."""
        configs = self.config_for(project)
        args = ["-p", project]
        for cfg in configs:
            if cfg and cfg != "—":
                args += ["-f", cfg]
        if action in {"up", "start", "run", "create"}:
            args += self._extra_compose_args()
        if action == "up":
            args += ["up", "-d"]
        elif action == "logs":
            args += ["logs", "--tail", "200", "--timestamps"]
        else:
            args.append(action)
        ssh = self.get_ssh()
        r = await DockerManager(ssh).compose(args, timeout=3600)
        try:
            self._remember_project(project, configs)
        except Exception:
            pass
        out = r.stdout if r.exit_code == 0 else (r.stderr or r.stdout or f"exit {r.exit_code}")
        return f"$ {r.command}\n{out}"

    async def _stream_into_tab(self, dlg: MultiTabJobDialog, action: str, project: str) -> bool:
        ssh = self.get_ssh()
        if ssh is None:
            dlg.append(project, "Not connected.")
            dlg.mark_tab_done(project, ok=False)
            NotificationBus.instance().error(f"Compose {action} failed", f"{project}: not connected")
            return False
        configs = self.config_for(project)
        try:
            self._remember_project(project, configs)
        except Exception:
            pass
        parts: list[str] = ["-p", project]
        for cfg in configs:
            if cfg and cfg != "—":
                parts += ["-f", cfg]
        if action in {"up", "start"}:
            parts += self._extra_compose_args()
        if action == "up":
            parts += ["up", "-d"]
        elif action == "build":
            parts += ["build", "--progress", "plain"]
        else:
            parts.append(action)
        dm = DockerManager(ssh)
        bin_cmd = await dm.detect_compose_bin()
        if not bin_cmd:
            dlg.append(project, "Compose binary not available.")
            dlg.mark_tab_done(project, ok=False)
            NotificationBus.instance().error(f"Compose {action} failed", f"{project}: compose binary not available")
            return False
        if bin_cmd == "docker compose":
            cmd = "docker compose " + " ".join(_shell_quote(p) for p in parts)
        else:
            cmd = "docker-compose " + " ".join(_shell_quote(p) for p in parts)
        shell_cmd = f"sh -lc {_shell_quote(cmd + ' 2>&1')}"
        dlg.append(project, f"$ {cmd}")
        proc = None
        try:
            proc = await ssh.start_command(shell_cmd)
            while True:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=0.25)
                except asyncio.TimeoutError:
                    # Some SSH implementations expose the final exit code only
                    # after the stream closes; keep polling without blocking the UI.
                    continue
                if not line:
                    break
                text = line if isinstance(line, str) else line.decode("utf-8", "replace")
                if text:
                    dlg.append(project, text.rstrip("\r\n"))
            exit_status = getattr(proc, "exit_status", None)
            if callable(exit_status):
                exit_status = exit_status()
                if asyncio.iscoroutine(exit_status):
                    exit_status = await exit_status
            ok = exit_status in (None, 0)
            dlg.mark_tab_done(project, ok=ok)
            if ok:
                NotificationBus.instance().success(f"Compose {action}", project)
            else:
                dlg.append(project, f"[exit {exit_status}]")
                NotificationBus.instance().error(f"Compose {action} failed", project)
            return ok
        except asyncio.CancelledError:
            dlg.append(project, "[cancelled]")
            dlg.mark_tab_done(project, ok=False)
            NotificationBus.instance().warning(f"Compose {action} cancelled", project)
            raise
        except Exception as exc:
            dlg.append(project, f"[error] {exc}")
            dlg.mark_tab_done(project, ok=False)
            NotificationBus.instance().error(f"Compose {action} failed", project)
            return False
        finally:
            if proc is not None:
                for meth in ("close",):
                    try:
                        fn = getattr(proc, meth, None)
                        if callable(fn):
                            res = fn()
                            if asyncio.iscoroutine(res):
                                await res
                    except Exception:
                        pass

    # ── logs dialog with follow ───────────────────────────────
    def open_logs(self, project: str) -> None:
        dlg = QDialog(self.parent)
        dlg.setWindowTitle(f"Compose logs — {project}")
        dlg.resize(780, 480)
        lay = QVBoxLayout(dlg)
        follow_cb = QCheckBox("Follow (-f)")
        follow_cb.setChecked(True)
        top = QHBoxLayout()
        top.addWidget(follow_cb)
        top.addStretch()
        stop_btn = QPushButton("Stop")
        stop_btn.setEnabled(False)
        top.addWidget(stop_btn)
        lay.addLayout(top)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        mono = view.font()
        mono.setFamily("Consolas")
        mono.setStyleHint(mono.StyleHint.Monospace)
        view.setFont(mono)
        lay.addWidget(view, 1)
        close = QPushButton("Close")
        close.clicked.connect(dlg.reject)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

        state: dict[str, Any] = {"proc": None, "task": None}

        async def _run_logs() -> None:
            ssh = self.get_ssh()
            if ssh is None:
                return
            configs = self.config_for(project)
            # Build shell command for streaming
            parts = ["-p", project]
            for cfg in configs:
                if cfg and cfg != "—":
                    parts += ["-f", cfg]
            parts += ["logs", "--tail", "200", "--timestamps"]
            if follow_cb.isChecked():
                parts.append("-f")
            dm = DockerManager(ssh)
            bin_cmd = await dm.detect_compose_bin()
            if not bin_cmd:
                view.appendPlainText("Compose binary not available.")
                return
            if bin_cmd == "docker compose":
                cmd = "docker compose " + " ".join(_shell_quote(p) for p in parts)
            else:
                cmd = "docker-compose " + " ".join(_shell_quote(p) for p in parts)
            stop_btn.setEnabled(True)
            try:
                proc = await ssh.start_command(cmd)
                state["proc"] = proc
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    view.appendPlainText(line.rstrip("\n"))
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                view.appendPlainText(f"\n[error] {exc}")
            finally:
                stop_btn.setEnabled(False)
                state["proc"] = None

        def start() -> None:
            if state.get("task") and not state["task"].done():
                return
            view.clear()
            state["task"] = self.spawn(_run_logs())

        def stop() -> None:
            proc = state.get("proc")
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            task = state.get("task")
            if task and not task.done():
                task.cancel()

        stop_btn.clicked.connect(stop)
        dlg.finished.connect(lambda _: stop())
        start()
        dlg.setModal(False)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.show()  # non-modal — nested exec() freezes qasync
        dlg.raise_()

    # ── row actions / menus ───────────────────────────────────
    def _row_actions(self, project: str) -> QWidget:
        w = QWidget()
        l = QHBoxLayout(w)
        l.setContentsMargins(4, 2, 4, 2)
        l.setSpacing(4)
        menu_btn = QToolButton()
        menu_btn.setObjectName("rowMenuButton")
        menu_btn.setText("Actions")
        menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        menu_btn.setFixedHeight(26)
        menu_btn.setMinimumWidth(96)
        menu_btn.setMaximumWidth(120)
        if qta:
            try:
                menu_btn.setIcon(qta.icon("fa5s.bars", color="#dce6f0"))
            except Exception:
                pass
        menu = QMenu(menu_btn)
        for label, action, icon in [
            ("Up", "up", "fa5s.play"),
            ("Start", "start", "fa5s.play-circle"),
            ("Stop", "stop", "fa5s.stop-circle"),
            ("Down", "down", "fa5s.stop"),
            ("Restart", "restart", "fa5s.redo"),
            ("Pull", "pull", "fa5s.download"),
            ("Build", "build", "fa5s.hammer"),
            ("Logs", "logs", "fa5s.file-alt"),
        ]:
            if action == "logs":
                act = menu.addAction(qta.icon(icon) if qta else menu_btn.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView), label)
                act.triggered.connect(lambda _=False, p=project: self.open_logs(p))
            else:
                act = menu.addAction(qta.icon(icon) if qta else menu_btn.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton), label)
                act.triggered.connect(lambda _=False, a=action, p=project: self.spawn(self.action_for_project(a, p)))
        menu.addSeparator()
        cp = menu.addAction("Copy project name")
        cp.triggered.connect(lambda _=False, p=project: QApplication.clipboard().setText(p))
        rm = menu.addAction("Unregister project")
        rm.triggered.connect(lambda _=False, p=project: self.unregister(p))
        menu_btn.setMenu(menu)
        l.addWidget(menu_btn)
        return w

    def _context_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        item = self.table.item(row, 1)
        if not item:
            return
        project = item.text()
        menu = QMenu(self.parent)
        for label, action in [
            ("Up", "up"), ("Start", "start"), ("Stop", "stop"),
            ("Down", "down"), ("Restart", "restart"), ("Pull", "pull"),
            ("Build", "build"),
        ]:
            a = menu.addAction(label)
            a.triggered.connect(lambda _=False, a=action, p=project: self.spawn(self.action_for_project(a, p)))
        menu.addAction("Logs…").triggered.connect(lambda _=False, p=project: self.open_logs(p))
        menu.addSeparator()
        menu.addAction("Copy project name").triggered.connect(
            lambda _=False, p=project: QApplication.clipboard().setText(p)
        )
        menu.addAction("Unregister project").triggered.connect(
            lambda _=False, p=project: self.unregister(p)
        )
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def add_project_dialog(self) -> None:
        sid = self.get_server_id()
        if not sid:
            QMessageBox.information(self.parent, "Compose", "Connect to a server first.")
            return
        dlg = QDialog(self.parent)
        dlg.setWindowTitle("Add / Register Compose project")
        dlg.resize(480, 220)
        form = QFormLayout(dlg)
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("my-project")
        form.addRow("Project name", name_edit)
        cfg_edit = QLineEdit()
        cfg_edit.setPlaceholderText("/path/to/docker-compose.yml  (optional, comma-separated)")
        form.addRow("Config file(s)", cfg_edit)
        wd_edit = QLineEdit()
        wd_edit.setPlaceholderText("/path/to/project  (optional working dir)")
        form.addRow("Working dir", wd_edit)
        note_edit = QLineEdit()
        note_edit.setPlaceholderText("optional note")
        form.addRow("Note", note_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = name_edit.text().strip()
        if not name:
            QMessageBox.warning(self.parent, "Compose", "Project name is required.")
            return
        configs = [c.strip() for c in cfg_edit.text().split(",") if c.strip()]
        self.store.add_compose_project(sid, {
            "name": name,
            "config_files": configs,
            "working_dir": wd_edit.text().strip(),
            "note": note_edit.text().strip(),
        })
        NotificationBus.instance().success("Compose project registered", name)
        self.spawn(self.load())

    def unregister(self, name: str) -> None:
        sid = self.get_server_id()
        if not sid:
            return
        self.store.remove_compose_project(sid, name)
        NotificationBus.instance().info("Compose project unregistered", name)
        self.spawn(self.load())


def _shell_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)
