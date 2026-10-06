from __future__ import annotations

from app.core.runtime import create_task
import asyncio
import json
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow,
    QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget,
)

from app.docker.manager import DockerManager
from app.files.browser import RemoteFileBrowser, ContainerFileBackend
from app.ui.widgets import icon_button, MetricCard
from app.files.highlighter import CodeHighlighter


def _parse_cpu_percent(raw: str | None) -> str:
    if not raw:
        return "—"
    return raw.strip()


def _split_usage(usage: str | None) -> tuple[str, str]:
    if not usage:
        return "—", "—"
    parts = [p.strip() for p in usage.split("/")]
    if len(parts) == 2:
        return parts[0], parts[1]
    return usage.strip(), "—"


def _selectable_plain(font: QFont | None = None) -> QPlainTextEdit:
    """Read-only editor that still allows select + Ctrl+C."""
    w = QPlainTextEdit()
    w.setReadOnly(True)
    w.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.TextSelectableByKeyboard
    )
    w.setUndoRedoEnabled(False)
    if font is not None:
        w.setFont(font)
    return w


class ContainerDetailsWindow(QMainWindow):
    """Inspect / Logs / Stats / Files workspace for a single container."""

    def __init__(self, parent, ssh, container_name: str, initial_tab: str = "Inspect"):
        super().__init__(parent)
        self.ssh = ssh
        self.dm = DockerManager(ssh)
        self.name = str(container_name or "").strip().lstrip("/")
        if "," in self.name:
            self.name = self.name.split(",", 1)[0].strip()
        if not self.name or self.name in {"False", "True", "None"}:
            self.name = "unknown"
        self.setWindowTitle(f"Container — {self.name}")
        self.resize(1200, 780)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._closed = False
        self._loading = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._auto_tick)
        self._inspect_hl = None
        self._stats_hl = None
        self._build()
        tab_map = {"Inspect": 0, "Logs": 1, "Stats": 2, "Files": 3}
        self.tabs.setCurrentIndex(tab_map.get(initial_tab, 0))
        self._spawn(self.refresh_all())

    def _build(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("terminalToolbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 8, 10, 8)
        bl.setSpacing(6)

        title = QLabel(self.name)
        title.setStyleSheet("font-size:15px; font-weight:700;")
        bl.addWidget(title)
        bl.addSpacing(12)

        self.shell_btn = icon_button("Shell", "fa5s.terminal", self._open_shell, "Open shell in container")
        self.refresh_btn = icon_button(
            "Refresh", "fa5s.sync-alt",
            lambda: self._spawn(self.refresh_all()),
            "Refresh current / all data",
        )
        bl.addWidget(self.shell_btn)
        bl.addWidget(self.refresh_btn)
        bl.addStretch()

        # Same pattern as main Overview: Off / 2 / 5 / 10 …
        auto_lbl = QLabel("Auto refresh")
        auto_lbl.setObjectName("muted")
        auto_lbl.setStyleSheet("color:#8a95a4; font-size:12px;")
        bl.addWidget(auto_lbl)
        self.auto_refresh = QComboBox()
        self.auto_refresh.addItem("Off", 0)
        self.auto_refresh.addItem("2 seconds", 2)
        self.auto_refresh.addItem("5 seconds", 5)
        self.auto_refresh.addItem("10 seconds", 10)
        self.auto_refresh.addItem("30 seconds", 30)
        self.auto_refresh.setCurrentIndex(0)
        self.auto_refresh.setToolTip("Auto-refresh Logs & Stats (like Overview on the main window)")
        self.auto_refresh.setMinimumWidth(120)
        self.auto_refresh.currentIndexChanged.connect(self._auto_changed)
        bl.addWidget(self.auto_refresh)

        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setStyleSheet("color:#8a95a4; font-size:12px; padding-left:8px;")
        bl.addWidget(self.status)
        layout.addWidget(bar)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)

        # ── Inspect ──
        inspect_page = QWidget()
        ip = QVBoxLayout(inspect_page)
        ip.setContentsMargins(0, 0, 0, 0)
        ip.setSpacing(0)
        inspect_bar = QHBoxLayout()
        inspect_bar.setContentsMargins(8, 6, 8, 6)
        inspect_bar.addStretch()
        inspect_bar.addWidget(icon_button("Copy JSON", "fa5s.copy", self._copy_inspect, "Copy pretty inspect JSON"))
        ip.addLayout(inspect_bar)
        self.inspect_view = _selectable_plain(mono)
        ip.addWidget(self.inspect_view, 1)
        self.tabs.addTab(inspect_page, "Inspect")

        # ── Logs ──
        logs_page = QWidget()
        lp = QVBoxLayout(logs_page)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(0)
        logs_bar = QHBoxLayout()
        logs_bar.setContentsMargins(8, 6, 8, 6)
        logs_bar.addWidget(QLabel("Last 500 lines · select text or use Copy"))
        logs_bar.addStretch()
        logs_bar.addWidget(icon_button("Copy all", "fa5s.copy", self._copy_logs, "Copy entire log buffer"))
        logs_bar.addWidget(icon_button("Copy selection", "fa5s.highlighter", self._copy_logs_selection, "Copy selected log text"))
        logs_bar.addWidget(icon_button("Refresh logs", "fa5s.sync-alt", lambda: self._spawn(self._load_logs()), "Reload logs"))
        lp.addLayout(logs_bar)
        self.logs_view = _selectable_plain(mono)
        self.logs_view.setStyleSheet("background:#080d13; color:#dbe5ef;")
        lp.addWidget(self.logs_view, 1)
        self.tabs.addTab(logs_page, "Logs")

        # ── Stats ──
        stats_page = QWidget()
        sp = QVBoxLayout(stats_page)
        sp.setContentsMargins(12, 12, 12, 12)
        sp.setSpacing(10)
        cards = QGridLayout()
        cards.setSpacing(10)
        self.stat_cpu = MetricCard("CPU %")
        self.stat_mem = MetricCard("Memory")
        self.stat_mem_pct = MetricCard("Mem %")
        self.stat_net = MetricCard("Network I/O")
        self.stat_block = MetricCard("Block I/O")
        self.stat_pids = MetricCard("PIDs")
        for i, w in enumerate(
            [self.stat_cpu, self.stat_mem, self.stat_mem_pct, self.stat_net, self.stat_block, self.stat_pids]
        ):
            cards.addWidget(w, i // 3, i % 3)
        sp.addLayout(cards)

        stats_bar = QHBoxLayout()
        stats_bar.addWidget(QLabel("Raw stats (JSON)"))
        stats_bar.addStretch()
        stats_bar.addWidget(icon_button("Copy JSON", "fa5s.copy", self._copy_stats, "Copy pretty stats JSON"))
        stats_bar.addWidget(icon_button("Refresh stats", "fa5s.sync-alt", lambda: self._spawn(self._load_stats()), "Reload stats"))
        sp.addLayout(stats_bar)

        self.stats_raw = _selectable_plain(mono)
        self.stats_raw.setPlaceholderText("Formatted stats JSON appears here…")
        self.stats_raw.setMinimumHeight(200)
        sp.addWidget(self.stats_raw, 1)
        self.tabs.addTab(stats_page, "Stats")

        # ── Files ──
        try:
            backend = ContainerFileBackend(self.ssh, self.name)
            self.files_browser = RemoteFileBrowser(backend, f"Files — {self.name}")
            self.tabs.addTab(self.files_browser, "Files")
        except Exception as exc:
            fallback = _selectable_plain(mono)
            fallback.setPlainText(f"File browser unavailable:\n{exc}")
            self.files_browser = None
            self.tabs.addTab(fallback, "Files")

        self.setCentralWidget(root)

    def _open_shell(self):
        parent = self.parent()
        if parent is not None and hasattr(parent, "_open_container_terminal"):
            parent._open_container_terminal(self.name)

    def _auto_changed(self, _index: int = 0):
        self._timer.stop()
        seconds = int(self.auto_refresh.currentData() or 0)
        if seconds > 0:
            self._timer.start(seconds * 1000)

    def _auto_tick(self):
        if self._closed or self._loading:
            return
        idx = self.tabs.currentIndex()
        if idx == 1:
            self._spawn(self._load_logs())
        elif idx == 2:
            self._spawn(self._load_stats())
        elif idx == 0:
            self._spawn(self._load_inspect())

    def closeEvent(self, event):
        self._closed = True
        self._timer.stop()
        super().closeEvent(event)

    def _spawn(self, coro):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = create_task(coro)

        def _done(t):
            try:
                t.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        task.add_done_callback(_done)

    async def refresh_all(self):
        if self._closed or self._loading:
            return
        self._loading = True
        self.status.setText("Refreshing…")
        try:
            await asyncio.gather(
                self._load_inspect(),
                self._load_logs(),
                self._load_stats(),
            )
            if self.files_browser is not None and hasattr(self.files_browser, "_refresh_async"):
                try:
                    await self.files_browser._refresh_async()
                except Exception:
                    pass
            self.status.setText("Ready")
        finally:
            self._loading = False

    def _clipboard_set(self, text: str, ok_msg: str) -> None:
        QGuiApplication.clipboard().setText(text or "")
        self.status.setText(ok_msg)

    def _copy_inspect(self):
        self._clipboard_set(self.inspect_view.toPlainText(), "Inspect JSON copied")

    def _copy_logs(self):
        self._clipboard_set(self.logs_view.toPlainText(), "Logs copied")

    def _copy_logs_selection(self):
        cursor = self.logs_view.textCursor()
        text = cursor.selectedText().replace("\u2029", "\n")
        if not text:
            self.status.setText("Select some log text first")
            return
        self._clipboard_set(text, "Selection copied")

    def _copy_stats(self):
        self._clipboard_set(self.stats_raw.toPlainText(), "Stats JSON copied")

    async def _load_inspect(self):
        result = await self.dm.run(["inspect", self.name], timeout=60)
        text = (result.stdout or result.stderr or "").strip()
        try:
            data = json.loads(text)
            text = json.dumps(data, indent=2, ensure_ascii=False)
        except Exception:
            pass
        self.inspect_view.setPlainText(text)
        try:
            if self._inspect_hl is not None:
                self._inspect_hl.setDocument(None)
            self._inspect_hl = CodeHighlighter(self.inspect_view.document(), "json")
        except Exception:
            pass

    async def _load_logs(self):
        result = await self.dm.run(["logs", "--tail", "500", "--timestamps", self.name], timeout=60)
        text = result.stdout or result.stderr or ""
        # Preserve selection only if empty refresh preference — always show latest
        self.logs_view.setPlainText(text)
        cursor = self.logs_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.logs_view.setTextCursor(cursor)

    async def _load_stats(self):
        result = await self.dm.run(
            ["stats", "--no-stream", "--format", "{{json .}}", self.name],
            timeout=30,
        )
        raw = (result.stdout or result.stderr or "").strip()
        data: dict[str, Any] | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

        # Also fetch the container's CPU quota so we can normalise the
        # displayed CPU% relative to the quota (not the host capacity).
        # Without this, a container limited to --cpus=2 on an 8-core host
        # shows 25% CPU when it's actually at 100% of its 2-CPU quota.
        quota_cores: float | None = None
        try:
            from app.docker.stats_math import (
                get_container_cpu_quota,
                normalise_cpu_pct_by_quota,
                parse_docker_stats_json_line,
            )
            inspect_result = await self.dm.run(
                ["inspect", "--format", "{{json .}}", self.name],
                timeout=15,
            )
            for line in inspect_result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    inspect_data = json.loads(line)
                    quota_cores = get_container_cpu_quota(inspect_data)
                    break
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass

        if data is not None:
            pretty = json.dumps(data, indent=2, ensure_ascii=False)
            self.stats_raw.setPlainText(pretty)
            try:
                if self._stats_hl is not None:
                    self._stats_hl.setDocument(None)
                self._stats_hl = CodeHighlighter(self.stats_raw.document(), "json")
            except Exception:
                pass
            # Parse the raw CPUPerc string ("0.42%") into a float.
            parsed = parse_docker_stats_json_line(json.dumps(data))
            cpu_pct_num = parsed.get("_cpu_pct_num") if parsed else None
            if cpu_pct_num is not None and quota_cores is not None and quota_cores > 0:
                # Re-scale relative to the container's CPU quota.
                normalised = normalise_cpu_pct_by_quota(cpu_pct_num, quota_cores)
                if normalised is not None:
                    cpu_display = f"{normalised:.2f}%"
                    cpu_display += f"  (of {quota_cores:.1f} CPU quota)"
                else:
                    cpu_display = _parse_cpu_percent(data.get("CPUPerc") or data.get("CPU %"))
            else:
                # No quota set — show the host-relative percentage as-is.
                cpu_display = _parse_cpu_percent(data.get("CPUPerc") or data.get("CPU %"))
            self.stat_cpu.set_value(cpu_display)
            mem_usage = data.get("MemUsage") or data.get("MEM USAGE / LIMIT") or ""
            used, limit = _split_usage(mem_usage)
            self.stat_mem.set_value(f"{used} / {limit}" if limit != "—" else used)
            self.stat_mem_pct.set_value((data.get("MemPerc") or data.get("MEM %") or "—").strip())
            self.stat_net.set_value(str(data.get("NetIO") or data.get("NET I/O") or "—").strip())
            self.stat_block.set_value(str(data.get("BlockIO") or data.get("BLOCK I/O") or "—").strip())
            self.stat_pids.set_value(str(data.get("PIDs") or data.get("PIDS") or "—").strip())
        else:
            self.stats_raw.setPlainText(raw or "(no stats — is the container running?)")
            for card in (
                self.stat_cpu, self.stat_mem, self.stat_mem_pct,
                self.stat_net, self.stat_block, self.stat_pids,
            ):
                card.set_value("—")
