from __future__ import annotations
from pathlib import Path
from contextlib import contextmanager
import shlex
import os
import posixpath
import stat

import asyncio
import json
import logging
from typing import Any

from PySide6.QtCore import Qt, QSize, QTimer, QPoint
from PySide6.QtGui import QAction, QKeySequence, QColor, QFont, QCursor
from PySide6.QtWidgets import (
    QScrollArea,
    QAbstractItemView, QAbstractButton, QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QDialog, QFileDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem, QMenu, QInputDialog,
    QToolButton, QVBoxLayout, QWidget, QHeaderView, QStyle, QProgressBar, QCheckBox, QSpinBox, QDialogButtonBox
)

from app.docker.manager import DockerManager
from app.server.monitor import ServerMonitor
from app.ssh.session_manager import SessionManager
from app.storage.settings import SettingsStore
from app.ui.pages.containers_page import ContainersPage
from app.ui.job_tabs import MultiTabJobDialog
from app.ui.compose_panel import ComposePanel
from app.ui.docker_pickers import ResourcePickerDialog, similar_names, append_csv, NameSuggestEdit, PortCheckEdit
from app.ui.notifications import NotificationBus, ToastHost, NotificationBell
from app.ui.styles import theme_stylesheet, DARK, LIGHT
from app.terminal.web_widget import create_terminal_widget, XtermTerminalWidget, HAS_WEBENGINE
from app.terminal.emulator import TerminalWidget  # fallback / type checks
from app.terminal.predictor import (
    CommandPredictor, load_remote_history, split_shell_commands,
    split_pasted_shell_commands,
)
from app.files.browser import RemoteFileBrowser, ServerFileBackend, ContainerFileBackend
from app.files.terminal_explorer import TerminalFileExplorer
from app.ui.docker_results_table import DockerResultsTable
from app.ui.dialogs import ErrorDialog, ConfirmRemoveDialog
from app.files.highlighter import CodeHighlighter
from app.ui.container_details import ContainerDetailsWindow
from app.ui.widgets import NavButton, MetricCard, icon_button, AutoRefreshControl, TableAutoRefresh, ProxySettingsWidget
from app.ui.widgets.table import StandardTableWidget, TableActionMenuController, create_table_actions_cell
from app.ui.resource_monitor import ResourceMonitorDialog
from app.workers.task_manager import TaskManager

log = logging.getLogger(__name__)
try:
    import qtawesome as qta
except ImportError:
    qta = None





class MainWindow(QMainWindow):
    # (section, [(name, icon, shortcut), ...])
    NAV = [
        ("OVERVIEW", [("Overview", "fa5s.tachometer-alt", "Ctrl+1")]),
        ("DOCKER", [
            ("Containers", "fa5s.box-open", "Ctrl+2"),
            ("Stats", "fa5s.chart-line", "Ctrl+Shift+2"),
            ("Images", "fa5s.clone", "Ctrl+3"),
            ("Storage", "fa5s.database", "Ctrl+4"),
            ("Compose", "fa5s.layer-group", "Ctrl+5"),
            ("Events", "fa5s.stream", "Ctrl+6"),
            ("Config", "fa5s.cogs", "Ctrl+0"),
        ]),
        ("HOST", [
            ("Server", "fa5s.server", "Ctrl+7"),
            ("Logs", "fa5s.file-alt", "Ctrl+8"),
        ]),
        ("TOOLS", [("Terminal", "fa5s.terminal", "Ctrl+`")]),
    ]
    PAGE_SHORTCUTS = {
        "Overview": "Ctrl+1", "Containers": "Ctrl+2", "Images": "Ctrl+3",
        "Storage": "Ctrl+4", "Compose": "Ctrl+5", "Events": "Ctrl+6", "Config": "Ctrl+0",
        "Server": "Ctrl+7", "Logs": "Ctrl+8", "Settings": "Ctrl+9",
        "Stats": "Ctrl+Shift+2",
    }

    def __init__(self, manager: SessionManager, profiles, store: SettingsStore):
        super().__init__()
        self.manager = manager
        self.profiles = profiles
        self.store = store
        self.current_id: str | None = None
        self.terms: dict[str, Any] = {}
        # Row menus live outside table cells so live refreshes cannot destroy
        # an Actions popup that is currently open.
        self._table_action_menu = TableActionMenuController(
            self,
            icon_factory=(
                lambda icon_name: qta.icon(icon_name, color=self._ui_icon_color())
                if qta is not None else None
            ),
        )
        self.terminal_widgets: dict[str, QWidget] = {}
        self._magic_table_kind: dict[str, str] = {}
        self.terminal_meta: dict[str, QLabel] = {}
        self.terminal_status: dict[str, str] = {}
        self.terminal_server_ids: dict[str, str] = {}
        self.terminal_shells: dict[str, str] = {}
        # One outstanding command or confirmed paste batch per session. The
        # shell's next prompt result confirms the submitted input.
        self._terminal_pending_command_results: dict[str, tuple[str, str, str, str, bool]] = {}
        self._terminal_selected_suggestions: dict[str, str] = {}
        self._command_predictor = CommandPredictor()
        ai_prefs = self.store.load_ui_prefs()
        self._terminal_ai_shadow_enabled = bool(ai_prefs.get("terminal_ai_shadow_enabled", True))
        self._terminal_ai_suggestions_enabled = bool(ai_prefs.get("terminal_ai_suggestions_enabled", True))
        self._terminal_ai_learn_history = bool(ai_prefs.get("terminal_ai_learn_history", True))
        self._terminal_ai_persist_learning = bool(ai_prefs.get("terminal_ai_persist_learning", True))
        try:
            if self._terminal_ai_persist_learning:
                self._command_predictor.load_state_dict(
                    self.store.load_terminal_learning_state()
                )
        except Exception:
            log.debug("Could not restore terminal AI learning state", exc_info=True)
        self._tasks: set[asyncio.Task] = set()
        self.task_manager = TaskManager()
        self._terminal_predictor_persist_dirty = False
        self._terminal_predictor_persist_active = False
        self._page_refresh_task = None
        self._server_ui_states: dict[str, str] = {}
        self._shutdown_task: asyncio.Task | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._terminal_panel_expanded = False
        self._terminal_mode = "hidden"
        self._nav_buttons: dict[str, QToolButton] = {}
        self._history: list[str] = []
        self._history_index = -1
        self._shortcut_actions: dict[str, QAction] = {}
        self._overview_refresh_seconds = 5
        self._overview_timer = QTimer(self)
        self._overview_timer.timeout.connect(self._auto_refresh_overview)
        self._table_refreshers: dict[str, tuple[AutoRefreshControl, TableAutoRefresh]] = {}
        self._table_refresh_default = 5
        self._resource_monitor_dialog = None
        self._server_process_loading = False
        self._server_process_refresh_task: asyncio.Task | None = None
        self._terminal_files_visible = False
        # Prevent concurrent terminal-creation coroutines from racing before
        # SessionManager registers the newly opened session in self.terms.
        self._terminal_creation_in_progress = False
        self._server_generation = 0
        self.setWindowTitle("SSHDock — Docker over SSH")
        self.resize(1480, 930)
        self.setMinimumSize(QSize(960, 600))
        self._build()
        self._wire_manager()
        self._toast = ToastHost(self)  # no-op; bell is the UX
        NotificationBus.instance().notified.connect(self._on_app_notification)
        self._apply_ui_prefs()

    def _build(self):
        root = QWidget(); root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0); root_layout.setSpacing(0)

        self._sidebar_collapsed = False
        self._sidebar_expanded_w = 248
        self._sidebar_collapsed_w = 56
        self.sidebar = QFrame(); self.sidebar.setObjectName("sidebar"); self.sidebar.setFixedWidth(self._sidebar_expanded_w)
        sl = QVBoxLayout(self.sidebar); sl.setContentsMargins(10, 12, 10, 12); sl.setSpacing(4)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(6)
        self.sidebar_toggle = QToolButton()
        self.sidebar_toggle.setObjectName("sidebarToggle")
        self.sidebar_toggle.setToolTip("Collapse / expand sidebar")
        self.sidebar_toggle.setToolTipDuration(800)
        self.sidebar_toggle.setAutoRaise(True)
        self.sidebar_toggle.setFixedSize(28, 28)
        try:
            if qta:
                self.sidebar_toggle.setIcon(qta.icon("fa5s.bars", color="#9aa6b5"))
        except Exception:
            self.sidebar_toggle.setText("☰")
        self.sidebar_toggle.clicked.connect(self._toggle_sidebar)
        brand_row.addWidget(self.sidebar_toggle)
        self.brand = QLabel("SSHDock"); self.brand.setObjectName("brand")
        brand_row.addWidget(self.brand)
        self.brand_sub = QLabel("Remote Docker"); self.brand_sub.setObjectName("brandSub")
        brand_row.addWidget(self.brand_sub); brand_row.addStretch()
        sl.addLayout(brand_row)

        self.server_card = QFrame(); self.server_card.setObjectName("serverCard")
        sc = QVBoxLayout(self.server_card); sc.setContentsMargins(11, 9, 11, 9); sc.setSpacing(2)
        self.server_name = QLabel("No server connected"); self.server_name.setObjectName("serverName")
        self.server_details = QLabel("Connect an SSH server to begin"); self.server_details.setObjectName("muted")
        self.server_details.setWordWrap(True)
        sc.addWidget(self.server_name); sc.addWidget(self.server_details)
        sl.addWidget(self.server_card)

        self._nav_section_labels: list[QLabel] = []
        for section, items in self.NAV:
            hdr = QLabel(section); hdr.setObjectName("sectionLabel"); sl.addWidget(hdr)
            self._nav_section_labels.append(hdr)
            for name, icon_name, shortcut in items:
                btn = NavButton(name, icon_name, shortcut=shortcut, checkable=True)
                if name == "Terminal":
                    btn.setToolTip("Open full Terminal workspace. Ctrl+` toggles bottom panel.")
                    btn.clicked.connect(lambda _=False: self.select_page("Terminal"))
                else:
                    btn.clicked.connect(lambda _=False, n=name: self.select_page(n))
                sl.addWidget(btn)
                self._nav_buttons[name] = btn

        sl.addStretch()
        self.disconnect_side = QPushButton("Disconnect")
        self.disconnect_side.setProperty("danger", True)
        self.disconnect_side.setToolTip("Disconnect from current server")
        self.disconnect_side.setToolTipDuration(800)
        try:
            if qta:
                self.disconnect_side.setIcon(qta.icon("fa5s.plug", color="#ff939b"))
        except Exception:
            pass
        self.disconnect_side.clicked.connect(lambda: self._spawn(self.disconnect_current()))
        sl.addWidget(self.disconnect_side)
        settings_btn = NavButton("Settings", "fa5s.cog", shortcut="Ctrl+9", checkable=True)
        settings_btn.clicked.connect(lambda _=False: self.select_page("Settings"))
        sl.addWidget(settings_btn)
        self._nav_buttons["Settings"] = settings_btn
        root_layout.addWidget(self.sidebar)

        content = QWidget(); cl = QVBoxLayout(content); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(0)
        top = QFrame(); top.setObjectName("topbar")
        tl = QHBoxLayout(top); tl.setContentsMargins(14, 6, 14, 6); tl.setSpacing(8)
        self.title = QLabel("Overview"); self.title.setObjectName("topTitle"); tl.addWidget(self.title)
        tl.addSpacing(8)
        self.page_loading = QProgressBar(); self.page_loading.setRange(0, 0); self.page_loading.setTextVisible(False)
        self.page_loading.setFixedWidth(72); self.page_loading.setFixedHeight(4); self.page_loading.setObjectName("pageLoading"); self.page_loading.hide()
        tl.addWidget(self.page_loading)
        tl.addStretch()
        self.server_picker = QComboBox()
        self.server_picker.setObjectName("serverPicker")
        self.server_picker.setMinimumWidth(260)
        self.server_picker.setMaximumWidth(420)
        self.server_picker.setMinimumHeight(32)
        self.server_picker.setMaximumHeight(34)
        self.server_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.server_picker.setMinimumContentsLength(18)
        self.server_picker.view().setTextElideMode(Qt.TextElideMode.ElideNone)
        self.server_picker.view().setMinimumWidth(260)
        # Popup list rows need room for descenders (g, p, y, …)
        # Popup styling is inherited from the active application theme.
        self.server_picker.currentIndexChanged.connect(self._server_selected)
        tl.addWidget(self.server_picker)
        self.add_server_btn = icon_button("Add", "fa5s.plus", self.add_server, "Add / connect SSH server")
        tl.addWidget(self.add_server_btn)
        self.state = QLabel("● Disconnected"); self.state.setObjectName("stateDisconnected"); tl.addWidget(self.state)
        self.refresh_btn = icon_button("Refresh", "fa5s.sync-alt", lambda: self._spawn(self.refresh_current_page()), "Refresh current page (F5)")
        tl.addWidget(self.refresh_btn)
        self.reconnect_btn = icon_button("Reconnect", "fa5s.plug", lambda: self._spawn(self.reconnect_current()), "Reconnect current server")
        tl.addWidget(self.reconnect_btn)
        self.disconnect_btn = icon_button("Disconnect", "fa5s.power-off", lambda: self._spawn(self.disconnect_current()), "Disconnect")
        tl.addWidget(self.disconnect_btn)
        cl.addWidget(top)

        self.stack = QStackedWidget()
        self.main_splitter = QSplitter(Qt.Orientation.Vertical)
        self.main_splitter.setChildrenCollapsible(True)
        self.main_splitter.setHandleWidth(8)
        self.main_splitter.setObjectName("mainSplitter")
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 0)
        self.main_splitter.splitterMoved.connect(self._on_terminal_splitter_moved)
        self.main_splitter.addWidget(self.stack)
        self._build_terminal_panel()
        self.main_splitter.addWidget(self.terminal_panel)
        self.terminal_panel.setMinimumHeight(120)
        self.terminal_panel.setMaximumHeight(16777215)
        self.terminal_panel.hide()
        cl.addWidget(self.main_splitter, 1)

        footer = QFrame(); footer.setObjectName("terminalFooter")
        fl = QHBoxLayout(footer); fl.setContentsMargins(8, 3, 8, 3)
        self.terminal_toggle = QToolButton()
        self.terminal_toggle.setText("Terminal")
        self.terminal_toggle.setToolTip("Toggle terminal panel")
        self.terminal_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.terminal_toggle.clicked.connect(lambda _checked=False: self.toggle_terminal_panel())
        try:
            self.terminal_toggle.setProperty("sshdockIconName", "fa5s.terminal")
            self.terminal_toggle.setProperty("sshdockIconRole", "normal")
            self.terminal_toggle.setIcon(
                qta.icon("fa5s.terminal", color=self._ui_icon_color("normal"))
                if qta else QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
            )
        except Exception:
            pass
        fl.addWidget(self.terminal_toggle)
        self.terminal_shortcut_badge = QLabel("Ctrl+`")
        self.terminal_shortcut_badge.setObjectName("navShortcut")
        self.terminal_shortcut_badge.setStyleSheet("")
        fl.addWidget(self.terminal_shortcut_badge)
        self.terminal_state = QLabel("No active terminal")
        self.terminal_state.setObjectName("muted")
        fl.addWidget(self.terminal_state)
        fl.addStretch()
        self.notify_bell = NotificationBell(footer)
        fl.addWidget(self.notify_bell)
        cl.addWidget(footer)
        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)

        self.pages: dict[str, QWidget] = {}
        self._install_shortcuts()
        self._build_pages()
        self._refresh_server_picker()
        self._set_connection_state(False)
        self.select_page("Overview")
        self.statusBar().showMessage("Ready")

    def _wire_manager(self):
        self.manager.state_changed.connect(self._manager_state)

    def _install_shortcuts(self):
        shortcuts = [
            ("terminal.toggle", "Ctrl+`", self._shortcut_toggle_terminal),
            ("terminal.new", "Ctrl+Shift+`", self._shortcut_new_terminal),
            ("terminal.close", "Ctrl+W", self._shortcut_close_terminal),
            ("terminal.next", "Ctrl+PageDown", self._shortcut_next_terminal),
            ("terminal.previous", "Ctrl+PageUp", self._shortcut_previous_terminal),
            ("terminal.find", "Ctrl+Shift+F", self._focus_terminal_find),
            ("terminal.clear", "Ctrl+L", self._shortcut_clear_terminal),
            ("page.overview", "Ctrl+1", lambda: self.select_page("Overview")),
            ("page.containers", "Ctrl+2", lambda: self.select_page("Containers")),
            ("page.images", "Ctrl+3", lambda: self.select_page("Images")),
            ("page.storage", "Ctrl+4", lambda: self.select_page("Storage")),
            ("page.compose", "Ctrl+5", lambda: self.select_page("Compose")),
            ("page.events", "Ctrl+6", lambda: self.select_page("Events")),
            ("page.config", "Ctrl+0", lambda: self.select_page("Config")),
            ("page.server", "Ctrl+7", lambda: self.select_page("Server")),
            ("page.logs", "Ctrl+8", lambda: self.select_page("Logs")),
            ("page.settings", "Ctrl+9", lambda: self.select_page("Settings")),
            ("refresh", "F5", lambda: self._spawn(self.refresh_current_page())),
            ("server.add", "Ctrl+Shift+A", self.add_server),
            ("server.reconnect", "Ctrl+Shift+C", lambda: self._spawn(self.reconnect_current())),
        ]
        self._shortcut_actions.clear()
        for name, sequence, callback in shortcuts:
            action = QAction(self)
            if name == "terminal.toggle":
                # Explicit binding — string "Ctrl+`" is unreliable on some platforms
                action.setShortcut(QKeySequence(Qt.KeyboardModifier.ControlModifier | Qt.Key.Key_QuoteLeft))
            else:
                action.setShortcut(QKeySequence(sequence))
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            action.triggered.connect(callback)
            self.addAction(action)
            self._shortcut_actions[name] = action
        # Refresh Settings table if it already exists
        if hasattr(self, "shortcuts_table"):
            self._populate_shortcuts_table()

    def _shortcut_toggle_terminal(self):
        """VS Code-style: show bottom terminal (create if needed) or hide it."""
        self.toggle_terminal_panel()

    def _shortcut_new_terminal(self):
        if self.current_id:
            self._spawn(self._create_terminal())
        else:
            self.open_terminal()

    def _shortcut_close_terminal(self):
        self._close_selected_terminal()

    def _shortcut_next_terminal(self):
        count = self.terminal_picker.count()
        if count:
            self.terminal_picker.setCurrentIndex((self.terminal_picker.currentIndex() + 1) % count)

    def _shortcut_previous_terminal(self):
        count = self.terminal_picker.count()
        if count:
            self.terminal_picker.setCurrentIndex((self.terminal_picker.currentIndex() - 1) % count)

    def _shortcut_clear_terminal(self):
        sid = self.terminal_picker.currentData()
        if sid in self.terms:
            widget = self.terminal_widgets.get(sid)
            if widget:
                term = self._find_term_widget(widget)
                if term:
                    term.clear()


    def _build_pages(self):
        self.pages["Overview"] = self._overview_page()
        self.pages["Containers"] = self._containers_page()
        self.pages["Stats"] = self._container_stats_page()
        self.pages["Images"] = self._images_page()
        self.pages["Storage"] = self._storage_page()
        self.compose_panel = ComposePanel(
            get_server_id=lambda: self.current_id,
            get_ssh=lambda: self.manager.servers[self.current_id].ssh if self.current_id and self.current_id in self.manager.servers else None,
            store=self.store,
            spawn=self._spawn,
            parent=self,
        )
        self.pages["Compose"] = self.compose_panel.page
        self.pages["Events"] = self._events_page()
        self.pages["Config"] = self._docker_config_page()
        self.pages["Server"] = self._server_page()
        self.pages["Logs"] = self._logs_page()
        self.pages["Terminal"] = self._terminal_page()
        self.pages["Settings"] = self._settings_page()
        for widget in self.pages.values():
            self.stack.addWidget(widget)

    def _terminal_page(self):
        """Full-page Terminal workspace host. The live panel is reparented here when active."""
        page = QWidget()
        page.setObjectName("terminalPage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        placeholder = QLabel("Opening terminal workspace…")
        placeholder.setObjectName("muted")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(placeholder)
        self._terminal_page_host = page
        self._terminal_page_layout = lay
        self._terminal_page_placeholder = placeholder
        return page

    def _overview_page(self):
        page = QWidget()
        lay = QVBoxLayout()
        page.setLayout(lay)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(14)
        head = QHBoxLayout()
        title = QLabel("Overview"); title.setObjectName("heroTitle"); head.addWidget(title)
        sub = QLabel("Live host and Docker telemetry over the persistent SSH connection."); sub.setObjectName("heroSubtitle"); head.addWidget(sub); head.addStretch()
        self.overview_refresh = self._register_table_auto_refresh(
            "overview",
            head,
            self.refresh_overview,
            page_name="Overview",
        )
        head.addWidget(icon_button("Refresh", "fa5s.sync-alt", lambda: self._spawn(self.refresh_overview()), "Refresh overview"))
        head.addWidget(icon_button("Resource monitor", "fa5s.microchip", self.open_resource_monitor, "Open live CPU and memory monitor"))
        lay.addLayout(head)
        grid = QGridLayout(); grid.setSpacing(10); self.cards = {}
        for i, (key, title) in enumerate([("docker", "Docker Engine"), ("containers", "Containers"), ("cpu", "CPU"), ("memory", "Memory"), ("disk", "Disk"), ("host", "Hostname")]):
            card = MetricCard(title); self.cards[key] = card.value; grid.addWidget(card, i // 3, i % 3)
        lay.addLayout(grid)
        proc_panel = QFrame(); proc_panel.setObjectName("panel"); proc_lay = QVBoxLayout(proc_panel); proc_lay.setContentsMargins(14, 12, 14, 12)
        r=QHBoxLayout(); lbl=QLabel("Host processes"); lbl.setObjectName("panelTitle"); r.addWidget(lbl); r.addStretch(); self.overview_proc_meta=QLabel("Top CPU processes"); self.overview_proc_meta.setObjectName("muted"); r.addWidget(self.overview_proc_meta); proc_lay.addLayout(r)
        self.overview_processes=StandardTableWidget(0,6); self.overview_processes.setHorizontalHeaderLabels(["PID","USER","CPU %","MEM %","STATE","COMMAND"]); self.overview_processes.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.overview_processes.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.overview_processes.verticalHeader().setVisible(False); self.overview_processes.setAlternatingRowColors(True)
        self.overview_processes.setColumnWidth(0,80); self.overview_processes.setColumnWidth(1,100); self.overview_processes.setColumnWidth(2,75); self.overview_processes.setColumnWidth(3,75); self.overview_processes.setColumnWidth(4,90); self.overview_processes.setColumnWidth(5,280)
        self._make_columns_resizable(self.overview_processes, stretch_cols={5})
        proc_lay.addWidget(self.overview_processes,1); lay.addWidget(proc_panel,1)
        self._install_table_copy_context(self.overview_processes)
        panel=QFrame(); panel.setObjectName("panel"); pl=QVBoxLayout(panel); pl.setContentsMargins(14,12,14,12); row=QHBoxLayout(); t=QLabel("Quick actions"); t.setObjectName("panelTitle"); row.addWidget(t); row.addStretch(); pl.addLayout(row)
        actions=QHBoxLayout()
        for text,target_name,icon in [("Containers","Containers","fa5s.box-open"),("Images","Images","fa5s.clone"),("Storage","Storage","fa5s.database"),("Compose","Compose","fa5s.layer-group")]: actions.addWidget(icon_button(text,icon,lambda _,p=target_name:self.select_page(p)))
        terminal=icon_button("Terminal","fa5s.terminal",self.open_terminal); terminal.setProperty("accent",True); actions.addWidget(terminal); actions.addStretch(); pl.addLayout(actions); lay.addWidget(panel)
        self._configure_overview_timer()
        return page

    def open_resource_monitor(self, _checked=False):
        if not self.current_id:
            QMessageBox.information(self, "No server", "Connect to an SSH server first.")
            return
        # Reuse existing dialog if still alive (closed with hide, or still open)
        dlg = getattr(self, "_resource_monitor_dialog", None)
        if dlg is not None:
            try:
                # Closed dialogs may still exist without WA_DeleteOnClose — just show again
                if not dlg.isVisible():
                    dlg.show()
                dlg.raise_()
                dlg.activateWindow()
                return
            except RuntimeError:
                self._resource_monitor_dialog = None
        self._server_process_loading = False
        self._server_process_refresh_task: asyncio.Task | None = None
        self._terminal_files_visible = False
        monitor = ServerMonitor(self.manager.servers[self.current_id].ssh)
        dlg = ResourceMonitorDialog(monitor, self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._resource_monitor_dialog = dlg

        def _clear(*_args):
            if getattr(self, "_resource_monitor_dialog", None) is dlg:
                self._resource_monitor_dialog = None
        self._server_process_loading = False
        self._server_process_refresh_task: asyncio.Task | None = None
        self._terminal_files_visible = False

        dlg.destroyed.connect(_clear)
        dlg.finished.connect(_clear)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _table_refresh_default_seconds(self) -> int:
        try:
            value = self.store.load_ui_prefs().get("table_auto_refresh_seconds", 5)
            value = int(value)
            return value if value in {0, 2, 5, 10, 30} else 5
        except Exception:
            return 5

    def _register_table_auto_refresh(
        self,
        key: str,
        layout,
        callback,
        *,
        page_name: str,
        allowed=None,
        default: int | None = None,
    ):
        control = AutoRefreshControl(
            self._table_refresh_default_seconds() if default is None else default,
            self,
        )
        runner = TableAutoRefresh(self)
        if allowed is None:
            allowed = lambda: bool(self.current_id) and self.pages.get(page_name) is self.stack.currentWidget()
        def fire():
            return self._spawn(callback(), f"Auto refresh: {key}")
        def changed(seconds: int):
            runner.configure(seconds, fire, allowed=allowed)
        control.intervalChanged.connect(changed)
        runner.configure(control.seconds, fire, allowed=allowed)
        self._table_refreshers[key] = (control, runner)
        layout.addWidget(control)
        return control

    def _stop_all_table_refreshers(self):
        for _control, runner in self._table_refreshers.values():
            runner.stop()

    def _overview_refresh_changed(self, _=0):
        # Kept as compatibility API for older callers.
        seconds = self.overview_refresh.seconds if hasattr(self.overview_refresh, "seconds") else 0
        self._overview_refresh_seconds = seconds

    def _configure_overview_timer(self):
        # AutoRefreshControl/TableAutoRefresh own the timer now.
        if hasattr(self, "_overview_timer"):
            self._overview_timer.stop()

    def _auto_refresh_overview(self):
        # Legacy hook; the shared table refresh controller handles scheduling.
        if self.current_id and self.title.text() == "Overview":
            self._spawn(self.refresh_overview(), "Auto refresh: overview")

    def _install_copy_context(self, widget):
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(lambda pos, w=widget: self._copy_context_menu(w, pos))

    def _copy_context_menu(self, widget, pos):
        menu = QMenu(widget)
        copy_action = menu.addAction("Copy")
        select_action = menu.addAction("Select All")
        paste_action = None
        if not widget.isReadOnly():
            paste_action = menu.addAction("Paste")
        action = menu.exec(widget.mapToGlobal(pos))
        if action is copy_action and hasattr(widget, "copy"):
            widget.copy()
        elif action is select_action and hasattr(widget, "selectAll"):
            widget.selectAll()
        elif paste_action is not None and action is paste_action and hasattr(widget, "paste"):
            widget.paste()

    def _subnav(self, names: list[str], target: QWidget):
        bar = QFrame(); bar.setObjectName("subNav"); l=QHBoxLayout(bar); l.setContentsMargins(12,8,12,8); l.setSpacing(6)
        for idx,name in enumerate(names):
            b=QToolButton(); b.setText(name); b.setCheckable(True); b.setAutoExclusive(True); b.setProperty("class","subnav")
            b.clicked.connect(lambda _,n=name:self._show_subtarget(n)); l.addWidget(b)
        l.addStretch(); target._sub_buttons = bar.findChildren(QToolButton); return bar

    def _show_subtarget(self,name):
        page = getattr(self, "_subpage_map", {}).get(name)
        if page: self._substack.setCurrentWidget(page)

    def _containers_page(self):
        page = ContainersPage(
            self,
            make_columns_resizable=self._make_columns_resizable,
            actions_cell=self._actions_cell,
            spawn_action=lambda action, name: self._spawn(self.container_action_exec(action, name)),
            open_logs=lambda name: self._show_container_logs(name),
            open_shell=lambda name: self._open_container_terminal(name),
            open_files=lambda name: self._open_container_files(name),
            open_stats=lambda name: self._open_container_stats(name),
            open_inspect=lambda name: self._open_container_inspect(name),
            remove_one=lambda name: self._spawn(self.container_action_exec("rm", name)),
        )
        container_runner = TableAutoRefresh(self)
        def refresh_containers_tick():
            return self._spawn(self.load_containers(), "Auto refresh: containers")
        container_allowed = lambda: bool(self.current_id) and self.pages.get("Containers") is self.stack.currentWidget()
        container_runner.configure(page.auto_refresh.seconds, refresh_containers_tick, allowed=container_allowed)
        page.auto_refresh.intervalChanged.connect(
            lambda seconds: container_runner.configure(seconds, refresh_containers_tick, allowed=container_allowed)
        )
        self._table_refreshers["containers"] = (page.auto_refresh, container_runner)
        page.refresh_requested.connect(lambda: self._spawn(self.load_containers()))
        page.new_container_requested.connect(self._new_container_placeholder)
        page.details_requested.connect(self._open_container_details)
        page.filter_changed.connect(lambda: self._render_containers(getattr(self, "_container_rows", [])))
        # expose widgets for legacy call sites
        self.containers_page = page
        self.container_table = page.table
        self.container_search = page.search
        self.container_filter = page.filter
        self.container_selected = page.selected_label
        page.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        page.table.customContextMenuRequested.connect(self._containers_context_menu)
        return page


    def _container_stats_page(self):
        """A dedicated page showing live stats for ALL running containers.

        Unlike the per-container Stats tab (which only shows one container
        at a time), this page fetches ``docker stats`` for every running
        container in one call and displays them in a sortable table with
        CPU% normalised by each container's CPU quota.
        """
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Container Stats")
        title.setObjectName("heroTitle")
        header.addWidget(title)
        sub = QLabel("Live resource usage for all running containers. CPU% is normalised by each container's CPU quota.")
        sub.setObjectName("heroSubtitle")
        header.addWidget(sub, 1)

        self.stats_auto_refresh = self._register_table_auto_refresh(
            "stats",
            header,
            self.load_container_stats,
            page_name="Stats",
        )

        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.setToolTip("Fetch fresh stats for all running containers")
        refresh_btn.clicked.connect(lambda: self._spawn(self.load_container_stats()))
        header.addWidget(refresh_btn)
        lay.addLayout(header)

        # Summary cards: total CPU%, total memory, container count.
        summary = QHBoxLayout()
        summary.setSpacing(10)
        self.stats_card_containers = QLabel("0 containers")
        self.stats_card_containers.setObjectName("metricCard")
        self.stats_card_containers.setProperty("statsCard", True)
        summary.addWidget(self.stats_card_containers)
        self.stats_card_cpu = QLabel("CPU: —")
        self.stats_card_cpu.setObjectName("metricCard")
        self.stats_card_cpu.setProperty("statsCard", True)
        summary.addWidget(self.stats_card_cpu)
        self.stats_card_mem = QLabel("Memory: —")
        self.stats_card_mem.setObjectName("metricCard")
        self.stats_card_mem.setProperty("statsCard", True)
        summary.addWidget(self.stats_card_mem)
        summary.addStretch()
        lay.addLayout(summary)

        # Main stats table.
        self.stats_table = StandardTableWidget(0, 8)
        self.stats_table.setHorizontalHeaderLabels(
            ["Name", "CPU %", "Mem %", "Memory", "Net I/O", "Block I/O", "PIDs", "Quota"]
        )
        self.stats_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.stats_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.stats_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setAlternatingRowColors(True)
        self.stats_table.setWordWrap(False)
        self.stats_table.setColumnWidth(0, 200)
        self.stats_table.setColumnWidth(1, 90)
        self.stats_table.setColumnWidth(2, 80)
        self.stats_table.setColumnWidth(3, 140)
        self.stats_table.setColumnWidth(4, 160)
        self.stats_table.setColumnWidth(5, 160)
        self.stats_table.setColumnWidth(6, 60)
        self.stats_table.setColumnWidth(7, 100)
        self._make_columns_resizable(self.stats_table, stretch_cols={0})
        lay.addWidget(self.stats_table, 1)

        hint = QLabel(
            "CPU% shows each container's usage relative to its own CPU quota "
            "(--cpus / --cpu-quota). A container at 100% is saturating its "
            "allocated cores. Hover any cell for the raw docker stats value."
        )
        hint.setWordWrap(True)
        hint.setObjectName("statsHint")
        lay.addWidget(hint)


        return page

    async def load_container_stats(self):
        """Fetch stats for all running containers and replace the table atomically."""
        if not self.current_id:
            return
        dm = DockerManager(self.manager.servers[self.current_id].ssh)
        try:
            stats_list = await dm.stats_all_with_quota()
        except Exception as exc:
            self.stats_card_containers.setText("Unable to refresh stats")
            self.stats_card_cpu.setText(str(exc)[:80])
            return

        total_cpu = 0.0
        total_mem_pct = 0.0
        with self._atomic_table(self.stats_table, (0,)):
            self.stats_table.setRowCount(0)
            for s in stats_list:
                name = (s.get("Name") or s.get("Container") or "—").lstrip("/")
                cpu_num = s.get("_cpu_pct_normalised")
                quota = s.get("_quota_cores")
                if cpu_num is not None:
                    cpu_str = f"{cpu_num:.2f}%"
                    total_cpu += cpu_num
                else:
                    cpu_str = s.get("CPUPerc") or "—"
                quota_str = f"{quota:.1f} CPUs" if quota is not None else "unlimited"
                mem_pct_str = s.get("MemPerc") or "—"
                mem_pct_num = s.get("_mem_pct_num")
                if mem_pct_num is not None:
                    total_mem_pct += mem_pct_num
                cells = [
                    name, cpu_str, mem_pct_str, s.get("MemUsage") or "—",
                    s.get("NetIO") or "—", s.get("BlockIO") or "—",
                    str(s.get("PIDs") or "—"), quota_str,
                ]
                row = self.stats_table.rowCount()
                self.stats_table.insertRow(row)
                for col, value in enumerate(cells):
                    item = QTableWidgetItem(str(value))
                    if col in (1, 2):
                        numeric = cpu_num if col == 1 else mem_pct_num
                        if numeric is not None:
                            item.setForeground(
                                QColor("#ff5260") if numeric >= 80
                                else QColor("#ffca4b") if numeric >= 50
                                else QColor("#39d47a")
                            )
                    self.stats_table.setItem(row, col, item)
                self.stats_table.setRowHeight(row, 34)

        n = len(stats_list)
        self.stats_card_containers.setText(f"{n} container{'s' if n != 1 else ''}")
        self.stats_card_cpu.setText(f"Total CPU: {total_cpu:.1f}%")
        self.stats_card_mem.setText(f"Total Mem: {total_mem_pct:.1f}%")
        self.statusBar().showMessage(f"Stats refreshed · {n} containers", 1800)


    def _images_page(self):
        page=QWidget(); lay=QVBoxLayout(page);
        page._layout_owner = lay; lay.setContentsMargins(24,20,24,24)
        row=QHBoxLayout(); title=QLabel("Images"); title.setObjectName("heroTitle"); row.addWidget(title); row.addStretch(); self.image_search=QLineEdit(); self._install_copy_context(self.image_search); self.image_search.setPlaceholderText("Search images…"); self.image_search.setMaximumWidth(260); self.image_search.textChanged.connect(self._filter_images); row.addWidget(self.image_search); pull=QPushButton("Pull image"); pull.setProperty("accent",True); pull.clicked.connect(self._pull_image); row.addWidget(pull); refresh=QPushButton("↻"); refresh.clicked.connect(lambda:self._spawn(self.load_images())); row.addWidget(refresh); lay.addLayout(row)
        self.image_table=StandardTableWidget(0,6); self.image_table.setHorizontalHeaderLabels(["Used","Repository","Tag","ID","Size","Actions"]); self.image_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.image_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection); self.image_table.verticalHeader().setVisible(False); self.image_table.verticalHeader().setDefaultSectionSize(38); self.image_table.setColumnWidth(0,56); self.image_table.setColumnWidth(1,240); self.image_table.setColumnWidth(2,100); self.image_table.setColumnWidth(3,140); self.image_table.setColumnWidth(4,100); self.image_table.setColumnWidth(5,110); self._make_columns_resizable(self.image_table, stretch_cols={1}); self.image_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.image_table.customContextMenuRequested.connect(lambda pos:self._table_context_menu(self.image_table,pos));
        img_bar=QHBoxLayout(); self.image_selected=QLabel("0 selected"); self.image_selected.setObjectName("metricHeading"); img_bar.addWidget(self.image_selected); des=QPushButton("Deselect"); des.clicked.connect(lambda: (self._deselect_table(self.image_table), self.image_selected.setText("0 selected"))); img_bar.addWidget(des); img_bar.addStretch()
        rm_sel=QPushButton("Remove selected"); rm_sel.setToolTip("Remove selected images"); rm_sel.clicked.connect(lambda: self._spawn(self._bulk_remove_images())); img_bar.addWidget(rm_sel)
        purge_img=QPushButton("Purge unused"); purge_img.setToolTip("docker image prune -f (dangling images)"); purge_img.setProperty("danger", True); purge_img.clicked.connect(lambda: self._spawn(self._prune_resource("image"))); img_bar.addWidget(purge_img)
        lay.addLayout(img_bar); self.image_table.itemSelectionChanged.connect(lambda: self.image_selected.setText(f"{len(self.image_table.selectionModel().selectedRows())} selected")); lay.addWidget(self.image_table,1); return page

    def _storage_page(self):
        page=QWidget(); lay=QVBoxLayout(page);
        page._layout_owner = lay; lay.setContentsMargins(24,20,24,24)
        title=QLabel("Storage"); title.setObjectName("heroTitle"); lay.addWidget(title)
        tabs=QTabWidget()
        self.volume_table=StandardTableWidget(0,5); self.volume_table.setHorizontalHeaderLabels(["Name","Driver","Scope","Used by","Actions"])
        self.network_table=StandardTableWidget(0,6); self.network_table.setHorizontalHeaderLabels(["Name","Driver","Scope","Used by","ID","Actions"])
        for t in (self.volume_table, self.network_table):
            t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            t.verticalHeader().setVisible(False)
            t.verticalHeader().setDefaultSectionSize(38)
        self.volume_table.customContextMenuRequested.connect(lambda pos:self._table_context_menu(self.volume_table,pos))
        self.network_table.customContextMenuRequested.connect(lambda pos:self._table_context_menu(self.network_table,pos))
        self.volume_table.setColumnWidth(0, 220); self.volume_table.setColumnWidth(1, 100); self.volume_table.setColumnWidth(2, 90); self.volume_table.setColumnWidth(3, 100); self.volume_table.setColumnWidth(4, 110)
        self.network_table.setColumnWidth(0, 180); self.network_table.setColumnWidth(1, 100); self.network_table.setColumnWidth(2, 90); self.network_table.setColumnWidth(3, 100); self.network_table.setColumnWidth(4, 140); self.network_table.setColumnWidth(5, 110)
        self._make_columns_resizable(self.volume_table, stretch_cols={0})
        self._make_columns_resizable(self.network_table, stretch_cols={0})
        v=QWidget(); vl=QVBoxLayout(v)
        vb=QHBoxLayout(); self.volume_selected=QLabel("0 selected"); self.volume_selected.setObjectName("metricHeading"); vb.addWidget(self.volume_selected)
        vdes=QPushButton("Deselect"); vdes.clicked.connect(lambda: (self._deselect_table(self.volume_table), self.volume_selected.setText("0 selected"))); vb.addWidget(vdes); vb.addStretch()
        vrm=QPushButton("Remove selected"); vrm.setToolTip("Remove selected volumes"); vrm.clicked.connect(lambda: self._spawn(self._bulk_remove_volumes())); vb.addWidget(vrm)
        vpurge=QPushButton("Purge unused"); vpurge.setToolTip("docker volume prune -f"); vpurge.setProperty("danger", True); vpurge.clicked.connect(lambda: self._spawn(self._prune_resource("volume"))); vb.addWidget(vpurge)
        self.volume_auto_refresh = self._register_table_auto_refresh(
            "volumes",
            vb,
            self.load_volumes,
            page_name="Storage",
            allowed=lambda: bool(self.current_id) and self.pages.get("Storage") is self.stack.currentWidget() and tabs.currentWidget() is v,
        )
        vref = QPushButton("↻ Refresh volumes")
        vref.clicked.connect(lambda: self._spawn(self.load_volumes()))
        vb.addWidget(vref)
        vl.addLayout(vb); vl.addWidget(self.volume_table,1)
        self.volume_table.itemSelectionChanged.connect(lambda: self.volume_selected.setText(f"{len(self.volume_table.selectionModel().selectedRows())} selected"))
        n=QWidget(); nl=QVBoxLayout(n)
        nb=QHBoxLayout(); self.network_selected=QLabel("0 selected"); self.network_selected.setObjectName("metricHeading"); nb.addWidget(self.network_selected)
        ndes=QPushButton("Deselect"); ndes.clicked.connect(lambda: (self._deselect_table(self.network_table), self.network_selected.setText("0 selected"))); nb.addWidget(ndes); nb.addStretch()
        nrm=QPushButton("Remove selected"); nrm.setToolTip("Remove selected networks"); nrm.clicked.connect(lambda: self._spawn(self._bulk_remove_networks())); nb.addWidget(nrm)
        npurge=QPushButton("Purge unused"); npurge.setToolTip("docker network prune -f"); npurge.setProperty("danger", True); npurge.clicked.connect(lambda: self._spawn(self._prune_resource("network"))); nb.addWidget(npurge)
        self.network_auto_refresh = self._register_table_auto_refresh(
            "networks",
            nb,
            self.load_networks,
            page_name="Storage",
            allowed=lambda: bool(self.current_id) and self.pages.get("Storage") is self.stack.currentWidget() and tabs.currentWidget() is n,
        )
        nref = QPushButton("↻ Refresh networks")
        nref.clicked.connect(lambda: self._spawn(self.load_networks()))
        nb.addWidget(nref)
        nl.addLayout(nb); nl.addWidget(self.network_table,1)
        self.network_table.itemSelectionChanged.connect(lambda: self.network_selected.setText(f"{len(self.network_table.selectionModel().selectedRows())} selected"))
        tabs.addTab(v,"Volumes"); tabs.addTab(n,"Networks"); lay.addWidget(tabs,1); return page

    def _compose_page(self):
        page=QWidget(); lay=QVBoxLayout(page); page._layout_owner=lay
        lay.setContentsMargins(24,20,24,24); lay.setSpacing(12)
        row=QHBoxLayout(); title=QLabel("Compose projects"); title.setObjectName("heroTitle"); row.addWidget(title); row.addStretch()
        add_btn=icon_button("Add project", "fa5s.plus", self._add_compose_project_dialog, "Register a compose file / project so it stays visible even when not running")
        row.addWidget(add_btn)
        refresh=icon_button("Refresh", "fa5s.sync-alt", lambda _=False:self._spawn(self.load_compose())); row.addWidget(refresh); lay.addLayout(row)

        toolbar=QHBoxLayout()
        self.compose_selected_label=QLabel("0 selected"); self.compose_selected_label.setObjectName("metricHeading"); toolbar.addWidget(self.compose_selected_label); toolbar.addStretch()
        for text, action, icon in [
            ("Up","up","fa5s.play"),
            ("Start","start","fa5s.play-circle"),
            ("Stop","stop","fa5s.stop-circle"),
            ("Down","down","fa5s.stop"),
            ("Restart","restart","fa5s.redo"),
            ("Pull","pull","fa5s.download"),
            ("Build","build","fa5s.hammer"),
        ]:
            b=icon_button(text, icon, lambda _=False,a=action:self._spawn(self._compose_selected_action(a))); b.setToolTip(f"{text} selected Compose projects"); toolbar.addWidget(b)
        lay.addLayout(toolbar)

        panel=QFrame(); panel.setObjectName("panel"); pl=QVBoxLayout(panel); pl.setContentsMargins(12,12,12,12)
        self.compose_table=StandardTableWidget(0,5)
        self.compose_table.setHorizontalHeaderLabels(["","Project","Status","Config files","Actions"])
        self.compose_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.compose_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.compose_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.compose_table.verticalHeader().setVisible(False); self.compose_table.setAlternatingRowColors(True)
        self.compose_table.setColumnWidth(0,40); self.compose_table.setColumnWidth(1,180); self.compose_table.setColumnWidth(2,120); self.compose_table.setColumnWidth(3,260); self.compose_table.setColumnWidth(4,132); self.compose_table.verticalHeader().setDefaultSectionSize(50)
        self._make_columns_resizable(self.compose_table, stretch_cols={3})
        self.compose_table.itemSelectionChanged.connect(self._update_compose_selection)
        self.compose_table.itemChanged.connect(lambda _item: self._update_compose_selection())
        self.compose_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.compose_table.customContextMenuRequested.connect(self._compose_context_menu)
        pl.addWidget(self.compose_table,1)
        self.compose_output=QPlainTextEdit(); self.compose_output.setReadOnly(True); self.compose_output.setMaximumHeight(170); pl.addWidget(self.compose_output)
        lay.addWidget(panel,1); return page
    def _update_compose_selection(self):
        count=0
        for r in range(self.compose_table.rowCount() if hasattr(self,"compose_table") else 0):
            cell=self.compose_table.item(r,0)
            if cell and cell.checkState()==Qt.CheckState.Checked: count += 1
        self.compose_selected_label.setText(f"{count} selected")

    def _selected_compose_projects(self):
        names=[]
        for r in range(self.compose_table.rowCount()):
            check=self.compose_table.item(r,0)
            if check and check.checkState()==Qt.CheckState.Checked:
                item=self.compose_table.item(r,1)
                if item: names.append(item.data(Qt.ItemDataRole.UserRole) or item.text())
        return names

    def _compose_context_menu(self,pos):
        row=self.compose_table.rowAt(pos.y())
        if row < 0: return
        item=self.compose_table.item(row,1)
        if not item: return
        project=item.text(); menu=QMenu(self)
        for label, action in [("Up","up"),("Start","start"),("Stop","stop"),("Down","down"),("Restart","restart"),("Pull","pull"),("Build","build")]:
            a=menu.addAction(label); a.triggered.connect(lambda _=False,a=action,p=project:self._spawn(self._compose_action_for_project(a,p)))
        menu.addSeparator()
        c=menu.addAction("Copy project name"); c.triggered.connect(lambda _=False,p=project:QApplication.clipboard().setText(p))
        menu.addAction("Unregister project").triggered.connect(lambda _=False,p=project: self._unregister_compose_project(p))
        menu.exec(self.compose_table.viewport().mapToGlobal(pos))

    async def _compose_selected_action(self, action):
        projects=self._selected_compose_projects()
        if not projects:
            QMessageBox.information(self,"Compose","Select one or more Compose projects first."); return
        outputs=[]
        for project in projects:
            outputs.append(await self._compose_action_for_project(action,project))
        self.compose_output.setPlainText("\n\n".join(outputs))
        await self.load_compose()

    async def _compose_action_for_project(self, action, project):
        configs = self._compose_config_for_project(project)
        # Build args without the "compose" prefix — DockerManager.compose() adds the right binary
        args = ["-p", project]
        for cfg in configs:
            if cfg and cfg != "—":
                args += ["-f", cfg]
        # Map friendly actions
        if action == "up":
            args += ["up", "-d"]
        elif action in {"start", "stop", "down", "restart", "pull", "build", "ps", "logs"}:
            args.append(action)
        else:
            args.append(action)
        r = await DockerManager(self.manager.servers[self.current_id].ssh).compose(args, timeout=3600)
        return f"[{project}]\n" + (r.stdout if r.exit_code == 0 else (r.stderr or r.stdout or f"exit {r.exit_code}"))

    def _compose_config_for_project(self, project):
        for item in getattr(self, "_compose_rows", []):
            if item.get("Name") == project or item.get("Project") == project:
                cfg = item.get("ConfigFiles") or item.get("ConfigFile") or ""
                files = [x.strip() for x in str(cfg).split(",") if x.strip() and x.strip() != "—"]
                if files:
                    return files
        # Also check persistent store
        for reg in self.store.load_compose_projects().get(self.current_id or "", []):
            if reg.get("name") == project:
                return list(reg.get("config_files") or [])
        return []



    def _add_compose_project_dialog(self):
        if not self.current_id:
            QMessageBox.information(self, "Compose", "Connect to a server first.")
            return
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit
        dlg = QDialog(self)
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
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        form.addRow(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Compose", "Project name is required.")
            return
        configs = [c.strip() for c in cfg_edit.text().split(",") if c.strip()]
        self.store.add_compose_project(self.current_id, {
            "name": name,
            "config_files": configs,
            "working_dir": wd_edit.text().strip(),
            "note": note_edit.text().strip(),
        })
        self._spawn(self.load_compose())

    def _unregister_compose_project(self, name: str):
        if not self.current_id:
            return
        self.store.remove_compose_project(self.current_id, name)
        self._spawn(self.load_compose())

    def _show_code_dialog(self, title: str, content: str, language: str = "json"):
        """Reusable viewer with syntax highlight + Copy button."""
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(820, 600)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(12, 12, 12, 12)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        mono = view.font()
        mono.setFamily("Consolas")
        mono.setStyleHint(mono.StyleHint.Monospace)
        view.setFont(mono)
        view.setPlainText(content)
        try:
            CodeHighlighter(view.document(), language)
        except Exception:
            pass
        lay.addWidget(view, 1)
        row = QHBoxLayout()
        row.addStretch()
        copy = QPushButton("Copy")
        if qta:
            try:
                theme = str(QApplication.instance().property("sshdockTheme") or "dark").lower()
                copy.setIcon(qta.icon("fa5s.copy", color="#475569" if theme == "light" else "#dbe7f5"))
            except Exception:
                pass
        copy.clicked.connect(lambda: QApplication.clipboard().setText(view.toPlainText()))
        close = QPushButton("Close")
        close.setProperty("accent", True)
        close.clicked.connect(dlg.accept)
        row.addWidget(copy)
        row.addWidget(close)
        lay.addLayout(row)
        dlg.exec()

    def _docker_config_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        page._layout_owner = lay
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(12)
        row = QHBoxLayout()
        t = QLabel("Docker Config"); t.setObjectName("heroTitle"); row.addWidget(t); row.addStretch()
        refresh = icon_button("Refresh", "fa5s.sync-alt", lambda: self._spawn(self.load_docker_config()), "Reload docker config")
        copy = icon_button("Copy", "fa5s.copy", lambda: QApplication.clipboard().setText(self.docker_config_view.toPlainText()), "Copy config")
        row.addWidget(refresh); row.addWidget(copy)
        lay.addLayout(row)
        hint = QLabel("daemon.json, docker info / version — highlighted when possible.")
        hint.setObjectName("muted"); lay.addWidget(hint)
        self.docker_config_view = QPlainTextEdit()
        self.docker_config_view.setReadOnly(True)
        mono = self.docker_config_view.font()
        mono.setFamily("Consolas")
        mono.setStyleHint(mono.StyleHint.Monospace)
        self.docker_config_view.setFont(mono)
        lay.addWidget(self.docker_config_view, 1)
        self._docker_config_hl = None
        return page

    async def load_docker_config(self):
        if not self.current_id:
            return
        ssh = self.manager.servers[self.current_id].ssh
        # Show a loading indicator immediately.
        self.docker_config_view.setPlainText("Loading Docker configuration…")
        # Run all 4 commands concurrently, bounded by the Settings worker limit.
        # Previously these were sequential, which meant 4 round-trips = ~4s
        # on a typical connection. Parallel = ~1s (slowest command wins).
        commands = [
            ("### /etc/docker/daemon.json", "cat /etc/docker/daemon.json 2>/dev/null || echo '(no /etc/docker/daemon.json)'", 20),
            ("### docker info", "docker info 2>/dev/null", 30),
            ("### docker version", "docker version 2>/dev/null", 20),
            ("### docker system df", "docker system df 2>/dev/null", 30),
        ]

        async def _fetch_one(header, cmd, timeout):
            try:
                r = await ssh.run_page(cmd, timeout=timeout)
                return header + '\n' + (r.stdout or r.stderr or '').strip()
            except Exception as exc:
                log.warning("Docker config command failed (%s): %s", cmd, exc)
                return header + f'\n(error: {exc})'

        # Gather all 4 concurrently. run_page() caps the actual in-flight
        # SSH commands to the configured worker count.
        results = await asyncio.gather(
            *[_fetch_one(h, c, t) for h, c, t in commands],
            return_exceptions=True,
        )
        blocks = [r if isinstance(r, str) else f"(error: {r})" for r in results]
        text_out = ('\n\n').join(blocks)
        self.docker_config_view.setPlainText(text_out)
        try:
            if getattr(self, '_docker_config_hl', None) is not None:
                self._docker_config_hl.setDocument(None)
            self._docker_config_hl = CodeHighlighter(self.docker_config_view.document(), 'yaml')
        except Exception:
            pass

    def _events_page(self):
        page = QWidget(); lay = QVBoxLayout(page)
        page._layout_owner = lay; lay.setContentsMargins(24, 20, 24, 24); lay.setSpacing(12)
        row = QHBoxLayout()
        t = QLabel("Docker Events"); t.setObjectName("heroTitle"); row.addWidget(t); row.addStretch()
        self.events_start = icon_button("Start", "fa5s.play", lambda: self._spawn(self.start_events()), "Start event stream")
        self.events_start.setProperty("accent", True)
        self.events_stop = icon_button("Stop", "fa5s.stop", lambda: self._spawn(self.stop_events()), "Stop stream")
        self.events_stop.setEnabled(False)
        clear = icon_button("Clear", "fa5s.eraser", lambda: self.events_output.clear(), "Clear output")
        copy = icon_button("Copy", "fa5s.copy", lambda: QApplication.clipboard().setText(self.events_output.toPlainText()), "Copy all events")
        for b in (self.events_start, self.events_stop, clear, copy):
            row.addWidget(b)
        lay.addLayout(row)
        self.events_output = QPlainTextEdit(); self.events_output.setReadOnly(True)
        mono = self.events_output.font(); mono.setFamily("Consolas"); mono.setStyleHint(mono.StyleHint.Monospace)
        self.events_output.setFont(mono)
        lay.addWidget(self.events_output, 1)
        return page

    def _server_page(self):
        page=QWidget(); lay=QVBoxLayout(page); page._layout_owner=lay; lay.setContentsMargins(24,20,24,24); lay.setSpacing(12)
        header=QHBoxLayout(); t=QLabel("Server"); t.setObjectName("heroTitle"); header.addWidget(t); header.addStretch()
        self.server_auto_refresh = self._register_table_auto_refresh(
            "server",
            header,
            self.refresh_server_page,
            page_name="Server",
        )
        refresh=QToolButton(); refresh.setText("Refresh"); refresh.setToolTip("Refresh server information"); refresh.clicked.connect(lambda _=False:self._spawn(self.refresh_server_page())); header.addWidget(refresh); lay.addLayout(header)
        self.server_tabs=QTabWidget()
        self.server_overview_table=StandardTableWidget(0,3); self.server_overview_table.setHorizontalHeaderLabels(["Metric","Value","Copy"]); self._prep_info_table(self.server_overview_table)
        self.server_overview_table.setColumnWidth(0,200); self.server_overview_table.setColumnWidth(1,400); self.server_overview_table.setColumnWidth(2,80)
        self._make_columns_resizable(self.server_overview_table, stretch_cols={1})
        self.server_overview_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.server_overview_table.customContextMenuRequested.connect(lambda pos:self._server_info_context(self.server_overview_table,pos))
        self.server_tabs.addTab(self.server_overview_table,"Overview")
        self.process_table=StandardTableWidget(0,7); self.process_table.setHorizontalHeaderLabels(["PID","User","CPU %","MEM %","State","Command","Actions"]); self._prep_info_table(self.process_table); self.process_table.setColumnWidth(0,80); self.process_table.setColumnWidth(1,120); self.process_table.setColumnWidth(2,75); self.process_table.setColumnWidth(3,75); self.process_table.setColumnWidth(4,90); self.process_table.setColumnWidth(5,280); self.process_table.setColumnWidth(6,115); self._make_columns_resizable(self.process_table, stretch_cols={5}); self.process_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.process_table.customContextMenuRequested.connect(self._process_context_menu); self.server_tabs.addTab(self.process_table,"Processes")
        self.service_table=StandardTableWidget(0,6); self.service_table.setHorizontalHeaderLabels(["Service","Load","Active","Sub","Description","Actions"]); self._prep_info_table(self.service_table); self.service_table.setColumnWidth(0,230); self.service_table.setColumnWidth(1,80); self.service_table.setColumnWidth(2,90); self.service_table.setColumnWidth(3,90); self.service_table.setColumnWidth(4,260); self.service_table.setColumnWidth(5,130); self._make_columns_resizable(self.service_table, stretch_cols={4}); self.service_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.service_table.customContextMenuRequested.connect(self._service_context_menu); self.server_tabs.addTab(self.service_table,"Services")

        files=QWidget(); fl=QVBoxLayout(files); fl.setContentsMargins(8,8,8,8)
        self.server_file_browser = RemoteFileBrowser(ServerFileBackend(lambda: self.manager.servers[self.current_id].ssh), "Server Files")
        fl.addWidget(self.server_file_browser, 1)
        self.server_tabs.addTab(files,"Files")
        lay.addWidget(self.server_tabs,1); self.server_tabs.currentChanged.connect(self._server_tab_changed); return page

    @contextmanager
    def _atomic_table(self, table: QTableWidget, key_columns: tuple[int, ...] = (0,)):
        selected = set()
        model = table.selectionModel()
        if model is not None:
            for index in model.selectedRows():
                selected.add(tuple(
                    (table.item(index.row(), c).text() if table.item(index.row(), c) else "")
                    for c in key_columns
                ))
        scroll = table.verticalScrollBar().value()
        old_updates = table.updatesEnabled()
        old_sorting = table.isSortingEnabled()
        old_signals = table.blockSignals(True)
        table.setUpdatesEnabled(False)
        table.setSortingEnabled(False)
        try:
            yield
            if selected:
                for row in range(table.rowCount()):
                    key = tuple(
                        (table.item(row, c).text() if table.item(row, c) else "")
                        for c in key_columns
                    )
                    if key in selected:
                        table.selectRow(row)
            table.verticalScrollBar().setValue(min(scroll, table.verticalScrollBar().maximum()))
        finally:
            table.blockSignals(old_signals)
            table.setSortingEnabled(old_sorting)
            table.setUpdatesEnabled(old_updates)
            table.viewport().update()
            table.update()

    def _make_columns_resizable(self, table, stretch_cols: set[int] | None = None):
        """Make every column of *table* user-resizable (drag the header dividers).

        All columns are set to ``Interactive`` mode — the only QHeaderView
        mode that lets the user drag column borders. (``Stretch`` and
        ``ResizeToContents`` auto-size the column and ignore user drags.)

        The last section is NOT stretched (``setStretchLastSection(False)``)
        so the user can freely resize even the last column. When the total
        column width exceeds the viewport, a horizontal scrollbar appears.

        ``stretch_cols`` is kept for API compatibility but no longer changes
        the resize *mode* — it only gives those columns a slightly wider
        initial width so they look like "the main column" on first paint.
        """
        h = table.horizontalHeader()
        h.setSectionsMovable(False)
        h.setStretchLastSection(False)
        h.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # Show a horizontal scrollbar when columns don't fit, instead of
        # forcing the last column to stretch and become non-resizable.
        try:
            table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        except Exception:
            pass
        stretch_cols = stretch_cols or set()
        h.setMinimumSectionSize(40)
        for c in range(table.columnCount()):
            # Interactive = user can drag. Every other mode (Stretch,
            # ResizeToContents, Fixed) prevents user dragging.
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            # For "stretch" columns, give a generous initial width so they
            # look like the main column on first paint — but still allow
            # the user to resize them afterwards.
            if c in stretch_cols:
                cur = table.columnWidth(c)
                if cur < 200:
                    table.setColumnWidth(c, 200)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def _container_checkbox(self, table, row):
        host = table.cellWidget(row, 0) if table is not None and 0 <= row < table.rowCount() else None
        if host is None:
            return None
        return host.findChild(QCheckBox)

    def _deselect_table(self, table):
        table.clearSelection()
        for r in range(table.rowCount()):
            cb = self._container_checkbox(table, r)
            if cb is not None:
                cb.blockSignals(True); cb.setChecked(False); cb.blockSignals(False)
                continue
            item = table.item(r, 0)
            if item is not None and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                item.setCheckState(Qt.CheckState.Unchecked)
        table.viewport().update()

    def _check_all_table(self, table, checked: bool = True) -> None:
        """Check/uncheck selectable resource rows, supporting both item and widget checkboxes."""
        table.blockSignals(True)
        try:
            for r in range(table.rowCount()):
                cb = self._container_checkbox(table, r)
                if cb is not None:
                    cb.blockSignals(True); cb.setChecked(checked); cb.blockSignals(False)
                else:
                    item = table.item(r, 0)
                    if item is not None and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                if checked:
                    table.selectRow(r)
            if not checked:
                table.clearSelection()
        finally:
            table.blockSignals(False)
        table.viewport().update()

    def _sync_selection_to_checks(self, table) -> None:
        """Mark row checkboxes for currently selected rows."""
        selected = {i.row() for i in table.selectionModel().selectedRows()}
        table.blockSignals(True)
        try:
            for r in range(table.rowCount()):
                cb = self._container_checkbox(table, r)
                if cb is not None:
                    cb.blockSignals(True); cb.setChecked(r in selected); cb.blockSignals(False)
                    continue
                item = table.item(r, 0)
                if item is not None and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                    item.setCheckState(Qt.CheckState.Checked if r in selected else Qt.CheckState.Unchecked)
        finally:
            table.blockSignals(False)
        table.viewport().update()

    def _prep_info_table(self, table):
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(38)
        table.setWordWrap(False)

    def _server_info_context(self, table, pos):
        item=table.itemAt(pos)
        if not item:return
        row=item.row(); vals=[table.item(row,c).text() if table.item(row,c) else "" for c in range(table.columnCount())]
        menu=QMenu(self); a=menu.addAction("Copy value"); b=menu.addAction("Copy row"); c=menu.addAction("Copy metric"); chosen=menu.exec(table.viewport().mapToGlobal(pos))
        if chosen is a: QApplication.clipboard().setText(vals[1] if len(vals)>1 else vals[0])
        elif chosen is b: QApplication.clipboard().setText("\t".join(vals[:2]))
        elif chosen is c: QApplication.clipboard().setText(vals[0])

    def _process_context_menu(self,pos):
        item=self.process_table.itemAt(pos)
        if not item:return
        row=item.row()
        pid_item=self.process_table.item(row,0)
        pid=pid_item.text() if pid_item else ""
        menu=QMenu(self)
        kill=menu.addAction("Terminate (TERM)")
        force=menu.addAction("Kill (KILL)")
        menu.addSeparator()
        cp_cell=menu.addAction("Copy cell")
        cp=menu.addAction("Copy row")
        cp_sel=menu.addAction("Copy selected rows")
        cp_all=menu.addAction("Copy all rows")
        menu.addSeparator()
        sel_all=menu.addAction("Select all")
        chosen=menu.exec(self.process_table.viewport().mapToGlobal(pos))
        if chosen is kill:
            self._spawn(self._manage_process(pid,"TERM"))
        elif chosen is force:
            self._spawn(self._manage_process(pid,"KILL"))
        elif chosen is cp_cell:
            col=self.process_table.columnAt(pos.x())
            it=self.process_table.item(row, col if col>=0 else 0)
            QApplication.clipboard().setText(it.text() if it else "")
        elif chosen is cp:
            QApplication.clipboard().setText(self._table_row_text(self.process_table, row))
        elif chosen is cp_sel:
            rows=sorted({i.row() for i in self.process_table.selectedIndexes()}) or [row]
            QApplication.clipboard().setText("\n".join(self._table_row_text(self.process_table, r) for r in rows))
        elif chosen is cp_all:
            headers=[self.process_table.horizontalHeaderItem(c).text() if self.process_table.horizontalHeaderItem(c) else "" for c in range(self.process_table.columnCount())]
            lines=[self._table_row_text(self.process_table, r) for r in range(self.process_table.rowCount())]
            QApplication.clipboard().setText("\t".join(headers)+"\n"+"\n".join(lines))
        elif chosen is sel_all:
            self.process_table.selectAll()

    def _service_context_menu(self,pos):
        item=self.service_table.itemAt(pos)
        if not item:return
        row=item.row()
        unit_item=self.service_table.item(row,0)
        unit=unit_item.text() if unit_item else ""
        menu=QMenu(self)
        for label,act in [("Start","start"),("Stop","stop"),("Restart","restart")]:
            a=menu.addAction(label); a.setData(("svc", act))
        menu.addSeparator()
        a_name=menu.addAction("Copy unit name"); a_name.setData(("copy","name"))
        a_row=menu.addAction("Copy row"); a_row.setData(("copy","row"))
        a_sel=menu.addAction("Copy selected rows"); a_sel.setData(("copy","sel"))
        a_all=menu.addAction("Copy all rows"); a_all.setData(("copy","all"))
        chosen=menu.exec(self.service_table.viewport().mapToGlobal(pos))
        if not chosen:
            return
        data=chosen.data()
        if not data:
            return
        kind, val = data
        if kind=="svc":
            self._spawn(self._manage_service(unit, val))
        elif kind=="copy":
            if val=="name":
                QApplication.clipboard().setText(unit)
            elif val=="row":
                QApplication.clipboard().setText(self._table_row_text(self.service_table, row))
            elif val=="sel":
                rows=sorted({i.row() for i in self.service_table.selectedIndexes()}) or [row]
                QApplication.clipboard().setText("\n".join(self._table_row_text(self.service_table, r) for r in rows))
            elif val=="all":
                headers=[self.service_table.horizontalHeaderItem(c).text() if self.service_table.horizontalHeaderItem(c) else "" for c in range(self.service_table.columnCount())]
                lines=[self._table_row_text(self.service_table, r) for r in range(self.service_table.rowCount())]
                QApplication.clipboard().setText("\t".join(headers)+"\n"+"\n".join(lines))

    async def _manage_process(self,pid,signal):
        if not self.current_id:return
        sig={"TERM":"-TERM","KILL":"-KILL"}[signal]
        await self.manager.servers[self.current_id].ssh.run(f"kill {sig} {shlex.quote(pid)}",timeout=10); await self._load_processes()

    async def _manage_service(self,unit,action):
        if not self.current_id:return
        r=await self.manager.servers[self.current_id].ssh.run(f"systemctl {action} {shlex.quote(unit)}",timeout=30); self.statusBar().showMessage((r.stdout or r.stderr or f"{unit}: {action}")[-500:],5000); await self._load_services()

    def _server_tab_changed(self, index):
        if getattr(self, "server_tabs", None) is None or index < 0:
            return
        tab = self.server_tabs.tabText(index)
        if not self.current_id:
            return
        if tab == "Files":
            self.server_file_browser.refresh()
        elif tab == "Processes":
            # Processes are intentionally lazy: don't spend an SSH slot on a
            # comparatively expensive sampler until the user actually opens
            # the tab. Use a fast one-shot ps snapshot for immediate feedback.
            self._spawn(self._load_processes(), name="Load server processes")
        elif tab == "Services":
            self._spawn(self._load_services(), name="Load server services")

    def _logs_page(self):
        page=QWidget(); lay=QVBoxLayout(page);
        page._layout_owner = lay; lay.setContentsMargins(24,20,24,24); row=QHBoxLayout()
        t=QLabel("Host logs"); t.setObjectName("heroTitle"); row.addWidget(t); row.addStretch()
        refresh = icon_button("Refresh", "fa5s.sync-alt", lambda: self._spawn(self.load_host_logs()), "Refresh logs")
        copy = icon_button("Copy", "fa5s.copy", lambda: QApplication.clipboard().setText(self.host_logs.toPlainText()), "Copy logs")
        row.addWidget(refresh); row.addWidget(copy); lay.addLayout(row)
        self.host_logs=QPlainTextEdit(); self.host_logs.setReadOnly(True)
        mono = self.host_logs.font(); mono.setFamily("Consolas"); mono.setStyleHint(mono.StyleHint.Monospace)
        self.host_logs.setFont(mono)
        lay.addWidget(self.host_logs,1); return page

    def _settings_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        page._layout_owner = outer
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: 0; }")
        outer.addWidget(scroll, 1)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(14)
        scroll.setWidget(body)

        title = QLabel("Settings")
        title.setObjectName("heroTitle")
        lay.addWidget(title)
        text = QLabel(
            "Workspace, keyboard shortcuts and SSH preferences. "
            "Secrets are delegated to OS credential storage when persistence is enabled."
        )
        text.setWordWrap(True)
        text.setObjectName("muted")
        lay.addWidget(text)

        # ---- Appearance (first) ----
        prefs = self.store.load_ui_prefs()
        appear = QFrame()
        appear.setObjectName("panel")
        ap = QVBoxLayout(appear)
        ap.setContentsMargins(16, 14, 16, 14)
        ap.setSpacing(12)
        at = QLabel("Appearance")
        at.setObjectName("sectionTitle")
        ap.addWidget(at)

        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.addItem("Light", "light")
        cur_theme = str(prefs.get("theme") or "dark").lower()
        self.theme_combo.setCurrentIndex(1 if cur_theme == "light" else 0)
        self.theme_combo.setMinimumWidth(140)
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch()
        ap.addLayout(theme_row)

        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Terminal font"))
        self.term_font_combo = QComboBox()
        self.term_font_combo.setEditable(False)
        self.term_font_combo.setMinimumWidth(220)
        for fam in self._terminal_font_choices():
            self.term_font_combo.addItem(fam)
            self.term_font_combo.setItemData(
                self.term_font_combo.count() - 1, QFont(fam, 12), Qt.ItemDataRole.FontRole
            )
        saved_font = str(prefs.get("terminal_font_family") or "Consolas")
        fidx = self.term_font_combo.findText(saved_font, Qt.MatchFlag.MatchFixedString)
        if fidx < 0:
            self.term_font_combo.addItem(saved_font)
            fidx = self.term_font_combo.findText(saved_font)
        self.term_font_combo.setCurrentIndex(max(0, fidx))
        font_row.addWidget(self.term_font_combo)
        font_row.addWidget(QLabel("Size"))
        self.term_font_size = QSpinBox()
        self.term_font_size.setRange(9, 28)
        self.term_font_size.setValue(int(prefs.get("terminal_font_size") or 13))
        font_row.addWidget(self.term_font_size)
        font_row.addStretch()
        ap.addLayout(font_row)
        font_hint = QLabel(
            "Pick a monospace font for the console. Suggestions include common coding fonts on this system."
        )
        font_hint.setObjectName("muted")
        font_hint.setWordWrap(True)
        ap.addWidget(font_hint)

        ai_title = QLabel("Terminal intelligence")
        ai_title.setObjectName("sectionTitle")
        ap.addWidget(ai_title)
        ai_hint = QLabel(
            "Local online-learning assistance. It learns from this terminal's command "
            "history and from suggestions you explicitly accept; secret-like entries "
            "are filtered before learning."
        )
        ai_hint.setObjectName("muted")
        ai_hint.setWordWrap(True)
        ap.addWidget(ai_hint)

        self.terminal_ai_shadow_cb = QCheckBox("Show AI command shadow while typing")
        self.terminal_ai_shadow_cb.setChecked(
            bool(prefs.get("terminal_ai_shadow_enabled", True))
        )
        ap.addWidget(self.terminal_ai_shadow_cb)

        self.terminal_ai_suggestions_cb = QCheckBox(
            "Enable Ctrl+Space AI suggestion palette"
        )
        self.terminal_ai_suggestions_cb.setChecked(
            bool(prefs.get("terminal_ai_suggestions_enabled", True))
        )
        ap.addWidget(self.terminal_ai_suggestions_cb)

        self.terminal_ai_history_cb = QCheckBox(
            "Learn from remote shell history when connecting"
        )
        self.terminal_ai_history_cb.setChecked(
            bool(prefs.get("terminal_ai_learn_history", True))
        )
        ap.addWidget(self.terminal_ai_history_cb)

        self.terminal_ai_persist_cb = QCheckBox(
            "Persist learned command patterns on this machine"
        )
        self.terminal_ai_persist_cb.setChecked(
            bool(prefs.get("terminal_ai_persist_learning", True))
        )
        ap.addWidget(self.terminal_ai_persist_cb)

        reset_ai_btn = QPushButton("Reset learned terminal model")
        reset_ai_btn.setObjectName("ghostBtnDanger")
        reset_ai_btn.setToolTip(
            "Forget learned command patterns and accepted-suggestion signals."
        )
        reset_ai_btn.clicked.connect(self._reset_terminal_learning)
        ap.addWidget(reset_ai_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self.notify_cb = QCheckBox("Enable notification bell for Docker / SSH events")
        self.notify_cb.setChecked(bool(prefs.get("notifications_enabled", True)))
        ap.addWidget(self.notify_cb)

        shell_row = QHBoxLayout()
        shell_row.addWidget(QLabel("SSH worker shells"))
        self.worker_shell_count = QSpinBox()
        self.worker_shell_count.setRange(1, 32)
        self.worker_shell_count.setValue(max(1, min(32, int(prefs.get("worker_shell_count", 3)))))
        shell_row.addWidget(self.worker_shell_count)
        shell_hint = QLabel("Persistent SSH shells kept warm for background data/command work.")
        shell_hint.setObjectName("muted")
        shell_row.addWidget(shell_hint, 1)
        ap.addLayout(shell_row)

        self.remote_agent_cb = QCheckBox(
            "Enable accelerated remote agent for background commands"
        )
        self.remote_agent_cb.setChecked(bool(prefs.get("remote_agent_enabled", False)))
        self.remote_agent_cb.setToolTip(
            "Uploads a temporary agent after SSH authentication. It listens only on "
            "127.0.0.1 and is reached through an encrypted SSH tunnel."
        )
        ap.addWidget(self.remote_agent_cb)
        agent_hint = QLabel(
            "Uses a temporary loopback-only RPC agent to reduce per-command SSH "
            "channel overhead. It is removed when the connection closes."
        )
        agent_hint.setObjectName("muted")
        agent_hint.setWordWrap(True)
        ap.addWidget(agent_hint)

        proxy_panel = QFrame()
        proxy_panel.setObjectName("proxyPanel")
        pp = QVBoxLayout(proxy_panel)
        pp.setContentsMargins(0, 4, 0, 0)
        pp.setSpacing(6)
        proxy_title = QLabel("SSH Proxy")
        proxy_title.setObjectName("sectionTitle")
        pp.addWidget(proxy_title)
        self.proxy_settings = ProxySettingsWidget(
            enabled_label="Enable proxy for SSH connections by default"
        )
        self.proxy_settings.set_config(self.store.load_proxy_settings())
        pp.addWidget(self.proxy_settings)
        ap.addWidget(proxy_panel)

        apply_btn = QPushButton("Apply appearance & SSH")
        apply_btn.setProperty("accent", True)
        apply_btn.clicked.connect(self._save_and_apply_ui_prefs)
        ap.addWidget(apply_btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(appear)

        # ---- Keyboard shortcuts ----
        shortcuts_panel = QFrame()
        shortcuts_panel.setObjectName("panel")
        sp = QVBoxLayout(shortcuts_panel)
        sp.setContentsMargins(16, 14, 16, 14)
        sp.setSpacing(8)
        st = QLabel("Keyboard shortcuts")
        st.setObjectName("sectionTitle")
        sp.addWidget(st)
        desc = QLabel("Active in the main window. Scroll the table to see all shortcuts.")
        desc.setObjectName("muted")
        sp.addWidget(desc)

        self.shortcuts_table = StandardTableWidget(0, 2)
        self.shortcuts_table.setHorizontalHeaderLabels(["Action", "Shortcut"])
        self.shortcuts_table.verticalHeader().setVisible(False)
        self.shortcuts_table.setAlternatingRowColors(True)
        self.shortcuts_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.shortcuts_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.shortcuts_table.setColumnWidth(0, 320)
        self.shortcuts_table.setColumnWidth(1, 180)
        self._make_columns_resizable(self.shortcuts_table, stretch_cols={0})
        self.shortcuts_table.verticalHeader().setDefaultSectionSize(32)
        # ~10 rows visible; rest via table scrollbar
        self.shortcuts_table.setMinimumHeight(32 * 10 + 36)
        self.shortcuts_table.setMaximumHeight(32 * 10 + 36)
        sp.addWidget(self.shortcuts_table)
        lay.addWidget(shortcuts_panel)
        self._populate_shortcuts_table()

        # ---- Background tasks ----
        tasks_panel = QFrame()
        tasks_panel.setObjectName("panel")
        tp = QVBoxLayout(tasks_panel)
        tp.setContentsMargins(16, 14, 16, 14)
        tp.setSpacing(8)
        tt = QHBoxLayout()
        tt.addWidget(QLabel("Background tasks"))
        tt.addStretch()
        refresh_tasks = QPushButton("↻ Refresh")
        refresh_tasks.clicked.connect(self._refresh_task_table)
        tt.addWidget(refresh_tasks)
        cancel_all = QPushButton("Cancel running")
        cancel_all.clicked.connect(self._cancel_all_tasks_from_ui)
        tt.addWidget(cancel_all)
        tp.addLayout(tt)
        self.task_table = StandardTableWidget(0, 5)
        self.task_table.setHorizontalHeaderLabels(["Task", "Status", "Duration", "Error", "Action"])
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.task_table.setColumnWidth(0, 200)
        self.task_table.setColumnWidth(1, 100)
        self.task_table.setColumnWidth(2, 90)
        self.task_table.setColumnWidth(3, 200)
        self.task_table.setColumnWidth(4, 100)
        self._make_columns_resizable(self.task_table, stretch_cols={0, 3})
        self.task_table.verticalHeader().setDefaultSectionSize(36)
        self.task_table.setMinimumHeight(36 * 10 + 36)
        self.task_table.setMaximumHeight(36 * 10 + 36)
        tp.addWidget(self.task_table)
        lay.addWidget(tasks_panel)

        self._task_refresh_timer = QTimer(self)
        self._task_refresh_timer.timeout.connect(self._refresh_task_table)
        self._task_refresh_timer.start(1000)

        note = QLabel(
            "Terminal sessions can also be created, renamed and closed from the terminal panel toolbar. "
            "Ctrl+W closes only the selected terminal session."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)


        # ---- Export / Import ----
        io = QFrame(); io.setObjectName("panel")
        iol = QVBoxLayout(io); iol.setContentsMargins(16, 14, 16, 14); iol.setSpacing(10)
        it = QLabel("Backup"); it.setObjectName("sectionTitle"); iol.addWidget(it)
        iol.addWidget(QLabel("Export or import profiles, registered Compose projects, and UI preferences."))
        brow = QHBoxLayout()
        exp = QPushButton("Export…"); exp.clicked.connect(self._export_settings_bundle)
        imp = QPushButton("Import…"); imp.clicked.connect(self._import_settings_bundle)
        brow.addWidget(exp); brow.addWidget(imp); brow.addStretch()
        iol.addLayout(brow)
        lay.addWidget(io)

        lay.addStretch(1)
        return page

    def _populate_shortcuts_table(self):
        table = getattr(self, "shortcuts_table", None)
        if table is None:
            return
        labels = {
            "terminal.toggle": "Toggle terminal panel",
            "terminal.new": "New terminal session",
            "terminal.close": "Close selected terminal",
            "terminal.next": "Next terminal session",
            "terminal.previous": "Previous terminal session",
            "terminal.find": "Find in terminal",
            "terminal.clear": "Clear terminal screen",
            "page.overview": "Go to Overview",
            "page.containers": "Go to Containers",
            "page.images": "Go to Images",
            "page.storage": "Go to Storage",
            "page.compose": "Go to Compose",
            "page.events": "Go to Events",
            "page.config": "Go to Docker Config",
            "page.server": "Go to Server",
            "page.logs": "Go to Logs",
            "page.settings": "Go to Settings",
            "refresh": "Refresh current page",
            "server.add": "Add server",
            "server.reconnect": "Reconnect current server",
        }
        table.setRowCount(0)
        # Prefer label order, then any extra registered actions
        order = list(labels.keys())
        seen = set()
        rows = []
        for name in order:
            action = self._shortcut_actions.get(name)
            if action:
                rows.append((labels[name], action))
                seen.add(name)
        for name, action in self._shortcut_actions.items():
            if name not in seen:
                rows.append((labels.get(name, name), action))
        for label, action in rows:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, QTableWidgetItem(label))
            seq = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            if not seq:
                # Fallback for explicitly-bound keys
                seq = action.shortcut().toString() or "—"
            item = QTableWidgetItem(seq)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(r, 1, item)
            table.setRowHeight(r, 32)

    def _refresh_task_table(self):
        table = getattr(self, "task_table", None)
        if table is None:
            return
        import time
        records = self.task_manager.snapshot()
        table.setRowCount(0)
        table.verticalHeader().setDefaultSectionSize(36)
        for rec in records:
            row = table.rowCount()
            table.insertRow(row)
            status = rec.live_status() if hasattr(rec, "live_status") else rec.status
            if rec.finished_at is None:
                duration = time.monotonic() - rec.started_at
            else:
                duration = rec.finished_at - rec.started_at
            name_item = QTableWidgetItem(rec.name)
            status_item = QTableWidgetItem(status)
            # Colorize status
            color = {
                "running": "#41d17d",
                "running (long)": "#ffca4b",
                "cancelling": "#ffca4b",
                "completed": "#8a95a4",
                "cancelled": "#8a95a4",
                "failed": "#ff939b",
            }.get(status, "#e9edf2")
            status_item.setForeground(QColor(color))
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, status_item)
            table.setItem(row, 2, QTableWidgetItem(f"{duration:.1f}s"))
            table.setItem(row, 3, QTableWidgetItem(rec.error or ""))
            table.setRowHeight(row, 36)
            if not rec.task.done():
                wrap = QWidget()
                hl = QHBoxLayout(wrap)
                hl.setContentsMargins(4, 2, 4, 2)
                hl.setSpacing(4)
                cancel_btn = QPushButton("Cancel")
                cancel_btn.setFixedHeight(26)
                cancel_btn.clicked.connect(lambda _=False, tid=rec.task_id: self._cancel_task_id(tid))
                force_btn = QPushButton("Force")
                force_btn.setFixedHeight(26)
                force_btn.setProperty("danger", True)
                force_btn.setToolTip("Cancel and remove from the list immediately")
                force_btn.clicked.connect(lambda _=False, tid=rec.task_id: self._force_drop_task_id(tid))
                hl.addWidget(cancel_btn)
                hl.addWidget(force_btn)
                table.setCellWidget(row, 4, wrap)
            else:
                drop = QPushButton("Dismiss")
                drop.setFixedHeight(26)
                drop.clicked.connect(lambda _=False, tid=rec.task_id: self._force_drop_task_id(tid))
                wrap = QWidget()
                hl = QHBoxLayout(wrap)
                hl.setContentsMargins(4, 2, 4, 2)
                hl.addWidget(drop)
                hl.addStretch()
                table.setCellWidget(row, 4, wrap)

    def _cancel_task_id(self, task_id: int):
        self.task_manager.cancel(task_id)
        self._refresh_task_table()

    def _force_drop_task_id(self, task_id: int):
        self.task_manager.force_drop(task_id)
        self._refresh_task_table()

    def _cancel_all_tasks_from_ui(self):
        for rec in list(self.task_manager.active):
            self.task_manager.cancel(rec.task_id)
        self._refresh_task_table()

    def _runner_page(self):
        return QWidget()


    def _toggle_sidebar(self):
        self._sidebar_collapsed = not getattr(self, "_sidebar_collapsed", False)
        collapsed = self._sidebar_collapsed
        self.sidebar.setFixedWidth(self._sidebar_collapsed_w if collapsed else self._sidebar_expanded_w)
        # Brand / server card / section labels
        if hasattr(self, "brand"):
            self.brand.setVisible(not collapsed)
        if hasattr(self, "brand_sub"):
            self.brand_sub.setVisible(not collapsed)
        if hasattr(self, "server_card"):
            self.server_card.setVisible(not collapsed)
        for hdr in getattr(self, "_nav_section_labels", []):
            hdr.setVisible(not collapsed)
        # Nav buttons icon-only
        for btn in self._nav_buttons.values():
            if hasattr(btn, "setCollapsed"):
                btn.setCollapsed(collapsed)
        # Disconnect: icon-only when collapsed
        if collapsed:
            self.disconnect_side.setText("")
            self.disconnect_side.setToolTip("Disconnect")
        else:
            self.disconnect_side.setText("Disconnect")
            self.disconnect_side.setToolTip("Disconnect from current server")
        try:
            if qta:
                icon_name = "fa5s.angle-double-right" if collapsed else "fa5s.bars"
                self.sidebar_toggle.setProperty("sshdockIconName", icon_name)
                self.sidebar_toggle.setProperty("sshdockIconRole", "normal")
                self.sidebar_toggle.setIcon(
                    qta.icon(icon_name, color=self._ui_icon_color("normal"))
                )
        except Exception:
            pass

    def select_page(self, name: str):
        if name not in self.pages:
            return
        # Remember where we were before opening the full Terminal tab
        if name == "Terminal":
            current = self.stack.currentWidget()
            for n, w in self.pages.items():
                if w is current and n != "Terminal":
                    self._page_before_terminal = n
                    break
            else:
                self._page_before_terminal = getattr(self, "_page_before_terminal", "Overview")
            self._attach_terminal_to_page()
        else:
            self._detach_terminal_to_panel()
        self.title.setText(name)
        self.stack.setCurrentWidget(self.pages[name])
        for n, b in self._nav_buttons.items():
            b.setChecked(n == name)
        if self.current_id and name != "Terminal":
            self._table_action_menu.dismiss()
            previous = getattr(self, "_page_refresh_task", None)
            if previous is not None and not previous.done():
                previous.cancel()
            self._page_refresh_task = self._spawn(
                self.refresh_current_page(show_progress=True),
                name=f"Load page: {name}",
            )

    def _attach_terminal_to_page(self):
        """Make terminal a full tab: move panel into the Terminal page host."""
        host = getattr(self, "_terminal_page_host", None)
        lay = getattr(self, "_terminal_page_layout", None)
        if host is None or lay is None:
            return
        # Remove placeholder
        ph = getattr(self, "_terminal_page_placeholder", None)
        if ph is not None:
            lay.removeWidget(ph)
            ph.hide()
        # Pull terminal_panel out of the splitter into the page
        if self.terminal_panel.parent() is not host:
            self.terminal_panel.setParent(None)
            lay.addWidget(self.terminal_panel, 1)
        self.terminal_panel.setMinimumHeight(0)
        self.terminal_panel.setMaximumHeight(16777215)
        self.terminal_panel.show()
        self._terminal_mode = "page"
        self._terminal_panel_expanded = True
        self._sync_terminal_chrome(True)
        self._refresh_terminal_selector()
        if not self.terms and self.current_id:
            self._spawn(self._create_terminal())
        else:
            QTimer.singleShot(40, self._nudge_terminal_display)

    def _ensure_terminal_in_splitter(self):
        """Make sure the terminal panel is a child of the bottom splitter."""
        if not hasattr(self, "main_splitter"):
            return
        if self.terminal_panel.parent() is self.main_splitter:
            return
        self.terminal_panel.setParent(None)
        self.main_splitter.addWidget(self.terminal_panel)
        # Restore placeholder on the Terminal page host
        lay = getattr(self, "_terminal_page_layout", None)
        ph = getattr(self, "_terminal_page_placeholder", None)
        if lay is not None and ph is not None:
            # Avoid duplicate adds
            if ph.parent() is not self._terminal_page_host:
                lay.addWidget(ph)
            ph.show()

    def _detach_terminal_to_panel(self):
        """Restore terminal panel into the bottom splitter and hide it (leave full tab)."""
        was_page = getattr(self, "_terminal_mode", "") == "page"
        self._ensure_terminal_in_splitter()
        if was_page or getattr(self, "_terminal_mode", "") == "page":
            self.terminal_panel.hide()
            self._terminal_mode = "hidden"
            self._terminal_panel_expanded = False
            self._sync_terminal_chrome(False)

    def _spawn(self,coro, name: str = "Background task"):
        task = self.task_manager.create(coro, name)
        if task is None:
            return None
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self,task):
        self._tasks.discard(task)
        try: task.result()
        except asyncio.CancelledError: pass
        except Exception as exc:
            log.exception("Background task failed")
            self.statusBar().showMessage(str(exc),7000)

    def _show_page_spinner(self, show: bool, page: QWidget | None = None):
        page = page or self.stack.currentWidget()
        if page is None:
            return
        spinner = getattr(page, "_page_spinner", None)
        if spinner is None:
            spinner = QFrame(page); spinner.setObjectName("inlineLoading")
            sl = QHBoxLayout(spinner); sl.setContentsMargins(10, 6, 10, 6); sl.setSpacing(8)
            bar = QProgressBar(); bar.setRange(0, 0); bar.setTextVisible(False); bar.setFixedHeight(4); bar.setMinimumWidth(140)
            label = QLabel("Loading…"); label.setObjectName("muted")
            sl.addWidget(bar, 0); sl.addWidget(label, 1)
            page._page_spinner = spinner
            layout = page.layout()
            if layout is not None:
                layout.insertWidget(0, spinner)
        spinner.setVisible(show)

    async def refresh_current_page(self, *, show_progress: bool = False):
        if not self.current_id:
            return
        name=self.title.text()
        mapping={"Overview":self.refresh_overview,"Containers":self.load_containers,"Stats":self.load_container_stats,"Images":self.load_images,"Storage":self.refresh_storage,"Compose":self.load_compose,"Server":self.refresh_server_page,"Logs":self.load_host_logs,"Config":self.load_docker_config,"Events":None}
        fn=mapping.get(name)
        if not fn:
            return

        # Full-page progress is for navigation/initial loads only. Routine
        # manual/auto refreshes update the table in place without flashing it.
        page = self.stack.currentWidget()
        if show_progress and page is not None:
            depth = int(getattr(page, "_page_spinner_depth", 0)) + 1
            page._page_spinner_depth = depth
            self._show_page_spinner(True, page)

        self.refresh_btn.setEnabled(False)
        try:
            await fn()
        except Exception as exc:
            log.exception("Page refresh failed: %s", name)
            self.statusBar().showMessage(f"{name} failed: {exc}", 8000)
            if name == "Containers" and hasattr(self, "container_table"):
                self.container_table.setRowCount(0)
        finally:
            if show_progress and page is not None:
                depth = max(0, int(getattr(page, "_page_spinner_depth", 1)) - 1)
                page._page_spinner_depth = depth
                self._show_page_spinner(depth > 0, page)
            self._set_connection_buttons(bool(self.current_id and self.manager.servers.get(self.current_id) and self.manager.servers[self.current_id].ssh.is_connected))

    def _refresh_server_picker(self):
        self.server_picker.blockSignals(True)
        self.server_picker.clear()
        for sid, r in self.manager.servers.items():
            p = r.profile
            name = (p.name or p.alias or p.host or sid or "server").strip()
            host = (p.host or "").strip()
            user = (getattr(p, "username", None) or "").strip()
            # Full readable label — not elided in the popup
            if user and host:
                label = f"{name}  ({user}@{host})"
            elif host and host != name:
                label = f"{name}  ({host})"
            else:
                label = name
            self.server_picker.addItem(f"●  {label}", sid)
            idx = self.server_picker.count() - 1
            self.server_picker.setItemData(idx, label, Qt.ItemDataRole.ToolTipRole)
        # Widen to fit longest label (capped)
        fm = self.server_picker.fontMetrics()
        max_w = 260
        for i in range(self.server_picker.count()):
            max_w = max(max_w, fm.horizontalAdvance(self.server_picker.itemText(i)) + 40)
        self.server_picker.setMinimumWidth(min(max_w, 420))
        self.server_picker.blockSignals(False)
        if self.current_id:
            idx=self.server_picker.findData(self.current_id)
            if idx>=0: self.server_picker.setCurrentIndex(idx)

    def _is_current_server_generation(self, server_id: str, generation: int) -> bool:
        return bool(
            server_id
            and self.current_id == server_id
            and self._server_generation == generation
            and server_id in self.manager.servers
        )

    def _server_selected(self,index):
        sid=self.server_picker.itemData(index)
        if sid and sid!=self.current_id:
            self._server_generation += 1
            self.current_id=sid; self._update_identity()
            previous = getattr(self, "_page_refresh_task", None)
            if previous is not None and not previous.done():
                previous.cancel()
            self._page_refresh_task = self._spawn(self.refresh_overview(), name="Load page: Overview")

    def _update_identity(self):
        if not self.current_id or self.current_id not in self.manager.servers:
            self.server_name.setText("No server connected"); self.server_details.setText("Connect an SSH server to begin"); return
        p=self.manager.servers[self.current_id].profile; self.server_name.setText(p.name or p.alias or p.host); self.server_details.setText(f"{p.username}@{p.host or p.alias}:{p.port}")

    def _manager_state(self,pid,state):
        previous = self._server_ui_states.get(pid)
        self._server_ui_states[pid] = state
        if pid!=self.current_id:return
        connected=state=="connected"; reconnecting=state=="reconnecting" or state=="connecting"
        self.state.setObjectName("stateConnected" if connected else "stateReconnecting" if reconnecting else "stateDisconnected"); self.state.style().unpolish(self.state); self.state.style().polish(self.state); self.state.setText("● Connected" if connected else "● Reconnecting…" if reconnecting else "● Disconnected")
        self._set_connection_buttons(connected or reconnecting)
        if connected and previous == "reconnecting":
            self._spawn(self.refresh_current_page(), name="Refresh after SSH reconnect")

    def _set_connection_state(self,connected): self._manager_state(self.current_id, "connected" if connected else "disconnected")

    def _set_connection_buttons(self,active):
        self.disconnect_btn.setEnabled(bool(self.current_id and active)); self.disconnect_side.setEnabled(bool(self.current_id and active)); self.reconnect_btn.setEnabled(bool(self.current_id and not active)); self.refresh_btn.setEnabled(bool(self.current_id and active))

    def add_server(self):
        """Open the SSH connection dialog and add/connect the selected server."""
        self.connect_dialog()

    async def add_server_and_connect(self, profile, auth=None):
        """Register a profile with the session manager and establish its SSH connection."""
        if profile.id not in self.manager.servers:
            self.manager.add_server(profile, auth)
        self.current_id = profile.id
        self._refresh_server_picker()
        self._update_identity()
        self._manager_state(self.current_id, "connecting")
        try:
            await self.manager.connect(profile.id)
            self._update_identity()
            self._refresh_server_picker()
            self._manager_state(self.current_id, "connected")
            await self.refresh_overview()
        except Exception:
            self._manager_state(self.current_id, "disconnected")
            raise

    def connect_dialog(self):
        from app.ssh.discovery import discover_aliases
        from app.ui.dialogs import ConnectionDialog
        dialog = ConnectionDialog(self.profiles, discover_aliases(), parent=self, store=self.store)
        if dialog.exec() == ConnectionDialog.DialogCode.Accepted and dialog.result_profile:
            # Keep local profile list in sync if user deleted profiles in the dialog
            self.profiles = list(dialog.profiles)
            from app.ssh.client import AuthMaterial
            auth = AuthMaterial(
                password=dialog.password.text() or None,
                passphrase=dialog.passphrase.text() or None,
            )
            self._spawn(self._connect(dialog.result_profile, auth))

    def _on_app_notification(self, level: str, title: str, message: str) -> None:
        prefs = self.store.load_ui_prefs()
        if not prefs.get("notifications_enabled", True):
            return
        # Bell popup holds the full history; also mirror every event in the
        # footer status bar so Compose actions have immediate visible feedback.
        if level in {"success", "error", "warning", "info"}:
            try:
                self.statusBar().showMessage(f"{title}: {message}"[:140], 6000)
            except Exception:
                pass


    @staticmethod
    def _terminal_font_choices() -> list[str]:
        """Return known-good terminal fonts without scanning legacy Windows bitmap fonts."""
        return [
            "Cascadia Mono", "Cascadia Code", "JetBrains Mono", "Fira Code",
            "Fira Mono", "Source Code Pro", "IBM Plex Mono", "Hack",
            "Consolas", "Menlo", "Monaco", "DejaVu Sans Mono",
            "Liberation Mono", "Ubuntu Mono", "Noto Sans Mono",
            "Roboto Mono", "Inconsolata", "SF Mono", "monospace",
        ]


    def _apply_ui_prefs(self) -> None:
        prefs = self.store.load_ui_prefs()
        theme = prefs.get("theme") or "dark"
        try:
            app = QApplication.instance()
            if app is not None:
                app.setProperty("sshdockTheme", str(theme).lower())
                app.setStyleSheet(theme_stylesheet(theme))
        except Exception:
            pass
        # Rebuild theme-sensitive QtAwesome icons and navigation labels.
        try:
            for btn in self.findChildren(QAbstractButton):
                icon_name = btn.property("sshdockIconName")
                if qta is not None and icon_name:
                    role = str(btn.property("sshdockIconRole") or "normal")
                    btn.setIcon(qta.icon(str(icon_name), color=self._ui_icon_color(role, theme)))
            for nav in self._nav_buttons.values():
                if hasattr(nav, "set_theme"):
                    nav.set_theme(theme)
            # Refresh only widgets which own dynamic/theme-sensitive assets.
            theme_targets = [
                getattr(self, "server_file_browser", None),
                getattr(self, "terminal_file_browser", None),
                getattr(self, "_resource_monitor_dialog", None),
            ]
            for dialog in getattr(self, "_container_file_dialogs", set()):
                theme_targets.append(dialog)
            for widget in theme_targets:
                apply_theme = getattr(widget, "apply_theme", None)
                if callable(apply_theme):
                    apply_theme(theme)
        except Exception:
            log.debug("Could not refresh theme icons", exc_info=True)

        self._terminal_ai_shadow_enabled = bool(
            prefs.get("terminal_ai_shadow_enabled", True)
        )
        self._terminal_ai_suggestions_enabled = bool(
            prefs.get("terminal_ai_suggestions_enabled", True)
        )
        self._terminal_ai_learn_history = bool(
            prefs.get("terminal_ai_learn_history", True)
        )
        self._terminal_ai_persist_learning = bool(
            prefs.get("terminal_ai_persist_learning", True)
        )
        for page in getattr(self, "terminal_widgets", {}).values():
            if HAS_WEBENGINE:
                for widget in page.findChildren(XtermTerminalWidget):
                    try:
                        widget.set_terminal_ai_settings(
                            shadow_enabled=self._terminal_ai_shadow_enabled,
                            suggestions_enabled=self._terminal_ai_suggestions_enabled,
                        )
                    except Exception:
                        pass

        family = str(prefs.get("terminal_font_family") or "Consolas")
        size = int(prefs.get("terminal_font_size") or 13)
        font = QFont(family, size)
        targets: list = []
        for page in getattr(self, "terminal_widgets", {}).values():
            try:
                if HAS_WEBENGINE:
                    targets.extend(page.findChildren(XtermTerminalWidget))
                # also pyte / generic terminals
                from app.terminal.emulator import TerminalWidget as _PyteTerm
                targets.extend(page.findChildren(_PyteTerm))
            except Exception:
                pass
            if not targets and hasattr(page, "set_font"):
                targets.append(page)
        # De-dupe by id
        seen = set()
        uniq = []
        for w in targets:
            wid = id(w)
            if wid not in seen:
                seen.add(wid)
                uniq.append(w)
        if not uniq and HAS_WEBENGINE:
            try:
                uniq = list(self.findChildren(XtermTerminalWidget))
            except Exception:
                pass
        for w in uniq:
            try:
                if hasattr(w, "apply_theme"):
                    w.apply_theme(theme)
                if hasattr(w, "set_font"):
                    w.set_font(font)
                elif hasattr(w, "setFont"):
                    w.setFont(font)
            except Exception:
                pass

    def _reset_terminal_learning(self) -> None:
        reply = QMessageBox.question(
            self,
            "Reset terminal model",
            "Forget learned terminal history, accepted suggestions and online-learning weights?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._command_predictor.clear()
        try:
            self.store.clear_terminal_learning_state()
        except Exception:
            pass
        self.statusBar().showMessage("Terminal AI learning model reset.", 3500)

    def _reset_terminal_learning(self) -> None:
        reply = QMessageBox.question(
            self,
            "Reset terminal model",
            "Forget learned terminal history, accepted suggestions and ranking weights?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._command_predictor.clear()
        try:
            self.store.clear_terminal_learning_state()
        except Exception:
            pass
        self.statusBar().showMessage("Terminal AI learning model reset.", 3500)

    def _ui_icon_color(self, role: str = "normal", theme: str | None = None) -> str:
        name = str(theme or self.store.load_ui_prefs().get("theme") or "dark").lower()
        if role == "danger":
            return "#b91c1c" if name == "light" else "#ff939b"
        if role == "accent":
            return "#ffffff"
        return "#475569" if name == "light" else "#dbe7f5"

    def _save_and_apply_ui_prefs(self) -> None:
        prefs = self.store.load_ui_prefs()
        prefs["theme"] = self.theme_combo.currentData() or ("light" if "light" in self.theme_combo.currentText().lower() else "dark")
        prefs["terminal_font_family"] = (self.term_font_combo.currentText() or "Consolas").strip()
        prefs["terminal_font_size"] = int(self.term_font_size.value())
        prefs["notifications_enabled"] = self.notify_cb.isChecked()
        worker_count = int(self.worker_shell_count.value())
        prefs["worker_shell_count"] = worker_count
        agent_enabled = self.remote_agent_cb.isChecked()
        proxy_error = self.proxy_settings.validation_error()
        if proxy_error:
            QMessageBox.warning(self, "Proxy configuration", proxy_error)
            return
        proxy_settings = self.proxy_settings.config()
        prefs["remote_agent_enabled"] = agent_enabled
        prefs["proxy"] = proxy_settings.to_dict()
        prefs["terminal_ai_shadow_enabled"] = self.terminal_ai_shadow_cb.isChecked()
        prefs["terminal_ai_suggestions_enabled"] = self.terminal_ai_suggestions_cb.isChecked()
        prefs["terminal_ai_learn_history"] = self.terminal_ai_history_cb.isChecked()
        prefs["terminal_ai_persist_learning"] = self.terminal_ai_persist_cb.isChecked()
        prefs["terminal_ai_max_suggestions"] = 5
        self._terminal_ai_shadow_enabled = prefs["terminal_ai_shadow_enabled"]
        self._terminal_ai_suggestions_enabled = prefs["terminal_ai_suggestions_enabled"]
        self._terminal_ai_learn_history = prefs["terminal_ai_learn_history"]
        self._terminal_ai_persist_learning = prefs["terminal_ai_persist_learning"]
        self.store.save_ui_prefs(prefs)
        if self._terminal_ai_persist_learning:
            self._persist_terminal_predictor()
        # Apply the scheduler limit synchronously so page loaders immediately
        # use the new value; resize the persistent shell pool asynchronously.
        self.manager.set_worker_shell_count_limit(worker_count)
        self._spawn(self.manager.set_worker_shell_count(worker_count), name="Resize SSH worker pool")
        self.manager.remote_agent_enabled = agent_enabled
        self._spawn(
            self.manager.set_remote_agent_enabled(agent_enabled),
            name="Update remote agent",
        )
        self._apply_ui_prefs()
        self.statusBar().showMessage(
            f"SSH worker limit applied: {worker_count}; "
            f"remote agent {'enabled' if agent_enabled else 'disabled'}; "
            f"SSH proxy {'enabled' if proxy_settings.enabled else 'disabled'}",
            4000,
        )

    def _export_settings_bundle(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export SSHDock data", "sshdock-backup.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            self.store.export_bundle(Path(path))
            NotificationBus.instance().success("Export complete", path)
            QMessageBox.information(self, "Export", f"Saved to:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def _import_settings_bundle(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import SSHDock data", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            counts = self.store.import_bundle(Path(path), merge=True)
            self.profiles = self.store.load_profiles()
            self._apply_ui_prefs()
            NotificationBus.instance().success(
                "Import complete",
                f"{counts.get('profiles', 0)} profiles, {counts.get('compose', 0)} compose",
            )
            QMessageBox.information(
                self, "Import",
                f"Imported {counts.get('profiles', 0)} profiles and "
                f"{counts.get('compose', 0)} compose entries (merged).",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Import failed", str(exc))


    async def _connect(self,profile,auth=None):
        self.current_id=profile.id; self.manager.add_server(profile,auth); self._update_identity(); self._refresh_server_picker(); self.state.setText("● Connecting…")
        try: await self.manager.connect(profile.id); await self.refresh_overview(); self._set_connection_state(True)
        except Exception as exc:
            self._set_connection_state(False)
            ErrorDialog("Connection failed", "Could not establish the SSH session.", str(exc), self).exec()

    async def disconnect_current(self):
        if self.current_id: await self.manager.disconnect(self.current_id); self._set_connection_state(False); self.statusBar().showMessage("Disconnected")

    async def reconnect_current(self):
        if self.current_id:
            self._manager_state(self.current_id,"reconnecting")
            try: await self.manager.reconnect(self.current_id); await self.refresh_overview()
            except Exception as exc:
                ErrorDialog("Reconnect failed", "Could not restore the SSH session.", str(exc), self).exec()

    async def refresh_overview(self):
        if not self.current_id:return
        ssh=self.manager.servers[self.current_id].ssh; docker=DockerManager(ssh)
        # Use the delta-based sampling script for accurate instantaneous CPU%.
        # Use a 0.5s interval for the overview (faster refresh, still accurate
        # enough for a dashboard glance). The Server page uses 1.0s for more
        # precise readings.
        async def processes():
            from app.docker.stats_math import build_top_script, parse_top_output
            script = build_top_script(sample_interval=0.5, max_procs=20)
            try:
                r = await ssh.run_page(script, timeout=15)
                text = r.stdout or ""
                if parse_top_output(text):
                    return text
                raise RuntimeError("top sampler returned no parseable process rows")
            except Exception as exc:
                log.warning("Process sampling failed (overview top), falling back to ps: %s", exc)
                try:
                    r = await ssh.run_page(
                        "ps -eo pid=,user=,%cpu=,%mem=,stat=,args= --sort=-%cpu 2>/dev/null | head -20",
                        timeout=10,
                    )
                    # Convert ps rows into the same parser-friendly TSV format.
                    lines = []
                    for line in (r.stdout or "").splitlines():
                        parts = line.strip().split(None, 5)
                        if len(parts) < 6:
                            continue
                        pid, user, cpu, mem, state, cmd = parts
                        lines.append(f"{pid}\t{user}\t{cpu}\t{mem}\t{state}\t{cmd}")
                    return "__PS_FALLBACK__\n" + "\n".join(lines)
                except Exception as exc2:
                    log.warning("Overview ps fallback failed: %s", exc2)
                    return ""
        # Use return_exceptions=True so one failing task doesn't cancel
        # the others. Previously, if the process-sampling script timed
        # out, asyncio.gather would cancel ServerMonitor.snapshot() mid-
        # flight, causing "RuntimeError: Cannot enter into task while
        # another task is being executed" because the SSH worker shell
        # was already dead.
        results = await asyncio.gather(
            docker.detect(),
            ServerMonitor(ssh).snapshot(),
            processes(),
            return_exceptions=True,
        )
        # Unpack safely.
        detect_result = results[0]
        mon_result = results[1]
        proc = results[2]
        # Handle detect result.
        if isinstance(detect_result, Exception):
            dok, dr = False, None
        else:
            dok, dr = detect_result
        # Handle monitor result.
        if isinstance(mon_result, Exception):
            mon = {}
        else:
            mon = mon_result
        # Handle process result.
        if isinstance(proc, Exception):
            proc = ""
        self.cards["docker"].setText(dr.stdout.strip() if dok and dr else "Unavailable")
        self.cards["host"].setText(mon.get("hostname","—"))
        self.cards["cpu"].setText(f"{mon.get('cpu_percent','—')}% · load {mon.get('load_short','—')}")
        self.cards["memory"].setText(f"{mon.get('memory_percent','—')}% · {mon.get('memory_used','—')}/{mon.get('memory_total','—')}")
        self.cards["disk"].setText(mon.get("disk","—"))
        try:
            container_count = str(len(await docker.ps())) if dok else "—"
        except Exception:
            container_count = "—"
        self.cards["containers"].setText(container_count)
        self._render_overview_processes(proc)
        self.statusBar().showMessage("Dashboard refreshed",3000)

    def _render_overview_processes(self,text):
        """Render top output, or the normalized ps fallback, into the overview table."""
        from app.docker.stats_math import parse_top_output
        rows = []
        if text.startswith("__PS_FALLBACK__\n"):
            for line in text.splitlines()[1:]:
                parts = line.split("\t", 5)
                if len(parts) != 6:
                    continue
                pid, user, cpu, mem, state, cmd = parts
                try: cpu_val = float(cpu)
                except ValueError: cpu_val = None
                try: mem_val = float(mem)
                except ValueError: mem_val = None
                rows.append({"pid": pid, "user": user, "cpu_pct": cpu_val, "mem_pct": mem_val, "state": state, "command": cmd})
        else:
            rows = parse_top_output(text)
        with self._atomic_table(self.overview_processes, (0,)):
            self.overview_processes.setRowCount(0)
            for row in rows[:20]:
                pid = str(row.get("pid") or "")
                user = str(row.get("user") or "")
                cpu_val = row.get("cpu_pct")
                cpu = f"{cpu_val:.2f}" if cpu_val is not None else "—"
                mem_val = row.get("mem_pct")
                mem = f"{mem_val:.2f}" if mem_val is not None else "—"
                state = str(row.get("state") or "")
                cmd = str(row.get("command") or "")
                r=self.overview_processes.rowCount(); self.overview_processes.insertRow(r)
                for c,val in enumerate([pid, user, cpu, mem, state, cmd]):
                    item = QTableWidgetItem(val)
                    # Colour-code CPU%: green < 30%, yellow < 70%, red >= 70%.
                    if c == 2 and cpu_val is not None:
                        if cpu_val >= 70:
                            item.setForeground(QColor("#ff5260"))
                        elif cpu_val >= 30:
                            item.setForeground(QColor("#ffca4b"))
                        else:
                            item.setForeground(QColor("#39d47a"))
                    self.overview_processes.setItem(r, c, item)

        self.overview_proc_meta.setText(f"{len(rows)} processes · sorted by CPU (instantaneous)")

    async def load_containers(self):
        if not self.current_id:
            return
        from app.services.containers import ContainerService
        from app.docker.manager import DockerManager
        svc = ContainerService(DockerManager(self.manager.servers[self.current_id].ssh))
        try:
            infos = await svc.list_containers(all_=True)
        except Exception as exc:
            log.warning("Container list fetch failed: %s", exc)
            self.statusBar().showMessage(f"Could not load containers: {exc}", 8000)
            return
        rows = [
            {
                "ID": c.id,
                "Names": c.name,
                "Name": c.name,
                "Image": c.image,
                "Status": c.status,
                "State": c.state,
                "Ports": c.ports,
                **(c.raw or {}),
            }
            for c in infos
        ]
        self._container_rows = rows
        self._render_containers(rows)
        self.statusBar().showMessage(f"{len(rows)} containers", 2500)

    def _render_containers(self, rows):
        page = getattr(self, "containers_page", None)
        if page is not None:
            page.set_rows(rows or [])
            return
        # Fallback if page not built yet
        self.container_table.setRowCount(0)


    def _actions_cell(self, entries: list, tip: str = "Actions") -> QWidget:
        """Build a refresh-safe, consistently styled Actions cell."""
        icon = None
        if qta is not None:
            try:
                icon = qta.icon("fa5s.ellipsis-h", color=self._ui_icon_color())
            except Exception:
                pass
        return create_table_actions_cell(
            self._table_action_menu,
            entries,
            tooltip=tip,
            icon=icon,
        )


    def _action_button(self, text, fn, icon_name=None, tooltip=None):
        icon_map={"Start":"fa5s.play","Stop":"fa5s.stop","Restart":"fa5s.redo","Logs":"fa5s.file-alt","Terminal":"fa5s.terminal","Remove":"fa5s.trash","Inspect":"fa5s.search","Files":"fa5s.folder-open","Hide":"fa5s.chevron-down","New":"fa5s.plus","New Terminal":"fa5s.terminal","Rename":"fa5s.pen","Expand":"fa5s.expand-alt","Clear":"fa5s.eraser","Close":"fa5s.times"}
        icon_name = icon_name or icon_map.get(text, "fa5s.circle")
        b=QPushButton(); b.setObjectName("subtleAction"); b.setFixedSize(32,28); b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        b.setToolTip(tooltip or text)
        b.setAccessibleName(text)
        b.setProperty("sshdockIconName", icon_name)
        b.setProperty("sshdockIconRole", "normal")
        if qta is not None:
            try:b.setIcon(qta.icon(icon_name, color=self._ui_icon_color()))
            except Exception: pass
        else:
            b.setText(text[:1])
        b.clicked.connect(fn); return b

    def _containers_context_menu(self, pos):
        row=self.container_table.rowAt(pos.y())
        if row < 0: return
        name_item=self.container_table.item(row,2)
        if not name_item: return
        name=name_item.text(); raw=self._container_rows_for_name(name)
        cid=(raw or {}).get("ID", "") or name_item.data(Qt.ItemDataRole.UserRole) or ""
        image=self.container_table.item(row,3).text() if self.container_table.item(row,3) else ""
        menu=QMenu(self)
        a_id=menu.addAction("Copy container ID"); a_name=menu.addAction("Copy container name"); a_image=menu.addAction("Copy image"); a_details=menu.addAction("Copy container details")
        a_row=menu.addAction("Copy row"); a_sel=menu.addAction("Copy selected rows"); a_all=menu.addAction("Copy all rows")
        menu.addSeparator()
        a_inspect=menu.addAction("Inspect"); a_logs=menu.addAction("Logs"); a_stats=menu.addAction("Stats")
        a_files=menu.addAction("Files"); a_shell=menu.addAction("Open shell")
        menu.addSeparator(); a_restart=menu.addAction("Restart"); a_remove=menu.addAction("Remove")
        menu.addSeparator()
        a_sel_all=menu.addAction("Select all")
        a_check_all=menu.addAction("Check all")
        a_uncheck=menu.addAction("Uncheck all")
        act=menu.exec(self.container_table.viewport().mapToGlobal(pos))
        if act==a_id: QApplication.clipboard().setText(cid)
        elif act==a_name: QApplication.clipboard().setText(name)
        elif act==a_image: QApplication.clipboard().setText(image)
        elif act==a_details: self._copy_container_details(name)
        elif act==a_row: QApplication.clipboard().setText(self._table_row_text(self.container_table, row))
        elif act==a_sel:
            rows=sorted({i.row() for i in self.container_table.selectedIndexes()}) or [row]
            QApplication.clipboard().setText("\n".join(self._table_row_text(self.container_table, r) for r in rows))
        elif act==a_all:
            headers=[self.container_table.horizontalHeaderItem(c).text() if self.container_table.horizontalHeaderItem(c) else "" for c in range(self.container_table.columnCount())]
            body="\n".join(self._table_row_text(self.container_table, r) for r in range(self.container_table.rowCount()))
            QApplication.clipboard().setText("\t".join(headers)+"\n"+body)
        elif act==a_sel_all:
            self.container_table.selectAll()
            self._sync_selection_to_checks(self.container_table)
            self.container_selected.setText(str(self.container_table.rowCount()))
        elif act==a_check_all:
            self._check_all_table(self.container_table, True)
            self.container_selected.setText(str(self.container_table.rowCount()))
        elif act==a_uncheck:
            self._check_all_table(self.container_table, False)
            self.container_selected.setText("0")
        elif act==a_shell: self._open_container_terminal(name)
        elif act==a_files: self._open_container_files(name)
        elif act==a_inspect: self._open_container_inspect(name)
        elif act==a_logs: self._show_container_logs(name)
        elif act==a_stats: self._open_container_stats(name)
        elif act==a_restart: self._spawn(self.container_action_exec("restart",name))
        elif act==a_remove: self._spawn(self.container_action_exec("rm",name))

    def _container_rows_for_name(self,name):
        for item in getattr(self,"_container_rows",[]):
            if item.get("Names")==name or item.get("Name")==name:
                return item
        return None

    def _table_context_menu(self, table, pos):
        """Context menu for resource tables (images / volumes / networks)."""
        item = table.itemAt(pos)
        row = table.rowAt(pos.y())
        col = table.columnAt(pos.x())
        menu = QMenu(table)
        cell = menu.addAction("Copy cell")
        rowa = menu.addAction("Copy row")
        sels = menu.addAction("Copy selected rows")
        all_rows = menu.addAction("Copy all rows")
        menu.addSeparator()
        sel_all = menu.addAction("Select all")
        # Resource-specific bulk actions
        rm_sel = None
        if table is getattr(self, "volume_table", None):
            rm_sel = menu.addAction("Remove selected volumes")
        elif table is getattr(self, "network_table", None):
            rm_sel = menu.addAction("Remove selected networks")
        elif table is getattr(self, "image_table", None):
            rm_sel = menu.addAction("Remove selected images")
        act = menu.exec(table.viewport().mapToGlobal(pos))
        if act is None:
            return
        if act is cell and item is not None:
            QApplication.clipboard().setText(item.text())
        elif act is rowa and row >= 0:
            QApplication.clipboard().setText(self._table_row_text(table, row))
        elif act is sels:
            rows = sorted({i.row() for i in table.selectedIndexes()})
            if not rows and row >= 0:
                rows = [row]
            QApplication.clipboard().setText("\n".join(self._table_row_text(table, r) for r in rows))
        elif act is all_rows:
            headers = []
            for c in range(table.columnCount()):
                h = table.horizontalHeaderItem(c)
                headers.append(h.text() if h else f"col{c}")
            body = "\n".join(self._table_row_text(table, r) for r in range(table.rowCount()))
            QApplication.clipboard().setText("\t".join(headers) + "\n" + body)
        elif act is sel_all:
            table.selectAll()
            self._check_all_table(table, True)
        elif act is rm_sel:
            if table is getattr(self, "volume_table", None):
                self._spawn(self._bulk_remove_volumes())
            elif table is getattr(self, "network_table", None):
                self._spawn(self._bulk_remove_networks())
            elif table is getattr(self, "image_table", None):
                self._spawn(self._bulk_remove_images())

    def _filter_containers(self,*_): self._render_containers(getattr(self,"_container_rows",[]))
    async def container_action_exec(self,action,name):
        name = (name or "").strip().lstrip("/")
        if not name or name in {"False", "True", "None"}:
            ErrorDialog("Docker action failed", "Invalid container name.", repr(name), self).exec()
            return
        if action == "rm":
            dlg = ConfirmRemoveDialog([name], self)
            if dlg.exec() != ConfirmRemoveDialog.DialogCode.Accepted:
                return
            result = await DockerManager(self.manager.servers[self.current_id].ssh).remove(name, force=bool(dlg.force))
        else:
            result = await DockerManager(self.manager.servers[self.current_id].ssh).action(action, name)
        if result.exit_code != 0:
            from app.docker.errors import classify_docker_result
            classified = classify_docker_result(result)
            details = (result.stderr or result.stdout or classified.message or f"docker {action} failed").strip()
            details = f"$ {result.command}\n\n[{classified.kind.value}] {details}"
            ErrorDialog(
                "Docker action failed",
                f"Could not {action} container «{name}».",
                details,
                self,
            ).exec()
        await self.load_containers()

    async def _bulk_selected(self):
        """Checked boxes first; if none, use highlighted rows (name col 2)."""
        names: list[str] = []
        seen: set[str] = set()
        for r in range(self.container_table.rowCount()):
            cell = self.container_table.item(r, 0)
            if cell and cell.checkState() == Qt.CheckState.Checked:
                n = cell.data(Qt.ItemDataRole.UserRole) or (
                    self.container_table.item(r, 2).text() if self.container_table.item(r, 2) else ""
                )
                n = str(n or "").strip()
                if n and n not in seen:
                    names.append(n); seen.add(n)
        if names:
            return names
        for idx in self.container_table.selectionModel().selectedRows():
            r = idx.row()
            name_item = self.container_table.item(r, 2)
            n = (name_item.text() if name_item else "").strip()
            if n and n not in seen:
                names.append(n); seen.add(n)
        return names


    def _update_container_selection_count(self) -> None:
        n = 0
        for r in range(self.container_table.rowCount()):
            cell = self.container_table.item(r, 0)
            if cell and cell.checkState() == Qt.CheckState.Checked:
                n += 1
        if n == 0:
            n = len(self.container_table.selectionModel().selectedRows())
        self.container_selected.setText(str(n))

    def _bulk_action_start(self): self._spawn(self._bulk_do("start"))
    def _bulk_action_stop(self): self._spawn(self._bulk_do("stop"))
    def _bulk_action_restart(self): self._spawn(self._bulk_do("restart"))
    def _bulk_action_remove(self): self._spawn(self._bulk_do("rm"))
    async def _bulk_do(self, action):
        names = await self._bulk_selected()
        if not names:
            return
        force = False
        if action == "rm":
            confirm = ConfirmRemoveDialog(names, self)
            if confirm.exec() != ConfirmRemoveDialog.DialogCode.Accepted:
                return
            force = confirm.force
        # One window with a tab per container
        job = MultiTabJobDialog(f"Container {action} — {len(names)} selected", self)
        for n in names:
            job.add_tab(n)
        job.show()
        job.raise_()
        dm = DockerManager(self.manager.servers[self.current_id].ssh)
        for n in names:
            job.set_running(n)
            try:
                if action == "rm":
                    r = await dm.remove(n, force=force)
                else:
                    r = await dm.action(action, n)
                out = (r.stdout or r.stderr or "").strip() or f"exit {r.exit_code}"
                job.append(n, f"$ {r.command}")
                job.append(n, out)
                job.mark_tab_done(n, ok=(r.exit_code == 0))
            except Exception as exc:
                job.append(n, f"[error] {exc}")
                job.mark_tab_done(n, ok=False)
        await self.load_containers()

    def _copy_container_details(self,name):
        row=next((r for r in range(self.container_table.rowCount()) if self.container_table.item(r,2) and self.container_table.item(r,2).text()==name),None)
        if row is None:return
        values=[self.container_table.item(row,c).text() if self.container_table.item(row,c) else "" for c in (2,3,4,5)]
        QApplication.clipboard().setText("\n".join(values)); self.statusBar().showMessage("Container details copied",2500)

    def _open_container_details(self):
        row=self.container_table.currentRow(); name=self.container_table.item(row,2).text() if row>=0 else None
        if name:self._spawn(self.show_container_details(name))
    async def show_container_details(self, name, initial_tab: str = "Inspect"):
        if not self.current_id:
            return
        name = str(name or "").strip().lstrip("/")
        if "," in name:
            name = name.split(",", 1)[0].strip()
        if not name or name in {"False", "True", "None"}:
            ErrorDialog("Container", "Invalid container name.", repr(name), self).exec()
            return
        ssh = self.manager.servers[self.current_id].ssh
        dlg = ContainerDetailsWindow(self, ssh, name, initial_tab=initial_tab)
        dlg.show()

    def _show_container_logs(self, name):
        self._spawn(self.show_container_details(name, initial_tab="Logs"))

    def _open_container_stats(self, name):
        self._spawn(self.show_container_details(name, initial_tab="Stats"))

    def _open_container_inspect(self, name):
        self._spawn(self.show_container_details(name, initial_tab="Inspect"))

    def _open_container_terminal(self, name, shell: str = "sh"):
        """Attach app terminal via `docker exec -it <name> <shell>` over SSH."""
        exe = (shell or "sh").strip() or "sh"
        self._spawn(self._create_terminal(
            title=f"{name} shell",
            command=["docker", "exec", "-it", str(name), exe],
        ))

    def _open_container_files(self,name):
        self._spawn(self.show_container_details(name, initial_tab="Files"))

    async def _container_files_dialog(self, name):
        if not self.current_id:
            return
        backend = ContainerFileBackend(self.manager.servers[self.current_id].ssh, name)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Files — {name}")
        dlg.resize(1180, 760)
        layout=QVBoxLayout(dlg); layout.setContentsMargins(12,12,12,12)
        browser=RemoteFileBrowser(backend, f"Container Files — {name}")
        layout.addWidget(browser)
        dlg.show()
        browser.refresh()
        # Keep the dialog alive by storing it on itself/parent until closed.
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._container_file_dialogs=getattr(self,'_container_file_dialogs',set())
        self._container_file_dialogs.add(dlg)
        dlg.destroyed.connect(lambda *_: self._container_file_dialogs.discard(dlg))


    def open_terminal(self):
        """Force-show the bottom terminal panel (create session if needed)."""
        self.toggle_terminal_panel(True)

    def toggle_terminal_panel(self, force: bool | None = None):
        """VS Code-style bottom terminal.

        - If hidden → show panel (create a session if none exists)
        - If visible → hide panel
        - Full Terminal page: hide leaves the page and collapses the panel
        """
        in_page = getattr(self, "_terminal_mode", "") == "page"
        if force is None:
            if in_page:
                self._leave_terminal_page("hidden")
                return
            show = not self.terminal_panel.isVisible()
        else:
            show = bool(force)
            if in_page and not show:
                self._leave_terminal_page("hidden")
                return
            if in_page and show:
                return
        if show:
            if not self.current_id:
                QMessageBox.information(self, "No server", "Connect to an SSH server before opening a terminal.")
                return
            self._apply_terminal_mode("panel")
            self._refresh_terminal_selector()
            QTimer.singleShot(30, self._nudge_terminal_display)
            # Create first session only when the panel is opened and empty
            if (
                not self.terms
                and not getattr(self, "_auto_terminal_creation", False)
                and not getattr(self, "_terminal_creation_in_progress", False)
            ):
                self._auto_terminal_creation = True
                task = self._spawn(self._create_terminal())
                if task is not None:
                    task.add_done_callback(lambda _t: setattr(self, "_auto_terminal_creation", False))
                else:
                    self._auto_terminal_creation = False
        else:
            self._apply_terminal_mode("hidden")

    def _apply_terminal_mode(self, mode: str):
        """Modes: hidden | panel | page (full Terminal tab). Size is free via splitter drag."""
        if getattr(self, "_terminal_mode", "") == "page" and mode not in ("page",):
            self._ensure_terminal_in_splitter()

        self._terminal_mode = mode
        if mode == "hidden":
            self.terminal_panel.hide()
            self._terminal_panel_expanded = False
            self.stack.show()
            self._sync_terminal_chrome(False)
            return

        if mode == "page":
            self._attach_terminal_to_page()
            return

        # Bottom panel: free drag from ~120px up to ~92% of the content area.
        self._ensure_terminal_in_splitter()
        self.terminal_panel.setMinimumHeight(120)
        self.terminal_panel.setMaximumHeight(16777215)  # no artificial cap
        self.stack.setMinimumHeight(48)  # allow main area to shrink so terminal can grow
        self.terminal_panel.show()
        self._terminal_panel_expanded = True
        self.stack.show()
        sizes = self.main_splitter.sizes()
        total = sum(sizes) if sizes else self.main_splitter.height()
        # Default open size ~55%; user can drag far beyond half
        if total > 0 and (not sizes or sizes[-1] < 80):
            QTimer.singleShot(0, lambda: self._resize_terminal_panel(0.55, min_panel=180, max_panel=None, min_stack=64))
        # Both sides stretch so dragging past half is smooth when the window resizes
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setCollapsible(0, False)
        self.main_splitter.setCollapsible(1, False)
        self._sync_terminal_chrome(True)



    def _resize_terminal_panel(self, fraction: float, min_panel: int = 160, max_panel: int | None = None, min_stack: int = 64):
        """Resize bottom terminal panel to a fraction of the splitter height."""
        total = self.main_splitter.height()
        if total <= 0:
            QTimer.singleShot(30, lambda: self._resize_terminal_panel(fraction, min_panel, max_panel, min_stack))
            return
        # Allow terminal up to ~92% — only keep a thin strip of the main page
        min_stack = max(48, min_stack)
        fraction = max(0.10, min(0.92, fraction))
        panel_h = int(total * fraction)
        panel_h = max(min_panel, panel_h)
        if max_panel is not None:
            panel_h = min(panel_h, max_panel)
        panel_h = min(panel_h, max(min_panel, total - min_stack))
        stack_h = max(min_stack, total - panel_h)
        self.main_splitter.setSizes([stack_h, panel_h])
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 1)

    def _on_terminal_splitter_moved(self, pos: int, index: int):
        """Keep drag professional: clamp so neither side collapses to unusable."""
        if getattr(self, "_terminal_mode", "") not in ("panel", "docked", "normal", "maximized"):
            return
        if not self.terminal_panel.isVisible():
            return
        total = self.main_splitter.height()
        if total <= 0:
            return
        sizes = self.main_splitter.sizes()
        if len(sizes) < 2:
            return
        stack_h, panel_h = sizes[0], sizes[1]
        min_panel = 120
        min_stack = 48
        max_panel = max(min_panel, int(total * 0.92))  # drag up to ~92% of content height
        changed = False
        if panel_h < min_panel:
            panel_h = min_panel
            stack_h = total - panel_h
            changed = True
        elif panel_h > max_panel:
            panel_h = max_panel
            stack_h = total - panel_h
            changed = True
        elif stack_h < min_stack:
            stack_h = min_stack
            panel_h = total - stack_h
            changed = True
        if changed:
            self.main_splitter.blockSignals(True)
            self.main_splitter.setSizes([stack_h, panel_h])
            self.main_splitter.blockSignals(False)

    def _sync_terminal_chrome(self, visible: bool):
        """Update footer toggle label / badge when panel visibility changes."""
        if hasattr(self, "terminal_toggle"):
            self.terminal_toggle.setText("Hide Terminal" if visible else "Terminal")
            self.terminal_toggle.setToolTip("Hide terminal panel" if visible else "Open terminal panel")
        if hasattr(self, "terminal_shortcut_badge"):
            self.terminal_shortcut_badge.setVisible(not visible)
        if hasattr(self, "terminal_state"):
            if visible and self.terms:
                self.terminal_state.setText(f"{len(self.terms)} session(s)")
            elif visible:
                self.terminal_state.setText("Terminal open")
            else:
                self.terminal_state.setText("No active terminal")

    def _leave_terminal_page(self, panel_mode: str = "panel"):
        """Exit full Terminal tab; panel_mode is 'panel' or 'hidden'."""
        prev = getattr(self, "_page_before_terminal", "Overview")
        if prev not in self.pages or prev == "Terminal":
            prev = "Overview"
        self._ensure_terminal_in_splitter()
        self._terminal_mode = "hidden"
        if panel_mode == "hidden":
            self._apply_terminal_mode("hidden")
        else:
            self._apply_terminal_mode("panel")
        self.title.setText(prev)
        self.stack.setCurrentWidget(self.pages[prev])
        for n, b in self._nav_buttons.items():
            b.setChecked(n == prev)
        if self.current_id and panel_mode != "hidden":
            self._spawn(self.refresh_current_page())

    def _terminal_dock(self):
        # Kept as alias for any residual callers — just show free panel
        if getattr(self, "_terminal_mode", "") == "page":
            self._leave_terminal_page("panel")
            return
        self._apply_terminal_mode("panel")

    def _terminal_maximize(self):
        if getattr(self, "_terminal_mode", "") != "page":
            self.select_page("Terminal")

    def _terminal_restore(self):
        if getattr(self, "_terminal_mode", "") == "page":
            self._leave_terminal_page("panel")
            return
        self._apply_terminal_mode("panel")


    def _nudge_terminal_display(self):
        """After show/reparent: focus terminal; ask xterm to fit (or pyte to resize)."""
        term = self._current_terminal_widget()
        if term is None:
            return
        try:
            term.setFocus(Qt.FocusReason.OtherFocusReason)
            # xterm.js
            if hasattr(term, "_run_js"):
                term._run_js("window.sshdockTerm && window.sshdockTerm.fit && window.sshdockTerm.fit();")
                return
            # pyte fallback
            if hasattr(term, "_recompute_metrics"):
                term._recompute_metrics()
                cols, rows = term._grid_size()
                prev = getattr(term, "_last_grid", None)
                if prev == (cols, rows):
                    return
                term._last_grid = (cols, rows)
                if term._screen is not None and hasattr(term._screen, "resize"):
                    try:
                        term._screen.resize(rows, cols)
                    except Exception:
                        pass
                if hasattr(term, "_schedule_resize_emit"):
                    term._schedule_resize_emit(cols, rows)
                else:
                    term.resize_requested.emit(cols, rows)
        except Exception:
            pass

    def _find_term_widget(self, widget):
        if widget is None:
            return None
        # Prefer xterm widget, then pyte fallback
        for cls in (XtermTerminalWidget, TerminalWidget):
            try:
                found = widget.findChild(cls)
                if found is not None:
                    return found
            except Exception:
                pass
            if isinstance(widget, cls):
                return widget
        return None

    def _current_terminal_widget(self):

        sid = self.terminal_picker.currentData() if hasattr(self, "terminal_picker") else None
        widget = self.terminal_widgets.get(sid) if sid else None
        return self._find_term_widget(widget)

    def _copy_current_terminal(self):
        term = self._current_terminal_widget()
        if term is None:
            return
        if isinstance(term, XtermTerminalWidget):
            term.copy_all_output()
            return
        if hasattr(term, "toPlainText"):
            QApplication.clipboard().setText(term.toPlainText())

    def _clear_current_terminal(self):
        term = self._current_terminal_widget()
        if term:
            term.clear()  # full screen clear (local buffer)
            term.setFocus(Qt.FocusReason.OtherFocusReason)

    def _build_terminal_panel(self):
        self.terminal_panel = QFrame(); self.terminal_panel.setObjectName("terminalPanel")
        self.terminal_panel.setMinimumHeight(120)
        outer = QVBoxLayout(self.terminal_panel); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)

        bar = QFrame(); bar.setObjectName("terminalToolbar"); bl = QHBoxLayout(bar); bl.setContentsMargins(8,3,8,3); bl.setSpacing(4)
        # Keep the combo as the compatibility/source-of-truth for keyboard actions.
        # The visible VS Code-style session list is on the right below.
        self.terminal_picker = QComboBox(); self.terminal_picker.setMinimumWidth(1); self.terminal_picker.setMaximumWidth(1); self.terminal_picker.setVisible(False)
        self.terminal_picker.currentIndexChanged.connect(self._terminal_selected)
        bl.addWidget(self.terminal_picker)
        self.terminal_status_label = QLabel("No terminal"); self.terminal_status_label.setObjectName("terminalToolbarStatus"); bl.addWidget(self.terminal_status_label)
        self.terminal_search = QLineEdit()
        self.terminal_search.setPlaceholderText("Ctrl+Shift+F")
        self.terminal_search.setToolTip("Find in terminal (Ctrl+Shift+F)")
        self.terminal_search.setClearButtonEnabled(True)
        self.terminal_search.setMaximumWidth(220)
        self.terminal_search.returnPressed.connect(self._find_in_current_terminal)
        bl.addWidget(self.terminal_search)
        find_btn = self._action_button("", lambda _=False: self._find_in_current_terminal(), "fa5s.search", "Find next (Enter)")
        bl.addWidget(find_btn)
        suggest_btn = self._action_button("", lambda _=False: self._show_terminal_suggestions(), "fa5s.magic", "Shell command suggestions")
        bl.addWidget(suggest_btn)
        bl.addStretch()
        # Left-side panel toggles (explorer | session list) grouped together
        files_btn = self._action_button("", lambda _=False: self._toggle_terminal_files(), "fa5s.folder-open", "Explorer (side panel)")
        files_btn.setObjectName("terminalPanelToggle")
        files_btn.setCheckable(True)
        files_btn.setChecked(False)
        bl.addWidget(files_btn)
        self.terminal_files_toggle = files_btn
        sessions_btn = self._action_button("", lambda _=False: self._toggle_terminal_sessions(), "fa5s.columns", "Session list (right strip)")
        sessions_btn.setObjectName("terminalPanelToggle")
        sessions_btn.setCheckable(True)
        sessions_btn.setChecked(True)
        bl.addWidget(sessions_btn)
        self.terminal_sessions_toggle = sessions_btn
        bl.addSpacing(8)
        new_btn = self._action_button("", lambda _=False: self._spawn(self._create_terminal()), "fa5s.plus", "New terminal")
        new_btn.setProperty("accent", True); bl.addWidget(new_btn)
        rename_btn = self._action_button("", lambda _=False: self._rename_selected_terminal(), "fa5s.edit", "Rename terminal"); bl.addWidget(rename_btn)
        copy_btn = self._action_button("", lambda _=False: self._copy_current_terminal(), "fa5s.copy", "Copy terminal output"); bl.addWidget(copy_btn)
        clear_btn = self._action_button("", lambda _=False: self._clear_current_terminal(), "fa5s.eraser", "Clear terminal screen"); bl.addWidget(clear_btn)
        close_btn = self._action_button("", lambda _=False: self._close_selected_terminal(), "fa5s.times", "Close this terminal session"); close_btn.setProperty("danger", True); bl.addWidget(close_btn)
        hide_btn = self._action_button("", lambda _=False: self.toggle_terminal_panel(False), "fa5s.chevron-down", "Hide terminal panel")
        bl.addWidget(hide_btn)
        outer.addWidget(bar)

        self.terminal_body_splitter = QSplitter(Qt.Orientation.Horizontal)
        # Allow collapsing explorer / session list by dragging handles or toggle buttons.
        self.terminal_body_splitter.setChildrenCollapsible(True)
        self.terminal_body_splitter.setHandleWidth(4)
        self._terminal_sessions_visible = True
        self._saved_explorer_width = 285
        self._saved_sessions_width = 155

        self.terminal_file_host = QFrame()
        self.terminal_file_host.setObjectName("terminalFileHost")
        self.terminal_file_host.setMinimumWidth(0)
        file_lay = QVBoxLayout(self.terminal_file_host)
        file_lay.setContentsMargins(0,0,0,0); file_lay.setSpacing(0)
        self.terminal_file_browser = None

        self.terminal_stack = QStackedWidget()

        self.terminal_sessions_list = QListWidget()
        self.terminal_sessions_list.setObjectName("terminalSessionList")
        # Allow the session strip to shrink enough to still show the title text.
        self.terminal_sessions_list.setMaximumWidth(220)
        self.terminal_sessions_list.setMinimumWidth(0)  # allow full collapse via splitter
        self.terminal_sessions_list.setSpacing(2)
        self.terminal_sessions_list.setUniformItemSizes(True)
        self.terminal_sessions_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.terminal_sessions_list.setEditTriggers(
            QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.terminal_sessions_list.currentRowChanged.connect(self._terminal_session_row_changed)
        self.terminal_sessions_list.setToolTip("Shell sessions — right-click to rename or close")
        self.terminal_sessions_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.terminal_sessions_list.customContextMenuRequested.connect(self._terminal_sessions_context_menu)
        self.terminal_sessions_list.itemChanged.connect(self._terminal_session_item_renamed)

        self.terminal_body_splitter.addWidget(self.terminal_file_host)
        self.terminal_body_splitter.addWidget(self.terminal_stack)
        self.terminal_body_splitter.addWidget(self.terminal_sessions_list)
        self.terminal_body_splitter.setStretchFactor(0, 0)
        self.terminal_body_splitter.setStretchFactor(1, 1)
        self.terminal_body_splitter.setStretchFactor(2, 0)
        self.terminal_body_splitter.setSizes([0, 900, 155])
        self.terminal_body_splitter.splitterMoved.connect(self._on_terminal_body_splitter_moved)
        outer.addWidget(self.terminal_body_splitter, 1)
        self._terminal_mode = "hidden"

    def _show_terminal_suggestions(self) -> None:
        """Command palette: interactive Docker tables + a few shell snippets."""
        sid = self.terminal_picker.currentData() if getattr(self, "terminal_picker", None) else None
        session = self.terms.get(sid) if sid else None
        if session is None:
            return
        menu = QMenu(self)

        # mdocker commands run in the shell — pretty HTML tables under the prompt
        for cmd, label in [
            ("mdocker ps", "mps / mdocker ps  —  containers (running)"),
            ("mdocker ps -a", "mpa / mdocker ps -a  —  containers (all)"),
            ("mdocker images", "mimg / mdocker images"),
            ("mdocker volume ls", "mvol / mdocker volume ls"),
            ("mdocker network ls", "mnet / mdocker network ls"),
            ("mdocker stats", "mstats / mdocker stats"),
            ("mdocker compose ls", "mcomp / mdocker compose ls"),
            ("mdocker compose ps", "mdocker compose ps  —  compose containers"),
            ("mdocker service ls", "msvc / mdocker service ls"),
            ("mdocker system df", "mdf / mdocker system df"),
            ("mdocker context ls", "mdocker context ls"),
            ("mdocker plugin ls", "mdocker plugin ls"),
            ("mdocker node ls", "mdocker node ls"),
            ("mdocker stack ls", "mdocker stack ls"),
            ("mdocker secret ls", "mdocker secret ls"),
            ("mdocker config ls", "mdocker config ls"),
        ]:
            act = menu.addAction(label)
            act.setData(("shell", cmd))
            act.setToolTip(cmd)

        menu.addSeparator()
        for cmd, desc in [
            ("pwd", "Print current directory"),
            ("ls -la", "List files, including hidden"),
            ("ps aux --sort=-%cpu | head", "Top CPU processes"),
            ("docker system df", "Docker disk usage (terminal)"),
            ("git status", "Git working tree"),
        ]:
            act = menu.addAction(desc)
            act.setData(("shell", cmd))
            act.setToolTip(cmd)

        menu.addSeparator()
        note = menu.addAction("Tip: command is typed in the shell · result appears below · keep typing at $")
        note.setEnabled(False)

        chosen = menu.exec(QCursor.pos()) if 'QCursor' in globals() else menu.exec(self.mapToGlobal(self.rect().center()))
        if not chosen or not chosen.data():
            return
        kind, payload = chosen.data()
        if kind == "shell" and session.running:
            # UI-triggered commands are deliberately tagged in remote shell history.
            # The predictor rejects __sshdock_* entries, while manually typed commands
            # like pwd/ls/git status remain eligible for learning.
            command = str(payload).rstrip() + " # __sshdock_internal__\r"
            self._spawn(session.write_internal_command(command.encode("utf-8")), name="Terminal suggestion")

    def _on_terminal_magic_resize(self, sid: str, cols: int):
        # HTML overlay reflows with CSS — no re-query / re-print on resize.
        return

    def _term_widget_for(self, sid: str):
        page = self.terminal_widgets.get(sid)
        if page is None:
            return None
        try:
            from app.terminal.web_widget import XtermTerminalWidget, HAS_WEBENGINE
            if HAS_WEBENGINE:
                found = page.findChildren(XtermTerminalWidget)
                if found:
                    return found[0]
        except Exception:
            pass
        for child in page.findChildren(QWidget):
            if hasattr(child, "feed"):
                return child
        return None

    async def _handle_magic_docker(self, sid: str, payload: str):
        """OSC from mdocker: fetch data and show inline result pane under the shell."""
        from app.terminal.docker_pretty import parse_magic_kind
        kind = parse_magic_kind(payload)
        if not kind:
            term = self._term_widget_for(sid)
            if term is not None and hasattr(term, "feed"):
                msg = (
                    chr(13) + chr(10)
                    + "\033[33mmdocker:\033[0m unknown target — try: "
                    + "ps, ps -a, images, volume ls, network ls, stats, "
                    + "compose ls, compose ps, service ls, system df, "
                    + "context ls, plugin ls, node ls, stack ls, secret ls, config ls"
                    + chr(13) + chr(10)
                    + "\033[2m(or short: mps mpa mimg mvol mnet mstats mcomp msvc mdf)\033[0m"
                    + chr(13) + chr(10)
                )
                term.feed(msg.encode("utf-8"))
            return
        self.statusBar().showMessage(f"mdocker: loading {kind}…", 2000)
        await self._open_docker_results_table(kind, sid=sid)

    async def _open_docker_results_table(self, kind: str, sid: str | None = None):
        """Fetch docker JSON over SSH and show the HTML result pane (under xterm)."""
        if not self.current_id:
            self.statusBar().showMessage("Connect to a server first", 2500)
            return
        sid = sid or (self.terminal_picker.currentData() if getattr(self, "terminal_picker", None) else None)
        if not sid or sid not in self.terms:
            self.statusBar().showMessage("Open a terminal session first", 2500)
            return
        if not hasattr(self, "_magic_table_kind"):
            self._magic_table_kind = {}
        self._magic_table_kind[sid] = kind
        term = self._term_widget_for(sid)
        dm = DockerManager(self.manager.servers[self.current_id].ssh)
        try:
            if kind == "containers":
                rows_raw = await dm.ps(all_=False)
                cols = ["ID", "Names", "Image", "Status", "Ports", "RunningFor"]
                title = "Containers (running)"
            elif kind == "containers_all":
                rows_raw = await dm.ps(all_=True)
                cols = ["ID", "Names", "Image", "Status", "Ports", "RunningFor"]
                title = "Containers (all)"
            elif kind == "images":
                rows_raw = await dm.images()
                cols = ["Repository", "Tag", "ID", "Size", "CreatedSince"]
                title = "Images"
            elif kind == "volumes":
                rows_raw = await dm.volumes()
                cols = ["Name", "Driver", "Scope"]
                title = "Volumes"
            elif kind == "networks":
                rows_raw = await dm.networks()
                cols = ["ID", "Name", "Driver", "Scope"]
                title = "Networks"
            elif kind == "stats":
                rows_raw = await dm.stats()
                if not isinstance(rows_raw, list):
                    rows_raw = []
                cols = ["Name", "CPUPerc", "MemUsage", "MemPerc", "NetIO", "BlockIO"]
                title = "Resource stats"
            elif kind == "compose":
                try:
                    rows_raw = await dm.compose_ls(all_=True)
                except Exception:
                    rows_raw = []
                cols = ["Name", "Status", "ConfigFiles"]
                title = "Compose projects"
            elif kind == "compose_ps":
                try:
                    rows_raw = await dm.compose_ps(all_=True)
                except Exception:
                    rows_raw = []
                cols = ["Name", "Service", "Image", "Status", "Ports"]
                title = "Compose containers"
            elif kind == "services":
                try:
                    rows_raw = await dm.services()
                except Exception:
                    rows_raw = []
                cols = ["ID", "Name", "Mode", "Replicas", "Image", "Ports"]
                title = "Services (swarm)"
            elif kind == "system_df":
                try:
                    rows_raw = await dm.system_df()
                except Exception:
                    rows_raw = []
                cols = ["Type", "TotalCount", "Active", "Size", "Reclaimable"]
                title = "Disk usage (system df)"
            elif kind == "contexts":
                try:
                    rows_raw = await dm.contexts()
                except Exception:
                    rows_raw = []
                cols = ["Name", "Description", "DockerEndpoint", "Current"]
                title = "Contexts"
            elif kind == "plugins":
                try:
                    rows_raw = await dm.plugins()
                except Exception:
                    rows_raw = []
                cols = ["ID", "Name", "Description", "Enabled"]
                title = "Plugins"
            elif kind == "nodes":
                try:
                    rows_raw = await dm.nodes()
                except Exception:
                    rows_raw = []
                cols = ["ID", "Hostname", "Status", "Availability", "ManagerStatus", "EngineVersion"]
                title = "Nodes (swarm)"
            elif kind == "stacks":
                try:
                    rows_raw = await dm.stacks()
                except Exception:
                    rows_raw = []
                cols = ["Name", "Services", "Orchestrator"]
                title = "Stacks (swarm)"
            elif kind == "secrets":
                try:
                    rows_raw = await dm.secrets()
                except Exception:
                    rows_raw = []
                cols = ["ID", "Name", "CreatedAt", "UpdatedAt"]
                title = "Secrets (swarm)"
            elif kind == "configs":
                try:
                    rows_raw = await dm.configs()
                except Exception:
                    rows_raw = []
                cols = ["ID", "Name", "CreatedAt", "UpdatedAt"]
                title = "Configs (swarm)"
            else:
                rows_raw, cols, title = [], [], kind

            table_rows = []
            for item in rows_raw or []:
                if not isinstance(item, dict):
                    continue
                lower = {str(k).lower(): v for k, v in item.items()}
                row = []
                for c in cols:
                    v = item.get(c)
                    if v is None:
                        v = lower.get(c.lower())
                    if v is None and c == "Names":
                        v = item.get("Name") or lower.get("name")
                    if v is None and c == "RunningFor":
                        v = item.get("RunningFor") or ""
                    if v is None and c == "CreatedSince":
                        v = item.get("CreatedSince") or item.get("CreatedAt") or ""
                    if v is None and c == "CPUPerc":
                        v = item.get("CPUPerc") or item.get("CPU %") or ""
                    if v is None and c == "TotalCount":
                        v = item.get("TotalCount") or item.get("Total") or lower.get("totalcount") or ""
                    if v is None and c == "DockerEndpoint":
                        v = item.get("DockerEndpoint") or item.get("Endpoint") or lower.get("dockerendpoint") or ""
                    if v is None and c == "ManagerStatus":
                        v = item.get("ManagerStatus") or lower.get("managerstatus") or ""
                    if v is None and c == "EngineVersion":
                        v = item.get("EngineVersion") or lower.get("engineversion") or ""
                    if v is None and c == "Replicas":
                        v = item.get("Replicas") or lower.get("replicas") or ""
                    row.append("" if v is None else str(v))
                table_rows.append(row)

            headers = {
                "RunningFor": "AGE", "CreatedSince": "CREATED", "CPUPerc": "CPU %",
                "MemUsage": "MEM", "MemPerc": "MEM %", "NetIO": "NET I/O",
                "BlockIO": "BLOCK I/O", "ConfigFiles": "CONFIG", "Names": "NAME",
                "Repository": "REPOSITORY", "TotalCount": "TOTAL", "Reclaimable": "RECLAIMABLE",
                "DockerEndpoint": "ENDPOINT", "ManagerStatus": "MANAGER",
                "EngineVersion": "ENGINE", "CreatedAt": "CREATED", "UpdatedAt": "UPDATED",
            }
            display_cols = [headers.get(c, c.upper() if len(c) <= 12 else c) for c in cols]

            from app.terminal.docker_pretty import KIND_TO_CMD
            cmd = KIND_TO_CMD.get(kind, "")

            # Prefer the rich HTML overlay in xterm.js. The original ANSI table
            # renderer remains the fallback for the pyte terminal path.
            if term is None:
                self.statusBar().showMessage("mdocker: no terminal widget", 4000)
                return
            if hasattr(term, "show_table"):
                term.show_table(title, display_cols, table_rows, command=cmd)
            elif hasattr(term, "feed"):
                from app.terminal.ansi_table import format_ansi_table
                screen = getattr(term, "_screen", None)
                term_cols = max(40, int(getattr(screen, "columns", 120) or 120))
                term.feed(
                    format_ansi_table(
                        title,
                        display_cols,
                        table_rows,
                        term_cols=term_cols,
                    ).encode("utf-8")
                )
            else:
                self.statusBar().showMessage("mdocker: terminal cannot render tables", 4000)
                return

            self.statusBar().showMessage(f"{title}: {len(table_rows)} row(s)", 2000)
        except Exception as exc:
            log.exception("Docker result pane failed")
            self.statusBar().showMessage(f"Docker table failed: {exc}", 6000)

    def _ensure_terminal_file_browser(self):
        """Create the explorer widget once; always keep it visible inside the host."""
        if self.terminal_file_browser is not None:
            return self.terminal_file_browser
        if not self.current_id:
            return None
        backend = ServerFileBackend(lambda: self.manager.servers[self.current_id].ssh)
        self.terminal_file_browser = TerminalFileExplorer(
            backend,
            change_dir=lambda path: self._change_terminal_cwd(path, None),
            run_command=self._run_terminal_explorer_command,
            parent=self.terminal_file_host,
        )
        self.terminal_file_host.layout().addWidget(self.terminal_file_browser)
        self.terminal_file_browser.closed_requested.connect(
            lambda: self._set_terminal_explorer_visible(False)
        )
        self.terminal_file_browser.show()
        return self.terminal_file_browser

    def _refresh_explorer_cwd(self) -> None:
        browser = getattr(self, "terminal_file_browser", None)
        if browser is None:
            return
        sid = self.terminal_picker.currentData() if getattr(self, "terminal_picker", None) else None
        session = self.terms.get(sid) if sid else None
        cwd = getattr(session, "cwd", None) if session is not None else None
        browser.set_cwd(cwd or "/", refresh=True)

    def _toggle_terminal_files(self) -> None:
        """Toggle the dedicated terminal explorer without replacing the shell UI."""
        if not self.current_id:
            self.statusBar().showMessage("Connect to a server first", 2500)
            return
        sid = self.terminal_picker.currentData() if getattr(self, "terminal_picker", None) else None
        if not sid or sid not in self.terms:
            self.statusBar().showMessage("Open a terminal session first", 2500)
            return
        self._ensure_terminal_file_browser()
        # Derive real state from splitter width (source of truth), not a stale flag.
        sizes = self.terminal_body_splitter.sizes()
        currently_open = len(sizes) >= 1 and sizes[0] >= 40
        self._set_terminal_explorer_visible(not currently_open)

    def _terminal_splitter_sizes(self, *, explorer: bool | None = None, sessions: bool | None = None) -> list[int]:
        """Compute splitter sizes while preserving the opposite panel width."""
        sizes = list(self.terminal_body_splitter.sizes()) if hasattr(self, "terminal_body_splitter") else [0, 900, 155]
        while len(sizes) < 3:
            sizes.append(0)
        # Snapshot current non-zero widths before collapsing
        if sizes[0] >= 40:
            self._saved_explorer_width = max(160, sizes[0])
        if sizes[2] >= 40:
            self._saved_sessions_width = max(72, sizes[2])
        explorer_on = self._terminal_files_visible if explorer is None else bool(explorer)
        sessions_on = getattr(self, "_terminal_sessions_visible", True) if sessions is None else bool(sessions)
        explorer_w = int(getattr(self, "_saved_explorer_width", 285) or 285) if explorer_on else 0
        sessions_w = int(getattr(self, "_saved_sessions_width", 155) or 155) if sessions_on else 0
        mid = max(200, sizes[1] if sizes[1] > 0 else 700)
        return [explorer_w, mid, sessions_w]

    def _apply_body_splitter_sizes(self, sizes: list[int]) -> None:
        """setSizes without re-entering the splitterMoved handler."""
        sp = self.terminal_body_splitter
        self._body_splitter_block = True
        try:
            sp.blockSignals(True)
            sp.setSizes(sizes)
        finally:
            sp.blockSignals(False)
            self._body_splitter_block = False

    def _set_terminal_explorer_visible(self, visible: bool):
        visible = bool(visible)
        self._terminal_files_visible = visible
        if visible:
            self._ensure_terminal_file_browser()
        # Never hide the browser widget — only collapse the splitter section.
        # Hiding the widget left an empty host when the user dragged the panel open.
        browser = getattr(self, "terminal_file_browser", None)
        if browser is not None:
            browser.show()
        self._apply_body_splitter_sizes(self._terminal_splitter_sizes(explorer=visible))
        if visible:
            self._refresh_explorer_cwd()
        if hasattr(self, "terminal_files_toggle") and self.terminal_files_toggle is not None:
            self.terminal_files_toggle.setChecked(visible)
            self.terminal_files_toggle.setToolTip(
                "Hide explorer" if visible else "Show explorer"
            )

    def _toggle_terminal_sessions(self) -> None:
        """Show or hide the terminal session list strip on the right."""
        sizes = self.terminal_body_splitter.sizes()
        currently_open = len(sizes) >= 3 and sizes[2] >= 40
        visible = not currently_open
        self._terminal_sessions_visible = visible
        # Keep the widget itself visible; only size collapses the strip.
        self.terminal_sessions_list.show()
        self._apply_body_splitter_sizes(self._terminal_splitter_sizes(sessions=visible))
        btn = getattr(self, "terminal_sessions_toggle", None)
        if btn is not None:
            btn.setChecked(visible)
            btn.setToolTip("Hide session list" if visible else "Show session list")

    def _on_terminal_body_splitter_moved(self, _pos: int = 0, _index: int = 0) -> None:
        """Sync toggle flags when the user drags a panel open/closed."""
        if getattr(self, "_body_splitter_block", False):
            return
        sizes = self.terminal_body_splitter.sizes()
        if len(sizes) < 3:
            return
        explorer_w, _mid, sessions_w = sizes[0], sizes[1], sizes[2]
        threshold = 40

        # --- Explorer ---
        if explorer_w < threshold:
            if self._terminal_files_visible:
                self._terminal_files_visible = False
                if hasattr(self, "terminal_files_toggle") and self.terminal_files_toggle is not None:
                    self.terminal_files_toggle.setChecked(False)
                    self.terminal_files_toggle.setToolTip("Show explorer")
        else:
            was_closed = not self._terminal_files_visible
            self._terminal_files_visible = True
            self._saved_explorer_width = max(160, explorer_w)
            if hasattr(self, "terminal_files_toggle") and self.terminal_files_toggle is not None:
                self.terminal_files_toggle.setChecked(True)
                self.terminal_files_toggle.setToolTip("Hide explorer")
            # Dragging open: ensure browser exists and has content
            if was_closed:
                self._ensure_terminal_file_browser()
                browser = getattr(self, "terminal_file_browser", None)
                if browser is not None:
                    browser.show()
                    self._refresh_explorer_cwd()

        # --- Session list ---
        if sessions_w < threshold:
            if getattr(self, "_terminal_sessions_visible", True):
                self._terminal_sessions_visible = False
                btn = getattr(self, "terminal_sessions_toggle", None)
                if btn is not None:
                    btn.setChecked(False)
                    btn.setToolTip("Show session list")
        else:
            self._terminal_sessions_visible = True
            self._saved_sessions_width = max(72, sessions_w)
            btn = getattr(self, "terminal_sessions_toggle", None)
            if btn is not None:
                btn.setChecked(True)
                btn.setToolTip("Hide session list")
            self.terminal_sessions_list.show()

    def _change_terminal_cwd(self, path: str, sid: str | None = None):
        sid = sid or (self.terminal_picker.currentData() if getattr(self, "terminal_picker", None) else None)
        session = self.terms.get(sid) if sid else None
        if session is None or not session.running:
            return
        target = posixpath.normpath(path or "/")
        # Send literal \033/\007 to remote printf (not binary ESC) so the line
        # never echoes as "7;file://sshdock/..." garbage in the terminal.
        command = (
            f"cd {shlex.quote(target)} && "
            r"printf '\033]7;file://sshdock%s\007' \"$PWD\""
            " # __sshdock_internal__\r"
        )
        self._spawn(session.write_internal_command(command.encode("utf-8")), name="Terminal explorer cd")

    def _run_terminal_explorer_command(self, command: str):
        sid = self.terminal_picker.currentData() if getattr(self, "terminal_picker", None) else None
        session = self.terms.get(sid) if sid else None
        if session is None or not session.running:
            self.statusBar().showMessage("Open a terminal session first", 2500)
            raise RuntimeError("No active terminal session")
        payload = (str(command).rstrip() + " # __sshdock_internal__\r").encode("utf-8")
        return self._spawn(
            session.write_internal_command(payload),
            name="Terminal explorer command",
        )

    def _terminal_session_row_changed(self, row: int) -> None:
        if row < 0 or row >= self.terminal_sessions_list.count():
            return
        item = self.terminal_sessions_list.item(row)
        sid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not sid:
            return
        idx = self.terminal_picker.findData(sid)
        if idx >= 0 and self.terminal_picker.currentIndex() != idx:
            self.terminal_picker.blockSignals(True)
            self.terminal_picker.setCurrentIndex(idx)
            self.terminal_picker.blockSignals(False)
        widget = self.terminal_widgets.get(sid)
        if widget:
            self.terminal_stack.setCurrentWidget(widget)
        self.terminal_status_label.setText(self.terminal_status.get(sid, "No terminal"))
        # Keep explorer locked to this session's cwd
        session = self.terms.get(sid)
        if session is not None and self._terminal_files_visible:
            browser = getattr(self, "terminal_file_browser", None)
            if browser is not None:
                browser.set_cwd(getattr(session, "cwd", "/") or "/", refresh=True)

    def _focus_terminal_find(self) -> None:
        if not getattr(self, "terminal_search", None):
            return
        self.toggle_terminal_panel(True)
        self.terminal_search.setFocus()
        self.terminal_search.selectAll()

    def _find_in_current_terminal(self) -> None:
        query = (getattr(self, "terminal_search", None).text() if getattr(self, "terminal_search", None) else "") or ""
        query = query.strip()
        if not query:
            return
        sid = self.terminal_picker.currentData() if getattr(self, "terminal_picker", None) else None
        page = self.terminal_widgets.get(sid) if sid else None
        if page is None:
            return
        try:
            from app.terminal.web_widget import XtermTerminalWidget, HAS_WEBENGINE
            terms = page.findChildren(XtermTerminalWidget) if HAS_WEBENGINE else []
        except Exception:
            terms = []
        for w in terms:
            if hasattr(w, "find_text"):
                w.find_text(query)
                return
        self.statusBar().showMessage("Find is available for xterm sessions", 2500)

    def _refresh_terminal_selector(self, select_id=None):
        current = self.terminal_picker.currentData()
        self.terminal_picker.blockSignals(True); self.terminal_picker.clear()
        self.terminal_sessions_list.blockSignals(True); self.terminal_sessions_list.clear()
        for sid, session in self.terms.items():
            self.terminal_picker.addItem(session.title, sid)
            item = QListWidgetItem(session.title)
            item.setData(Qt.ItemDataRole.UserRole, sid)
            item.setToolTip(f"{session.title}\n{self.terminal_status.get(sid, 'Unknown state')}")
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsEditable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            self.terminal_sessions_list.addItem(item)
        self.terminal_picker.blockSignals(False)
        target = select_id or current or (self.terminal_picker.itemData(0) if self.terminal_picker.count() else None)
        if target:
            idx = self.terminal_picker.findData(target)
            if idx >= 0: self.terminal_picker.setCurrentIndex(idx)
            for row in range(self.terminal_sessions_list.count()):
                if self.terminal_sessions_list.item(row).data(Qt.ItemDataRole.UserRole) == target:
                    self.terminal_sessions_list.setCurrentRow(row)
                    break
        self.terminal_sessions_list.blockSignals(False)
        self.terminal_state.setText(f"{self.terminal_picker.count()} terminal session(s)" if self.terminal_picker.count() else "No active terminal")
        sid = self.terminal_picker.currentData() if self.terminal_picker.count() else None
        self.terminal_status_label.setText(self.terminal_status.get(sid, "No terminal") if sid else "No terminal")

    def _terminal_selected(self, index):
        sid = self.terminal_picker.itemData(index)
        if sid in self.terms:
            widget = self.terminal_widgets.get(sid)
            if widget: self.terminal_stack.setCurrentWidget(widget)
            self.terminal_status_label.setText(self.terminal_status.get(sid, "No terminal"))
            if self._terminal_files_visible and getattr(self, "terminal_file_browser", None) is not None:
                self.terminal_file_browser.set_cwd(getattr(self.terms[sid], "cwd", "/") or "/", refresh=True)
            self.terminal_sessions_list.blockSignals(True)
            for row in range(self.terminal_sessions_list.count()):
                if self.terminal_sessions_list.item(row).data(Qt.ItemDataRole.UserRole) == sid:
                    self.terminal_sessions_list.setCurrentRow(row)
                    break
            self.terminal_sessions_list.blockSignals(False)

    def _rename_selected_terminal(self):
        sid = self.terminal_picker.currentData()
        if sid: self.rename_terminal(sid)

    def _terminal_sessions_context_menu(self, pos):
        """Right-click menu on the terminal session strip: rename / close / close others."""
        item = self.terminal_sessions_list.itemAt(pos)
        if item is None:
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        if not sid:
            return
        # Select the clicked session first so rename/close act on it.
        row = self.terminal_sessions_list.row(item)
        if row >= 0:
            self.terminal_sessions_list.setCurrentRow(row)
        menu = QMenu(self)
        rename_act = menu.addAction("Rename")
        close_act = menu.addAction("Close")
        menu.addSeparator()
        close_others_act = menu.addAction("Close others")
        close_all_act = menu.addAction("Close all")
        chosen = menu.exec(self.terminal_sessions_list.viewport().mapToGlobal(pos))
        if chosen is rename_act:
            # Inline edit in the list itself.
            self.terminal_sessions_list.editItem(item)
        elif chosen is close_act:
            self._close_terminal_id(sid)
        elif chosen is close_others_act:
            for other_sid in list(self.terms.keys()):
                if other_sid != sid:
                    self._close_terminal_id(other_sid)
        elif chosen is close_all_act:
            for other_sid in list(self.terms.keys()):
                self._close_terminal_id(other_sid)

    def _terminal_session_item_renamed(self, item: QListWidgetItem):
        """Validate in-place rename from the session strip; reject duplicates."""
        if item is None:
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        session = self.terms.get(sid) if sid else None
        if session is None:
            return
        new_title = (item.text() or "").strip()
        if not new_title:
            # Revert empty name
            self.terminal_sessions_list.blockSignals(True)
            item.setText(session.title)
            self.terminal_sessions_list.blockSignals(False)
            self.statusBar().showMessage("Terminal name cannot be empty", 2500)
            return
        # Reject duplicate names (case-insensitive) among other sessions
        for other_sid, other in self.terms.items():
            if other_sid != sid and (other.title or "").strip().lower() == new_title.lower():
                self.terminal_sessions_list.blockSignals(True)
                item.setText(session.title)
                self.terminal_sessions_list.blockSignals(False)
                self.statusBar().showMessage(f"Name «{new_title}» is already used", 3500)
                return
        if new_title != session.title:
            session.title = new_title
            self._refresh_terminal_selector(sid)

    def _close_terminal_id(self, sid):
        if sid and self.current_id and sid in self.terms:
            self.statusBar().showMessage(f"Closing {self.terms[sid].title}…", 2500)
            self._spawn(self._close_terminal_and_cleanup(self.current_id, sid))

    def _close_selected_terminal(self):
        sid = self.terminal_picker.currentData()
        if not sid or not self.current_id:
            return
        session = self.terms.get(sid)
        if not session:
            return
        self._close_terminal_id(sid)

    async def _close_terminal_and_cleanup(self, server_id, sid):
        status = self.terminal_meta.get(sid)
        if status:
            status.setText("● Closing…")
        try:
            await asyncio.wait_for(self.manager.close_terminal(server_id, sid), timeout=8.0)
        except Exception as exc:
            log.exception("Failed to close terminal %s", sid)
            self.statusBar().showMessage(f"Unable to close terminal: {exc}", 6000)
        finally:
            if sid in self.terminal_widgets and sid not in self.terms:
                self._terminal_ui_closed(sid)

    def _unique_terminal_title(self, base_title: str) -> str:
        """Return a terminal title that is unique among current sessions."""
        base = str(base_title or "Terminal").strip() or "Terminal"
        used = {
            str(session.title or "").strip().casefold()
            for session in self.terms.values()
            if str(session.title or "").strip()
        }
        if base.casefold() not in used:
            return base
        suffix = 2
        while f"{base} {suffix}".casefold() in used:
            suffix += 1
        return f"{base} {suffix}"

    async def _create_terminal(self, title=None, command=None):
        if not self.current_id:
            return
        # Guard the whole asynchronous creation, not just the UI click, so
        # concurrent requests cannot race before SessionManager registers a session.
        if self._terminal_creation_in_progress:
            return
        self._terminal_creation_in_progress = True
        try:
            await self._create_terminal_impl(title=title, command=command)
        finally:
            self._terminal_creation_in_progress = False

    async def _create_terminal_impl(self, title=None, command=None):
        server_id = self.current_id
        title = self._unique_terminal_title(title or f"Terminal {len(self.terms) + 1}")

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        prefs = self.store.load_ui_prefs()
        term = create_terminal_widget(theme=str(prefs.get("theme") or "dark"))
        # Do not accept keystrokes until a real SSH session is wired to the widget.
        # This prevents connection-time keystrokes from being silently discarded.
        term.setEnabled(False)
        if hasattr(term, "set_completion_provider"):
            term.set_completion_provider(self._predict_terminal_command)
            if hasattr(term, "set_suggestions_provider"):
                term.set_suggestions_provider(self._predict_terminal_commands)
            if hasattr(term, "set_suggestions_detail_provider"):
                term.set_suggestions_detail_provider(self._predict_terminal_command_details)
            if hasattr(term, "set_terminal_ai_settings"):
                term.set_terminal_ai_settings(
                    shadow_enabled=self._terminal_ai_shadow_enabled,
                    suggestions_enabled=self._terminal_ai_suggestions_enabled,
                )
            if hasattr(term, "suggestion_accepted"):
                term.suggestion_accepted.connect(
                    lambda command, t=term: self._terminal_suggestion_accepted(command, t)
                )
            term.set_completion_context(server_id=server_id, cwd="/", shell="")
        try:
            fam = str(prefs.get("terminal_font_family") or "Consolas")
            sz = int(prefs.get("terminal_font_size") or 13)
            if hasattr(term, "set_font"):
                term.set_font(QFont(fam, sz))
        except Exception:
            pass
        page_layout.addWidget(term, 1)
        status_label = QLabel("● Connecting…")
        status_label.setObjectName("terminalStatus")
        session_ref = {"id": None}

        self.terminal_stack.addWidget(page)
        self.toggle_terminal_panel(True)
        self.terminal_stack.setCurrentWidget(page)

        try:
            session = await asyncio.wait_for(self.manager.open_terminal(server_id, title), timeout=15.0)
            self.terms[session.id] = session
            self.terminal_server_ids[session.id] = server_id
            session_ref["id"] = session.id
            self.terminal_widgets[session.id] = page
            self.terminal_meta[session.id] = status_label
            session.output.connect(term.feed)
            if hasattr(session, "command_result"):
                session.command_result.connect(
                    lambda exit_code, sid=session.id: self._terminal_command_result(sid, exit_code)
                )
            session.status.connect(lambda text, sid=session.id: self._set_terminal_status(sid, text))
            # The session can reach CONNECTED before the UI subscribes to its signal.
            # Synchronize the current state explicitly so the UI never remains on
            # the initial Connecting… placeholder.
            self._set_terminal_status(session.id, "● Connected" if session.running else "● Disconnected")
            session.closed.connect(self._terminal_ui_closed)
            term.input_bytes.connect(lambda data, s=session: self._spawn(s.write(data)))
            term.resize_requested.connect(lambda cols, rows, s=session: self._spawn(s.resize(cols, rows)))
            term.setEnabled(True)
            term.resize_requested.connect(
                lambda cols, rows, sid=session.id: self._on_terminal_magic_resize(sid, cols)
            )
            # Primary cwd source: Python-side OSC 7 parse from the PTY stream
            # (works even when the JS bridge is not ready yet).
            if hasattr(session, "cwd_changed"):
                session.cwd_changed.connect(lambda path, sid=session.id: self._terminal_cwd_changed(sid, path))
            if hasattr(session, "magic_command"):
                session.magic_command.connect(
                    lambda payload, sid=session.id: self._spawn(
                        self._handle_magic_docker(sid, payload), name="mdocker"
                    )
                )
            if hasattr(term, "magic_command"):
                # Browser-side OSC 777 fallback for sequences that do not reach
                # the Python stream parser intact.
                term.magic_command.connect(
                    lambda payload, sid=session.id: self._spawn(
                        self._handle_magic_docker(sid, payload), name="mdocker-xterm"
                    )
                )
            # Secondary: xterm.js OSC handler (backup)
            if hasattr(term, "cwd_changed"):
                term.cwd_changed.connect(lambda path, sid=session.id: self._terminal_cwd_changed(sid, path))

            self._spawn(
                self._load_terminal_predictor_history(session.id, session.ssh, server_id, term),
                name="Terminal history",
            )

            await asyncio.sleep(0)
            # Initial PTY size — xterm reports via FitAddon; estimate until then
            cols, rows = 120, 36
            if hasattr(term, "_last_size") and term._last_size:
                cols, rows = term._last_size
            else:
                metrics = term.fontMetrics()
                cw = max(1, metrics.horizontalAdvance("M"))
                ch = max(1, metrics.lineSpacing())
                cols = max(20, (term.width() or 800) // cw)
                rows = max(5, (term.height() or 400) // ch)
            await session.resize(cols, rows)
            if command:
                import shlex as _shlex
                # This command was launched by an SSHDock action (e.g. opening a
                # container shell), not typed by the user. Keep it out of AI history.
                command_line = " ".join(_shlex.quote(x) for x in command)
                command_line += " # __sshdock_internal__\r"
                await session.write_internal_command(command_line.encode("utf-8"))
            self._refresh_terminal_selector(session.id)
            self.terminal_stack.setCurrentWidget(page)
            term.setFocus(Qt.FocusReason.OtherFocusReason)
            # pyte-only cursor helpers (xterm manages its own cursor)
            if hasattr(term, "_update_cursor"):
                term._cursor_visible = True
                term._update_cursor()
            if hasattr(term, "_run_js"):
                term._run_js("window.sshdockTerm && window.sshdockTerm.fit && window.sshdockTerm.fit();")
        except Exception as exc:
            status_label.setText("● Failed")
            term.show_message(f"\n\x1b[31mSSH terminal failed to start:\x1b[0m {exc}\n")
            self.statusBar().showMessage(f"Terminal failed: {exc}", 10000)
            log.exception("Terminal creation failed")

    def _persist_terminal_predictor(self) -> None:
        """Schedule coalesced persistence so JSON serialization and disk I/O do not stall Qt."""
        if not self._terminal_ai_persist_learning:
            return
        self._terminal_predictor_persist_dirty = True
        if self._terminal_predictor_persist_active:
            return

        self._terminal_predictor_persist_active = True
        task = self._spawn(
            self._flush_terminal_predictor_persistence(),
            name="Terminal predictor persistence",
        )
        if task is None:
            self._terminal_predictor_persist_active = False
            # Preserve state if the event loop is unavailable during shutdown.
            try:
                self.store.save_terminal_learning_state(
                    self._command_predictor.state_dict()
                )
            except Exception:
                log.debug("Could not persist terminal AI learning state", exc_info=True)

    async def _flush_terminal_predictor_persistence(self) -> None:
        """Write a stable snapshot off-thread; fold concurrent save requests into one."""
        try:
            while self._terminal_predictor_persist_dirty and self._terminal_ai_persist_learning:
                self._terminal_predictor_persist_dirty = False
                state = self._command_predictor.state_dict()
                await asyncio.to_thread(self.store.save_terminal_learning_state, state)
        except asyncio.CancelledError:
            # Do not respawn a persistence task while the application is shutting down.
            self._terminal_predictor_persist_dirty = False
            raise
        except Exception:
            self._terminal_predictor_persist_dirty = False
            log.debug("Could not persist terminal AI learning state", exc_info=True)
        finally:
            self._terminal_predictor_persist_active = False
            if self._terminal_predictor_persist_dirty and self._terminal_ai_persist_learning:
                self._persist_terminal_predictor()

    def _predict_terminal_command(
        self, prefix: str, cwd: str, shell: str, server_id: str
    ) -> str:
        """Return the highest-ranked AI shadow completion."""
        if not self._terminal_ai_shadow_enabled:
            return ""
        sid = server_id or self.terminal_server_ids.get(
            self.terminal_picker.currentData()
            if getattr(self, "terminal_picker", None)
            else "",
            self.current_id or "",
        )
        value = self._command_predictor.suggest(
            prefix,
            server_id=sid or "",
            cwd=cwd or "/",
            shell=shell
            or self.terminal_shells.get(
                self.terminal_picker.currentData()
                if getattr(self, "terminal_picker", None)
                else "",
                "",
            ),
        )
        return str(value or "")

    def _predict_terminal_command_details(
        self, prefix: str, cwd: str, shell: str, server_id: str
    ) -> list[dict]:
        """Return rich context-ranked candidates for the AI palette."""
        if not self._terminal_ai_suggestions_enabled:
            return []
        sid = server_id or self.terminal_server_ids.get(
            self.terminal_picker.currentData()
            if getattr(self, "terminal_picker", None)
            else "",
            self.current_id or "",
        )
        effective_shell = shell or self.terminal_shells.get(
            self.terminal_picker.currentData()
            if getattr(self, "terminal_picker", None)
            else "",
            "",
        )
        ranked = self._command_predictor.suggest_ranked(
            prefix,
            server_id=sid or "",
            cwd=cwd or "/",
            shell=effective_shell,
            limit=5,
            min_score=1.0,
        )
        return [candidate.to_dict() for candidate in ranked]

    def _predict_terminal_commands(
        self, prefix: str, cwd: str, shell: str, server_id: str
    ) -> list[str]:
        """Compatibility wrapper returning only command strings."""
        return [
            item["command"]
            for item in self._predict_terminal_command_details(
                prefix, cwd, shell, server_id
            )
        ][:5]

    async def _load_terminal_predictor_history(
        self, sid: str, ssh, server_id: str, term
    ) -> None:
        try:
            snapshot = await load_remote_history(ssh)
            if self._terminal_ai_learn_history and snapshot.commands:
                prepared = self._command_predictor.prepare_snapshot_ingest(
                    snapshot, server_id=server_id
                )
                if prepared is not None:
                    commands, fingerprint = prepared
                    completed = False
                    try:
                        # Let Qt paint and process input between small history
                        # batches instead of ingesting up to 1,200 rows in one
                        # uninterrupted qasync callback.
                        batch_size = 32
                        for offset in range(0, len(commands), batch_size):
                            self._command_predictor.ingest(
                                commands[offset : offset + batch_size],
                                server_id=server_id,
                                cwd="",
                                shell=snapshot.shell,
                            )
                            await asyncio.sleep(0.005)
                        completed = True
                    finally:
                        self._command_predictor.finish_snapshot_ingest(
                            fingerprint,
                            server_id=server_id,
                            completed=completed,
                        )
                    if completed:
                        self._persist_terminal_predictor()
            self.terminal_shells[sid] = snapshot.shell
            if hasattr(term, "set_completion_context"):
                session = self.terms.get(sid)
                term.set_completion_context(
                    server_id=server_id,
                    cwd=getattr(session, "cwd", "/")
                    if session is not None
                    else "/",
                    shell=snapshot.shell,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug(
                "Terminal predictor history load failed for %s",
                server_id,
                exc_info=True,
            )

    def _terminal_command_submitted(self, command: str, term) -> None:
        """Remember a simple submitted command until the shell reports its status."""
        if not command:
            return
        sid = next(
            (
                key
                for key, widget in self.terminal_widgets.items()
                if widget is term.parentWidget()
            ),
            None,
        )
        if sid is None:
            for key, page in self.terminal_widgets.items():
                try:
                    if term is page or term.isAncestorOf(page):
                        sid = key
                        break
                except Exception:
                    continue
        if sid is None:
            for key, page in self.terminal_widgets.items():
                if term is page or term.parentWidget() is page:
                    sid = key
                    break

        session = self.terms.get(sid) if sid else None
        if session is None:
            return
        # Do not let keystrokes sent to a running foreground application replace
        # the command whose eventual prompt status is still pending.
        selected_suggestion = self._terminal_selected_suggestions.pop(sid, None)
        if sid in self._terminal_pending_command_results:
            return
        parsed = split_shell_commands(command, limit=2)
        # Compound commands and shell blocks need execution-aware parsing of their
        # own; don't attach one prompt status to the wrong fragment.
        if len(parsed) != 1:
            return

        server_id = self.terminal_server_ids.get(sid or "", self.current_id or "")
        cwd = getattr(session, "cwd", "/") or "/"
        shell = self.terminal_shells.get(sid or "", "")
        selected_parts = split_shell_commands(selected_suggestion or "", limit=2)
        selected_was_submitted = (
            len(selected_parts) == 1 and selected_parts[0] == parsed[0]
        )
        if getattr(session, "result_reporting_enabled", False):
            self._terminal_pending_command_results[sid] = (
                parsed[0], server_id, cwd, shell, selected_was_submitted
            )
            expect_result = getattr(session, "expect_command_result", None)
            if callable(expect_result):
                expect_result()
            return

        # Enter confirms that this command was submitted. Some shells or custom
        # prompt configurations do not emit our OSC exit-status marker; in that
        # case, learn the command now rather than leaving the live model unchanged
        # forever while only the connection-time history is learned.
        self._command_predictor.add_command(
            parsed[0], server_id=server_id, cwd=cwd, shell=shell
        )
        if selected_was_submitted:
            self._command_predictor.record_suggestion_acceptance(
                parsed[0], server_id=server_id, cwd=cwd, shell=shell
            )
        # Arm the handshake after every submitted command. A prompt marker that
        # arrives during startup is ignored, so it cannot falsely enable result
        # correlation before the first real user command.
        expect_result = getattr(session, "expect_command_result", None)
        if callable(expect_result):
            expect_result()
        self._persist_terminal_predictor()

    def _terminal_command_batch_submitted(self, command_batch: str, term) -> None:
        """Stage a plain multiline paste until the shell reports a prompt result."""
        if not command_batch:
            return
        sid = next(
            (key for key, widget in self.terminal_widgets.items()
             if widget is term.parentWidget()),
            None,
        )
        if sid is None:
            for key, page in self.terminal_widgets.items():
                try:
                    if term is page or term.isAncestorOf(page):
                        sid = key
                        break
                except Exception:
                    continue
        if sid is None:
            for key, page in self.terminal_widgets.items():
                if term is page or term.parentWidget() is page:
                    sid = key
                    break

        session = self.terms.get(sid) if sid else None
        if session is None:
            return
        # Do not overwrite a pending command or batch whose result has not arrived.
        if sid in self._terminal_pending_command_results:
            return
        commands = split_pasted_shell_commands(command_batch, limit=64)
        if not commands:
            return

        self._terminal_selected_suggestions.pop(sid, None)
        server_id = self.terminal_server_ids.get(sid, self.current_id or "")
        cwd = getattr(session, "cwd", "/") or "/"
        shell = self.terminal_shells.get(sid, "")
        # One prompt status may cover the entire multiline readline buffer.
        # Keep the command order as a single pending item until execution is confirmed.
        if getattr(session, "result_reporting_enabled", False):
            self._terminal_pending_command_results[sid] = (
                "\n".join(commands), server_id, cwd, shell, False
            )
            expect_result = getattr(session, "expect_command_result", None)
            if callable(expect_result):
                expect_result()
            return

        # Learn submitted batches as well when this session has not demonstrated
        # working OSC status reporting. They have no reliable aggregate exit code.
        self._command_predictor.add_command(
            "\n".join(commands), server_id=server_id, cwd=cwd, shell=shell
        )
        expect_result = getattr(session, "expect_command_result", None)
        if callable(expect_result):
            expect_result()
        self._persist_terminal_predictor()

    def _terminal_command_result(self, sid: str, exit_code: int) -> None:
        """Learn submitted commands only after the shell reports a prompt result."""
        pending = self._terminal_pending_command_results.pop(sid, None)
        if pending is None:
            return
        command, server_id, cwd, shell, was_suggested = pending
        parsed = split_shell_commands(command, limit=64)
        if not parsed:
            return
        self._command_predictor.add_command(
            command, server_id=server_id, cwd=cwd, shell=shell
        )
        # A multiline paste may return only one final status. Do not attribute
        # that aggregate status to every command in the batch.
        if len(parsed) == 1:
            if was_suggested:
                self._command_predictor.record_suggestion_acceptance(
                    parsed[0], server_id=server_id, cwd=cwd, shell=shell
                )
            self._command_predictor.record_command_result(
                parsed[0], success=int(exit_code) == 0, server_id=server_id
            )
        self._persist_terminal_predictor()

    def _terminal_suggestion_accepted(self, command: str, term) -> None:
        if not command:
            return
        sid = next(
            (
                key
                for key, widget in self.terminal_widgets.items()
                if widget is term.parentWidget()
            ),
            None,
        )
        if sid is None:
            for key, page in self.terminal_widgets.items():
                try:
                    if term is page or term.isAncestorOf(page):
                        sid = key
                        break
                except Exception:
                    continue
        session = self.terms.get(sid) if sid else None
        if session is None or sid is None:
            return
        parsed = split_shell_commands(command, limit=2)
        if len(parsed) != 1:
            return
        # Selection is only staged here. The acceptance signal affects model
        # weights only after this exact command is submitted and the shell
        # returns its exit status; Ctrl+C before Enter therefore has no effect.
        self._terminal_selected_suggestions[sid] = parsed[0]

    def _terminal_suggestion_cancelled(self, term) -> None:
        """Discard an inserted suggestion when the input line is cancelled."""
        sid = next(
            (
                key for key, widget in self.terminal_widgets.items()
                if widget is term.parentWidget()
            ),
            None,
        )
        if sid is None:
            for key, page in self.terminal_widgets.items():
                try:
                    if term is page or term.isAncestorOf(page):
                        sid = key
                        break
                except Exception:
                    continue
        if sid is not None:
            self._terminal_selected_suggestions.pop(sid, None)

    def _terminal_cwd_changed(self, sid: str, path: str):
        """Update the session, live prediction context, and visible explorer."""
        normalized = posixpath.normpath(path or "/") or "/"
        session = self.terms.get(sid)
        if session is not None:
            session.cwd = normalized

        # The JS bridge keeps its own completion context. Refresh it on every
        # OSC cwd update so ranking uses the directory the shell is actually in,
        # not the initial "/" captured when the terminal widget was created.
        page = self.terminal_widgets.get(sid)
        term = self._find_term_widget(page)
        if term is not None and hasattr(term, "set_completion_context"):
            term.set_completion_context(
                server_id=self.terminal_server_ids.get(sid, self.current_id or ""),
                cwd=normalized,
                shell=self.terminal_shells.get(sid, ""),
            )

        current = self.terminal_picker.currentData() if getattr(self, "terminal_picker", None) else None
        if sid != current:
            return
        browser = getattr(self, "terminal_file_browser", None)
        if browser is not None and self._terminal_files_visible:
            browser.set_cwd(normalized, refresh=True)

    def _set_terminal_status(self, sid, text):
        # A disconnected PTY cannot deliver the pending prompt marker. Clear the
        # correlation slot so a reconnect cannot freeze online command learning.
        lowered_status = str(text or "").casefold()
        if any(word in lowered_status for word in ("reconnecting", "disconnected", "failed")):
            self._terminal_pending_command_results.pop(sid, None)
        self.terminal_status[sid] = text
        meta = self.terminal_meta.get(sid)
        if meta:
            meta.setText(text)
        if hasattr(self, "terminal_picker") and self.terminal_picker.currentData() == sid:
            self.terminal_status_label.setText(text)
        if hasattr(self, "terminal_sessions_list"):
            for row in range(self.terminal_sessions_list.count()):
                item = self.terminal_sessions_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == sid:
                    title = self.terms.get(sid).title if sid in self.terms else item.text()
                    item.setText(title)
                    item.setToolTip(f"{title}\n{text}")
                    break

    def _terminal_ui_closed(self, sid):
        self._terminal_pending_command_results.pop(sid, None)
        self._terminal_selected_suggestions.pop(sid, None)
        self.terms.pop(sid, None)
        self.terminal_server_ids.pop(sid, None)
        self.terminal_shells.pop(sid, None)
        widget = self.terminal_widgets.pop(sid, None)
        self.terminal_meta.pop(sid, None)
        self.terminal_status.pop(sid, None)
        if widget:
            self.terminal_stack.removeWidget(widget)
            widget.deleteLater()
        self._refresh_terminal_selector()
        if not self.terms:
            self.toggle_terminal_panel(False)

    def rename_terminal(self,sid):
        from PySide6.QtWidgets import QInputDialog
        s=self.terms.get(sid)
        if not s:return
        name,ok=QInputDialog.getText(self,"Rename terminal","Name:",text=s.title)
        if not (ok and name.strip()):
            return
        new_title = name.strip()
        for other_sid, other in self.terms.items():
            if other_sid != sid and (other.title or "").strip().lower() == new_title.lower():
                self.statusBar().showMessage(f"Name «{new_title}» is already used", 3500)
                return
        s.title = new_title
        self._refresh_terminal_selector(sid)


    async def load_images(self):
        # Clear BEFORE fetching so stale data doesn't persist on failure.
        dm = DockerManager(self.manager.servers[self.current_id].ssh)
        try:
            rows = await dm.images()
        except Exception as exc:
            log.warning("Image list fetch failed: %s", exc)
            r = self.image_table.rowCount()
            self.image_table.insertRow(r)
            self.image_table.setItem(r, 1, QTableWidgetItem(f"Error loading images: {exc}"))
            return
        try:
            used = await dm.used_images()
        except Exception:
            used = set()  # best-effort — just mark everything as unused
        with self._atomic_table(self.image_table, (1, 3)):
            for x in rows:
                r = self.image_table.rowCount(); self.image_table.insertRow(r)
                repo = x.get("Repository") or "—"
                tag = x.get("Tag") or "—"
                iid = x.get("ID") or "—"
                size = x.get("Size") or "—"
                # Match against image name:tag, id, short id
                ref = f"{repo}:{tag}" if repo != "<none>" and tag != "<none>" else ""
                short = iid.replace("sha256:", "")[:12]
                in_use = bool(
                    (ref and ref in used)
                    or (repo in used)
                    or (iid in used)
                    or (short and short in used)
                    or any(short and short in u for u in used)
                )
                dot = QTableWidgetItem("●" if in_use else "○")
                dot.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                dot.setForeground(QColor("#39d47a" if in_use else "#6b7280"))
                f = dot.font(); f.setPointSize(14); f.setBold(True); dot.setFont(f)
                dot.setToolTip("In use by a container" if in_use else "Not used by any container")
                self.image_table.setItem(r, 0, dot)
                self.image_table.setItem(r, 1, QTableWidgetItem(repo))
                self.image_table.setItem(r, 2, QTableWidgetItem(tag))
                self.image_table.setItem(r, 3, QTableWidgetItem(iid))
                self.image_table.setItem(r, 4, QTableWidgetItem(size))
                img_ref = iid if iid and iid != "—" else (f"{repo}:{tag}" if repo != "—" else "")
                entries = [
                    ("Inspect", "fa5s.search", lambda _=False, n=img_ref: self._spawn(self._inspect_resource("image", n))),
                    ("Copy ID", "fa5s.copy", lambda _=False, n=iid: QApplication.clipboard().setText(n)),
                    None,
                    ("Remove", "fa5s.trash", lambda _=False, n=img_ref: self._spawn(self._remove_image(n))),
                ]
                self.image_table.setCellWidget(r, 5, self._actions_cell(entries, f"Actions for {repo}:{tag}"))
                self.image_table.setRowHeight(r, 38)

        self._image_rows = rows
    def _filter_images(self,text):
        for r in range(self.image_table.rowCount()): self.image_table.setRowHidden(r,text.lower() not in " ".join((self.image_table.item(r,c).text() if self.image_table.item(r,c) else "") for c in range(1,5)).lower())
    def _pull_image(self):
        from PySide6.QtWidgets import QInputDialog
        image,ok=QInputDialog.getText(self,"Pull image","Image: example: nginx:latest")
        if ok and image.strip(): self._spawn(self._pull_image_exec(image.strip()))
    async def _pull_image_exec(self,image):
        result=await DockerManager(self.manager.servers[self.current_id].ssh).run(["pull",image],timeout=3600); self.statusBar().showMessage(result.stdout[-3000:] if result.exit_code==0 else result.stderr[-3000:],8000); await self.load_images()

    async def load_volumes(self, usage=None):
        # Clear BEFORE fetching so stale data doesn't persist on failure.
        self.volume_table.verticalHeader().setDefaultSectionSize(38)
        dm=DockerManager(self.manager.servers[self.current_id].ssh)
        try:
            rows=await dm.volumes()
        except Exception as exc:
            log.warning("Volume list fetch failed: %s", exc)
            r = self.volume_table.rowCount()
            self.volume_table.insertRow(r)
            self.volume_table.setItem(r, 0, QTableWidgetItem(f"Error loading volumes: {exc}"))
            return
        try:
            used_volumes = usage[0] if usage else (await dm.resource_usage())[0]
        except Exception:
            used_volumes = set()
        with self._atomic_table(self.volume_table, (0,)):
            for x in rows:
                name=x.get("Name","—"); r=self.volume_table.rowCount(); self.volume_table.insertRow(r)
                in_use = name in used_volumes
                used_txt = "● Used" if in_use else "○ Unused"
                vals=[name,x.get("Driver","—"),x.get("Scope","—"), used_txt]
                for c,val in enumerate(vals): self.volume_table.setItem(r,c,QTableWidgetItem(str(val)))
                self.volume_table.item(r,3).setForeground(QColor("#39d47a" if in_use else "#6b7280"))
                entries = [
                    ("Inspect", "fa5s.search", lambda _=False, n=name: self._spawn(self._inspect_resource("volume", n))),
                    ("Copy name", "fa5s.copy", lambda _=False, n=name: QApplication.clipboard().setText(n)),
                    None,
                    ("Remove", "fa5s.trash", lambda _=False, n=name: self._spawn(self._remove_volume(n))),
                ]
                self.volume_table.setCellWidget(r, 4, self._actions_cell(entries, f"Actions for {name}"))
                self.volume_table.setRowHeight(r, 38)



    async def _inspect_resource(self, kind: str, name: str):
        if not self.current_id or not name:
            return
        dm = DockerManager(self.manager.servers[self.current_id].ssh)
        if kind == "image":
            result = await dm.run(["image", "inspect", name], timeout=60)
        elif kind == "volume":
            result = await dm.run(["volume", "inspect", name], timeout=60)
        elif kind == "network":
            result = await dm.run(["network", "inspect", name], timeout=60)
        else:
            result = await dm.inspect(name)
        text = result.stdout or result.stderr or "(empty)"
        try:
            data = json.loads(text)
            text = json.dumps(data, indent=2)
            lang = "json"
        except Exception:
            lang = "text"
        self._show_code_dialog(f"Inspect {kind} — {name}", text, language=lang)

    async def _remove_image(self, name: str):
        if not name:
            return
        if QMessageBox.question(self, "Remove image", f"Remove image {name}?") != QMessageBox.StandardButton.Yes:
            return
        result = await DockerManager(self.manager.servers[self.current_id].ssh).run(["rmi", name], timeout=120)
        if result.exit_code != 0:
            ErrorDialog("Remove image", "Docker refused to remove the image.", result.stderr or result.stdout, self).exec()
        await self.load_images()

    async def _remove_volume(self,name):
        if QMessageBox.question(self,"Remove volume",f"Remove {name}?")!=QMessageBox.StandardButton.Yes:return
        await DockerManager(self.manager.servers[self.current_id].ssh).run(["volume","rm",name],120); await self.load_volumes()
    async def load_networks(self, usage=None):
        if not self.current_id:
            return
        self.network_table.verticalHeader().setDefaultSectionSize(38)
        dm = DockerManager(self.manager.servers[self.current_id].ssh)
        try:
            rows = await dm.networks()
        except Exception as exc:
            log.warning("Network list fetch failed: %s", exc)
            self.statusBar().showMessage(f"Could not refresh networks: {exc}", 5000)
            return
        try:
            used_networks = usage[1] if usage else (await dm.resource_usage())[1]
        except Exception:
            used_networks = set()
        with self._atomic_table(self.network_table, (0,)):
            self.network_table.setRowCount(0)
            for x in rows:
                name = x.get("Name", "—")
                row = self.network_table.rowCount()
                self.network_table.insertRow(row)
                in_use = name in used_networks
                vals = [name, x.get("Driver","—"), x.get("Scope","—"),
                        "● Used" if in_use else "○ Unused", x.get("ID","—")]
                for col, value in enumerate(vals):
                    self.network_table.setItem(row, col, QTableWidgetItem(str(value)))
                self.network_table.item(row, 3).setForeground(
                    QColor("#39d47a" if in_use else "#6b7280")
                )
                entries = [
                    ("Inspect", "fa5s.search", lambda _=False, n=name: self._spawn(self._inspect_resource("network", n))),
                    ("Copy name", "fa5s.copy", lambda _=False, n=name: QApplication.clipboard().setText(n)),
                    None,
                    ("Remove", "fa5s.trash", lambda _=False, n=name: self._spawn(self._remove_network(n))),
                ]
                self.network_table.setCellWidget(row, 5, self._actions_cell(entries, f"Actions for {name}"))
                self.network_table.setRowHeight(row, 38)

    async def _remove_network(self,name):
        if QMessageBox.question(self,"Remove network",f"Remove {name}?")!=QMessageBox.StandardButton.Yes:return
        await DockerManager(self.manager.servers[self.current_id].ssh).run(["network","rm",name],120); await self.load_networks()
    def _selected_table_names(self, table, name_col: int = 0) -> list[str]:
        rows = sorted({i.row() for i in table.selectionModel().selectedRows()})
        names = []
        for r in rows:
            item = table.item(r, name_col)
            if item and item.text().strip() and item.text() != "—":
                names.append(item.text().strip())
        return names

    async def _bulk_remove_images(self):
        if not self.current_id:
            return
        # Prefer repository:tag from cols 1,2; fallback to ID col 3
        rows = sorted({i.row() for i in self.image_table.selectionModel().selectedRows()})
        refs = []
        for r in rows:
            repo = self.image_table.item(r, 1).text() if self.image_table.item(r, 1) else ""
            tag = self.image_table.item(r, 2).text() if self.image_table.item(r, 2) else ""
            iid = self.image_table.item(r, 3).text() if self.image_table.item(r, 3) else ""
            if repo and repo != "<none>" and tag and tag != "<none>":
                refs.append(f"{repo}:{tag}")
            elif iid:
                refs.append(iid)
        if not refs:
            QMessageBox.information(self, "Images", "Select one or more images first.")
            return
        if QMessageBox.question(self, "Remove images", f"Remove {len(refs)} image(s)?") != QMessageBox.StandardButton.Yes:
            return
        dm = DockerManager(self.manager.servers[self.current_id].ssh)
        errors = []
        for ref in refs:
            r = await dm.run(["rmi", ref], timeout=120)
            if r.exit_code != 0:
                errors.append(f"{ref}: {(r.stderr or r.stdout or '')[:80]}")
        from app.ui.notifications import NotificationBus
        if errors:
            NotificationBus.instance().warning("Some images not removed", f"{len(errors)} failed")
            ErrorDialog("Remove images", "Some removals failed.", "\n".join(errors), self).exec()
        else:
            NotificationBus.instance().success("Images removed", str(len(refs)))
        await self.load_images()

    async def _bulk_remove_volumes(self):
        if not self.current_id:
            return
        names = self._selected_table_names(self.volume_table, 0)
        if not names:
            QMessageBox.information(self, "Volumes", "Select one or more volumes first.")
            return
        if QMessageBox.question(self, "Remove volumes", f"Remove {len(names)} volume(s)?\n\n" + "\n".join(names[:12])) != QMessageBox.StandardButton.Yes:
            return
        dm = DockerManager(self.manager.servers[self.current_id].ssh)
        errors = []
        for name in names:
            r = await dm.run(["volume", "rm", name], timeout=120)
            if r.exit_code != 0:
                errors.append(f"{name}: {(r.stderr or r.stdout or '')[:80]}")
        from app.ui.notifications import NotificationBus
        if errors:
            NotificationBus.instance().warning("Some volumes not removed", f"{len(errors)} failed")
            ErrorDialog("Remove volumes", "Some removals failed (in use?).", "\n".join(errors), self).exec()
        else:
            NotificationBus.instance().success("Volumes removed", str(len(names)))
        await self.load_volumes()

    async def _bulk_remove_networks(self):
        if not self.current_id:
            return
        names = self._selected_table_names(self.network_table, 0)
        # protect default networks
        protected = {"bridge", "host", "none"}
        names = [n for n in names if n not in protected]
        if not names:
            QMessageBox.information(self, "Networks", "Select one or more removable networks first (not bridge/host/none).")
            return
        if QMessageBox.question(self, "Remove networks", f"Remove {len(names)} network(s)?\n\n" + "\n".join(names[:12])) != QMessageBox.StandardButton.Yes:
            return
        dm = DockerManager(self.manager.servers[self.current_id].ssh)
        errors = []
        for name in names:
            r = await dm.run(["network", "rm", name], timeout=120)
            if r.exit_code != 0:
                errors.append(f"{name}: {(r.stderr or r.stdout or '')[:80]}")
        from app.ui.notifications import NotificationBus
        if errors:
            NotificationBus.instance().warning("Some networks not removed", f"{len(errors)} failed")
            ErrorDialog("Remove networks", "Some removals failed.", "\n".join(errors), self).exec()
        else:
            NotificationBus.instance().success("Networks removed", str(len(names)))
        await self.load_networks()

    async def _prune_resource(self, target: str):
        """target: image | volume | network | system"""
        if not self.current_id:
            return
        labels = {
            "image": ("Purge unused images", "docker image prune -f — remove dangling images"),
            "volume": ("Purge unused volumes", "docker volume prune -f — remove volumes not used by any container"),
            "network": ("Purge unused networks", "docker network prune -f — remove unused user-defined networks"),
            "system": ("System prune", "docker system prune -f — containers, networks, dangling images"),
        }
        title, desc = labels.get(target, (f"Purge {target}", f"docker {target} prune -f"))
        if QMessageBox.question(self, title, f"{desc}\n\nContinue?") != QMessageBox.StandardButton.Yes:
            return
        dm = DockerManager(self.manager.servers[self.current_id].ssh)
        r = await dm.prune(target)
        from app.ui.notifications import NotificationBus
        out = (r.stdout or r.stderr or "").strip()
        if r.exit_code == 0:
            NotificationBus.instance().success(title, out.splitlines()[-1] if out else "done")
        else:
            NotificationBus.instance().error(title + " failed", out[:100])
            ErrorDialog(title, "Prune failed.", out, self).exec()
        if target == "image":
            await self.load_images()
        elif target in {"volume", "network"}:
            await self.refresh_storage()


    async def refresh_storage(self):
        if not self.current_id:
            return
        dm=DockerManager(self.manager.servers[self.current_id].ssh)
        try:
            usage=await dm.resource_usage(max_age=10.0)
        except Exception as exc:
            log.warning("Resource usage fetch failed: %s", exc)
            usage = (set(), set())  # empty usage — nothing marked as in-use
        # Use return_exceptions=True so a failure in one loader doesn't
        # cancel the other.
        await asyncio.gather(
            self.load_volumes(usage),
            self.load_networks(usage),
            return_exceptions=True,
        )
    async def _compose_action(self, args):
        if not self.current_id:return
        r=await DockerManager(self.manager.servers[self.current_id].ssh).compose(args)
        self.compose_output.setPlainText(r.stdout if r.exit_code==0 else (r.stderr or r.stdout))

    async def load_compose(self):
        if hasattr(self, "compose_panel"):
            await self.compose_panel.load()


    def _compose_row_actions(self, project):
        entries = [
            ("Up", "fa5s.play", lambda _=False, p=project: self._spawn(self._compose_action_for_project("up", p))),
            ("Start", "fa5s.play-circle", lambda _=False, p=project: self._spawn(self._compose_action_for_project("start", p))),
            ("Stop", "fa5s.stop-circle", lambda _=False, p=project: self._spawn(self._compose_action_for_project("stop", p))),
            ("Down", "fa5s.stop", lambda _=False, p=project: self._spawn(self._compose_action_for_project("down", p))),
            ("Restart", "fa5s.redo", lambda _=False, p=project: self._spawn(self._compose_action_for_project("restart", p))),
            ("Pull", "fa5s.download", lambda _=False, p=project: self._spawn(self._compose_action_for_project("pull", p))),
            ("Build", "fa5s.hammer", lambda _=False, p=project: self._spawn(self._compose_action_for_project("build", p))),
            None,
            ("Copy project name", "fa5s.copy", lambda _=False, p=project: QApplication.clipboard().setText(p)),
            ("Unregister project", "fa5s.trash", lambda _=False, p=project: self._unregister_compose_project(p)),
        ]
        icon = None
        if qta is not None:
            try:
                icon = qta.icon("fa5s.bars", color=self._ui_icon_color())
            except Exception:
                pass
        return create_table_actions_cell(
            self._table_action_menu,
            entries,
            tooltip=f"Compose actions for {project}",
            icon=icon,
        )


    async def start_events(self):
        if not self.current_id:
            return
        self.events_start.setEnabled(False)
        self.events_stop.setEnabled(True)
        self._event_proc = await DockerManager(self.manager.servers[self.current_id].ssh).events()
        self._events_refresh_pending = False
        try:
            from app.docker.models import DockerEvent
            while True:
                line = await self._event_proc.stdout.readline()
                if not line:
                    break
                text = line if isinstance(line, str) else line.decode("utf-8", "replace")
                try:
                    obj = json.loads(text)
                    ev = DockerEvent.from_json(obj)
                    text = f"{ev.time}  {ev.type}  {ev.action}  {ev.name}".strip()
                    # Debounced live refresh of related tables
                    self._schedule_events_refresh(ev)
                except Exception:
                    pass
                self.events_output.appendPlainText(text)
        except asyncio.CancelledError:
            pass
        finally:
            self.events_start.setEnabled(True)
            self.events_stop.setEnabled(False)

    def _schedule_events_refresh(self, ev) -> None:
        """Coalesce docker events into a single delayed UI refresh."""
        kinds = getattr(self, "_events_dirty", set())
        if getattr(ev, "affects_containers", False):
            kinds.add("containers")
        if getattr(ev, "affects_images", False):
            kinds.add("images")
        if getattr(ev, "affects_volumes", False) or getattr(ev, "affects_networks", False):
            kinds.add("storage")
        self._events_dirty = kinds
        if getattr(self, "_events_refresh_pending", False):
            return
        self._events_refresh_pending = True

        def _run():
            self._events_refresh_pending = False
            dirty = getattr(self, "_events_dirty", set())
            self._events_dirty = set()
            if not self.current_id or not dirty:
                return
            if "containers" in dirty:
                self._spawn(self.load_containers())
            if "images" in dirty:
                self._spawn(self.load_images())
            if "storage" in dirty:
                self._spawn(self.load_volumes())
                self._spawn(self.load_networks())

        QTimer.singleShot(800, _run)
    async def stop_events(self):
        proc=getattr(self,"_event_proc",None)
        if proc:
            try:proc.terminate()
            except Exception:pass
            self._event_proc=None

    async def refresh_server_page(self):
        if not self.current_id:return
        self._show_page_spinner(True)
        try:
            # Use return_exceptions=True so one failing loader (e.g.
            # _load_services on a non-systemd host) doesn't cancel the
            # others. Each loader has its own try/except internally.
            results = await asyncio.gather(
                self._load_server_overview(),
                self._load_processes(),
                self._load_services(),
                return_exceptions=True,
            )
            # Log any exceptions that slipped through.
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    names = ["overview", "processes", "services"]
                    log.warning("Server tab %s failed: %s", names[i] if i < len(names) else i, r)
            if self.server_tabs.currentIndex() >= 0 and self.server_tabs.tabText(self.server_tabs.currentIndex()) == "Files":
                try:
                    await self.server_file_browser._refresh_async()
                except Exception as exc:
                    log.warning("File browser refresh failed: %s", exc)
        finally:
            self._show_page_spinner(False)

    async def _load_server_overview(self):
        # Clear the table BEFORE fetching so stale data doesn't persist
        # if the SSH command fails.
        try:
            snap = await ServerMonitor(self.manager.servers[self.current_id].ssh).snapshot()
        except Exception as exc:
            log.warning("Server overview snapshot failed: %s", exc)
            # Show a single "error" row so the user sees feedback.
            r = self.server_overview_table.rowCount()
            self.server_overview_table.insertRow(r)
            self.server_overview_table.setItem(r, 0, QTableWidgetItem("Error"))
            self.server_overview_table.setItem(r, 1, QTableWidgetItem(f"Could not fetch server info: {exc}"))
            return
        preferred=[("Hostname","hostname"),("OS","os"),("Uptime","uptime"),("CPU cores","cpu"),("Load","load_short"),("CPU usage","cpu_percent"),("Memory used","memory_used"),("Memory total","memory_total"),("Memory %","memory_percent"),("Disk","disk"),("Docker","docker")]
        with self._atomic_table(self.server_overview_table, (0,)):
            self.server_overview_table.setRowCount(0)
            for label,key in preferred:
                value=str(snap.get(key,"—"))
                row=self.server_overview_table.rowCount()
                self.server_overview_table.insertRow(row)
                self.server_overview_table.setItem(row,0,QTableWidgetItem(label))
                self.server_overview_table.setItem(row,1,QTableWidgetItem(value))
                button=QToolButton(); button.setText("Copy")
                button.clicked.connect(lambda _=False,v=value:self._copy_text(v))
                self.server_overview_table.setCellWidget(row,2,button)


    async def _load_processes(self):
        """Load a fast process snapshot without blocking the Server page.

        ``ps`` is deliberately used for the interactive Server tab. The former
        two-sample ``top`` command added an avoidable 0.5s+ delay and could look
        like the tab was doing nothing while waiting for a worker shell.
        """
        if not self.current_id or self._server_process_loading:
            return
        ssh = self.manager.servers[self.current_id].ssh
        self._server_process_loading = True
        try:
            result = await ssh.run_fast(
                "ps -eo pid=,user=,%cpu=,%mem=,stat=,args= --sort=-%cpu 2>/dev/null | head -120",
                timeout=8,
            )
            if result.exit_code != 0 and not result.stdout.strip():
                raise RuntimeError(result.stderr.strip() or "ps returned no data")

            rows = []
            for line in result.stdout.splitlines():
                parts = line.strip().split(None, 5)
                if len(parts) < 6:
                    continue
                pid, user, cpu, mem, state, cmd = parts
                try:
                    cpu_val = float(cpu)
                except ValueError:
                    cpu_val = None
                try:
                    mem_val = float(mem)
                except ValueError:
                    mem_val = None
                rows.append((pid, user, cpu_val, mem_val, state, cmd))

            with self._atomic_table(self.process_table, (0,)):
                if not rows:
                    row = self.process_table.rowCount()
                    self.process_table.insertRow(row)
                    self.process_table.setSpan(row, 0, 1, self.process_table.columnCount())
                    item = QTableWidgetItem("No process data returned by ps")
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.process_table.setItem(row, 0, item)
                else:
                    for pid, user, cpu_val, mem_val, state, cmd in rows:
                        row = self.process_table.rowCount()
                        self.process_table.insertRow(row)
                        values = [pid, user, f"{cpu_val:.2f}" if cpu_val is not None else "—", f"{mem_val:.2f}" if mem_val is not None else "—", state, cmd]
                        for col, value in enumerate(values):
                            item = QTableWidgetItem(value)
                            if col == 2 and cpu_val is not None:
                                item.setForeground(QColor("#ff5260") if cpu_val >= 70 else QColor("#ffca4b") if cpu_val >= 30 else QColor("#39d47a"))
                            self.process_table.setItem(row, col, item)
                        self.process_table.setCellWidget(row, 6, self._process_action_widget(pid))
                        self.process_table.setRowHeight(row, 38)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Server process list refresh failed: %s", exc)
            self.statusBar().showMessage(f"Process refresh failed: {str(exc)[:160]}", 5000)
        finally:
            self._server_process_loading = False

    async def _load_services(self):
        try:
            # run_page keeps page-level SSH concurrency bounded by Settings.
            s = await self.manager.servers[self.current_id].ssh.run_page(
                "systemctl list-units --type=service --all --no-legend --no-pager --plain | head -180",
                timeout=15,
            )
        except Exception as exc:
            log.warning("Services list fetch failed: %s", exc)
            r = self.service_table.rowCount()
            self.service_table.insertRow(r)
            self.service_table.setItem(r, 0, QTableWidgetItem(f"Error: {exc}"))
            return
        # If systemctl returned an error (non-systemd host, permission
        # denied), show a helpful message instead of an empty table.
        if s.exit_code != 0 and not s.stdout.strip():
            r = self.service_table.rowCount()
            self.service_table.insertRow(r)
            err_msg = (s.stderr or "systemctl not available or returned no data").strip()[:200]
            self.service_table.setItem(r, 0, QTableWidgetItem(f"systemctl error: {err_msg}"))
            return
        with self._atomic_table(self.service_table, (0,)):
            for line in s.stdout.splitlines():
                parts=line.strip().split(None,4)
                if len(parts)<5 or not parts[0].endswith('.service'):continue
                unit,load,active,sub,desc=parts; r=self.service_table.rowCount(); self.service_table.insertRow(r)
                for c,val in enumerate([unit,load,active,sub,desc]): self.service_table.setItem(r,c,QTableWidgetItem(val))
                self.service_table.setCellWidget(r,5,self._service_action_widget(unit)); self.service_table.setRowHeight(r,38)


    def _copy_text(self,text):
        QApplication.clipboard().setText(str(text)); self.statusBar().showMessage("Copied",1500)

    def _install_table_copy_context(self, table):
        """Install the copy context handler once without unsafe signal disconnects."""
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        if table.property("_sshdock_copy_context_installed"):
            return
        handler = lambda pos, t=table: self._table_copy_menu(t, pos)
        table.setProperty("_sshdock_copy_context_installed", True)
        table.customContextMenuRequested.connect(handler)
        # Keep a strong reference to the callable for the lifetime of the table.
        table._sshdock_copy_context_handler = handler

    def _table_copy_menu(self, table, pos):
        """Universal table context: copy cell / row / selected / all rows."""
        menu = QMenu(table)
        row = table.rowAt(pos.y())
        col = table.columnAt(pos.x())

        copy_cell = menu.addAction("Copy cell")
        copy_row = menu.addAction("Copy row")
        copy_sel = menu.addAction("Copy selected rows")
        copy_all = menu.addAction("Copy all rows")
        menu.addSeparator()
        select_all = menu.addAction("Select all")
        clear_sel = menu.addAction("Clear selection")

        action = menu.exec(table.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action is copy_cell and row >= 0 and col >= 0:
            item = table.item(row, col)
            QApplication.clipboard().setText(item.text() if item else "")
        elif action is copy_row and row >= 0:
            QApplication.clipboard().setText(self._table_row_text(table, row))
        elif action is copy_sel:
            rows = sorted({i.row() for i in table.selectedIndexes()})
            if not rows and row >= 0:
                rows = [row]
            text_out = "\n".join(self._table_row_text(table, r) for r in rows)
            QApplication.clipboard().setText(text_out)
        elif action is copy_all:
            lines = [self._table_row_text(table, r) for r in range(table.rowCount())]
            # include header
            headers = []
            for c in range(table.columnCount()):
                h = table.horizontalHeaderItem(c)
                headers.append(h.text() if h else f"col{c}")
            QApplication.clipboard().setText("\t".join(headers) + "\n" + "\n".join(lines))
        elif action is select_all:
            table.selectAll()
        elif action is clear_sel:
            table.clearSelection()

    def _table_row_text(self, table, row: int) -> str:
        values = []
        for c in range(table.columnCount()):
            item = table.item(row, c)
            if item is not None:
                values.append(item.text())
            else:
                values.append("")
        return "\t".join(values)


    def _process_action_widget(self, pid):
        entries = [
            ("Terminate (TERM)", "fa5s.stop", lambda _=False, p=pid: self._spawn(self._manage_process(p, "TERM"))),
            ("Kill (KILL)", "fa5s.times", lambda _=False, p=pid: self._spawn(self._manage_process(p, "KILL"))),
            None,
            ("Copy PID", "fa5s.copy", lambda _=False, p=pid: QApplication.clipboard().setText(str(p))),
        ]
        return self._actions_cell(entries, f"Process {pid}")

    def _service_action_widget(self, unit):
        entries = [
            ("Start", "fa5s.play", lambda _=False, u=unit: self._spawn(self._manage_service(u, "start"))),
            ("Stop", "fa5s.stop", lambda _=False, u=unit: self._spawn(self._manage_service(u, "stop"))),
            ("Restart", "fa5s.redo", lambda _=False, u=unit: self._spawn(self._manage_service(u, "restart"))),
            None,
            ("Copy unit", "fa5s.copy", lambda _=False, u=unit: QApplication.clipboard().setText(u)),
        ]
        return self._actions_cell(entries, f"Service {unit}")

    async def load_host_logs(self):
        if not self.current_id:
            return
        # Clear the panel first so the user sees immediate feedback.
        self.host_logs.setPlainText("Loading logs…")
        try:
            # Add a 15s timeout — journalctl can hang on corrupt journals
            # or when the journal is very large. Previously this had no
            # timeout at all and could hang indefinitely.
            # Use run_page so page data follows the configured worker limit.
            r = await self.manager.servers[self.current_id].ssh.run_page(
                "journalctl --no-pager -n 250 2>&1 || echo '--- journalctl not available or failed ---'",
                timeout=15,
            )
            text = r.stdout or r.stderr or "(no output)"
        except Exception as exc:
            log.warning("Host logs fetch failed: %s", exc)
            text = f"Could not fetch host logs: {exc}\n\nThe SSH connection may have dropped, or journalctl is not available on this host."
        self.host_logs.setPlainText(text)
        # Jump to latest log line on load
        cursor = self.host_logs.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.host_logs.setTextCursor(cursor)
        self.host_logs.ensureCursorVisible()

    def _new_container_placeholder(self):
        """Guided Docker run wizard — image/volume/network suggestions + unique name check."""
        if not self.current_id:
            QMessageBox.information(self, "Create container", "Connect to a server first.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Create container")
        dlg.resize(720, 720)
        lay = QVBoxLayout(dlg)
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        img_wrap = NameSuggestEdit(
            kind="image", parent=dlg, placeholder="nginx:latest  (local images suggested below)",
        )
        image = img_wrap.edit

        name_wrap = NameSuggestEdit(
            kind="name", parent=dlg, placeholder="Optional — must be unique on this host",
        )
        name = name_wrap.edit

        port_wrap = PortCheckEdit(parent=dlg, placeholder="8080:80, 8443:443")
        ports = port_wrap.edit

        vol_wrap = NameSuggestEdit(
            kind="volume", parent=dlg,
            placeholder="named_vol:/data  or  /host:/container  (comma-separated)",
        )
        volumes = vol_wrap.edit
        vol_row = QHBoxLayout(); vol_row.setContentsMargins(0, 0, 0, 0)
        vol_row.addWidget(vol_wrap, 1)
        vol_pick = QPushButton("Browse…")
        vol_pick.setToolTip("Pick existing volumes from a searchable list")
        vol_row.addWidget(vol_pick, 0, Qt.AlignmentFlag.AlignTop)

        net_wrap = NameSuggestEdit(
            kind="network", parent=dlg, placeholder="bridge, mynet  (comma-separated)",
        )
        networks = net_wrap.edit
        net_row = QHBoxLayout(); net_row.setContentsMargins(0, 0, 0, 0)
        net_row.addWidget(net_wrap, 1)
        net_pick = QPushButton("Browse…")
        net_pick.setToolTip("Pick networks from a searchable list")
        net_row.addWidget(net_pick, 0, Qt.AlignmentFlag.AlignTop)

        env = QPlainTextEdit(); env.setPlaceholderText("KEY=value\nANOTHER=value"); env.setFixedHeight(90)
        env_row = QHBoxLayout(); env_row.setContentsMargins(0, 0, 0, 0)
        env_row.addWidget(env, 1)
        env_import = QPushButton("Import .env…")
        env_import.setToolTip("Load KEY=value pairs from a local .env file")
        env_row.addWidget(env_import, 0, Qt.AlignmentFlag.AlignTop)

        command = QLineEdit(); command.setPlaceholderText("Optional command override")
        restart = QComboBox(); restart.addItems(["no", "always", "unless-stopped", "on-failure"])
        privileged = QCheckBox("Privileged")
        rm_flag = QCheckBox("Remove on exit (--rm)  — ignored when detached")
        interactive = QCheckBox("Interactive TTY (-it) — keep stdin/TTY (needed for shell)")
        interactive.setToolTip(
            "Use docker run -dit so bash/sh stay alive, then open the app terminal with docker exec -it. "
            "Without this, a container whose only command is bash/sh exits immediately."
        )
        open_shell_after = QCheckBox("Open shell in app terminal after create")
        open_shell_after.setToolTip("After the container starts, open docker exec -it in SSHDock terminal")
        open_shell_after.setChecked(False)

        def _cmd_looks_like_shell(text: str) -> bool:
            t = (text or "").strip().lower()
            if not t:
                return False
            first = t.split()[0]
            base = first.rsplit("/", 1)[-1]
            return base in {"sh", "bash", "ash", "zsh", "fish", "dash", "ksh"} or t in {
                "/bin/sh", "/bin/bash", "/usr/bin/bash", "bin/sh", "bin/bash"
            }

        def _on_command_changed(_=None):
            if _cmd_looks_like_shell(command.text()):
                interactive.setChecked(True)
                open_shell_after.setChecked(True)

        command.textChanged.connect(_on_command_changed)

        form.addWidget(QLabel("Image *"), 0, 0, Qt.AlignmentFlag.AlignTop); form.addWidget(img_wrap, 0, 1)
        form.addWidget(QLabel("Name"), 1, 0, Qt.AlignmentFlag.AlignTop); form.addWidget(name_wrap, 1, 1)
        form.addWidget(QLabel("Ports"), 2, 0, Qt.AlignmentFlag.AlignTop); form.addWidget(port_wrap, 2, 1)
        form.addWidget(QLabel("Volumes"), 3, 0, Qt.AlignmentFlag.AlignTop); form.addLayout(vol_row, 3, 1)
        form.addWidget(QLabel("Networks"), 4, 0, Qt.AlignmentFlag.AlignTop); form.addLayout(net_row, 4, 1)
        form.addWidget(QLabel("Restart policy"), 5, 0); form.addWidget(restart, 5, 1)
        form.addWidget(QLabel("Command"), 6, 0); form.addWidget(command, 6, 1)
        form.addWidget(QLabel("Environment"), 7, 0, Qt.AlignmentFlag.AlignTop); form.addLayout(env_row, 7, 1)
        form.addWidget(privileged, 8, 1)
        form.addWidget(rm_flag, 9, 1)
        form.addWidget(interactive, 10, 1)
        form.addWidget(open_shell_after, 11, 1)
        lay.addLayout(form)
        hint = QLabel(
            "Image and volume/network names suggest from the server. "
            "Container name must be unique if set (Docker requirement)."
        )
        hint.setObjectName("muted"); hint.setWordWrap(True); lay.addWidget(hint)
        buttons = QHBoxLayout(); buttons.addStretch()
        cancel = QPushButton("Cancel"); create = QPushButton("Create"); create.setProperty("accent", True)
        buttons.addWidget(cancel); buttons.addWidget(create); lay.addLayout(buttons)
        cancel.clicked.connect(dlg.reject)

        cache: dict[str, list[str]] = {"volumes": [], "networks": [], "images": [], "containers": []}

        async def _refresh_cache():
            dm = DockerManager(self.manager.servers[self.current_id].ssh)
            vols, nets, imgs, containers = await asyncio.gather(
                dm.volumes(), dm.networks(), dm.images(), dm.ps(all_=True),
            )
            cache["volumes"] = [str(v.get("Name") or "") for v in vols if v.get("Name")]
            cache["networks"] = [str(n.get("Name") or "") for n in nets if n.get("Name")]
            # Image refs: repo:tag (skip dangling <none>)
            image_refs = []
            for im in imgs:
                repo = str(im.get("Repository") or "").strip()
                tag = str(im.get("Tag") or "").strip()
                iid = str(im.get("ID") or "").strip()
                if repo and repo != "<none>" and tag and tag != "<none>":
                    image_refs.append(f"{repo}:{tag}")
                elif repo and repo != "<none>":
                    image_refs.append(repo)
                elif iid:
                    image_refs.append(iid)
            cache["images"] = image_refs
            # Container names (strip leading /)
            cnames = []
            for c in containers:
                n = str(c.get("Names") or c.get("Name") or "").strip()
                if not n:
                    continue
                n = n.lstrip("/").split(",")[0].strip()
                if n:
                    cnames.append(n)
            cache["containers"] = cnames
            vol_wrap.set_candidates(cache["volumes"])
            net_wrap.set_candidates(cache["networks"])
            img_wrap.set_candidates(cache["images"])
            name_wrap.set_candidates(cache["containers"])
            try:
                used_ports = await dm.listening_ports()
            except Exception:
                used_ports = {}
            port_wrap.set_used_ports(used_ports)

        def _ensure_cache(then):
            async def _run():
                if not cache["volumes"] and not cache["networks"] and not cache["images"]:
                    await _refresh_cache()
                then()
            self._spawn(_run())

        def _browse_volumes():
            def open_picker():
                existing = []
                for part in volumes.text().split(","):
                    part = part.strip()
                    if not part or part.startswith("/"):
                        continue
                    existing.append(part.split(":", 1)[0])
                dlg_p = ResourcePickerDialog(
                    "Select volumes", cache["volumes"], selected=existing, multi=True, parent=dlg,
                    hint="Checked volumes are added as name:/data — edit mount path after pick if needed.",
                )
                if dlg_p.exec() == QDialog.DialogCode.Accepted:
                    current_parts = [x.strip() for x in volumes.text().split(",") if x.strip()]
                    current_names = set()
                    for p in current_parts:
                        if p.startswith("/"):
                            continue
                        current_names.add(p.split(":", 1)[0])
                    for n in dlg_p.selected_names:
                        if n not in current_names:
                            current_parts.append(f"{n}:/data")
                            current_names.add(n)
                    vol_wrap.setText(", ".join(current_parts))
            _ensure_cache(open_picker)

        def _browse_networks():
            def open_picker():
                existing = [x.strip() for x in networks.text().split(",") if x.strip()]
                dlg_p = ResourcePickerDialog(
                    "Select networks", cache["networks"], selected=existing, multi=True, parent=dlg,
                    hint="Search and check one or more networks.",
                )
                if dlg_p.exec() == QDialog.DialogCode.Accepted:
                    net_wrap.setText(", ".join(dlg_p.selected_names))
            _ensure_cache(open_picker)

        def _import_env():
            path, _ = QFileDialog.getOpenFileName(
                dlg, "Import .env file", "", "Env files (*.env .env*);;All files (*)"
            )
            if not path:
                return
            try:
                raw = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                QMessageBox.warning(dlg, "Import .env", str(exc))
                return
            lines = []
            for line in raw.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if s.startswith("export "):
                    s = s[7:].strip()
                if "=" in s:
                    lines.append(s)
            if not lines:
                QMessageBox.information(dlg, "Import .env", "No KEY=value lines found.")
                return
            existing = env.toPlainText().rstrip()
            block = "\n".join(lines)
            env.setPlainText((existing + "\n" + block).strip() if existing else block)
            QMessageBox.information(dlg, "Import .env", f"Imported {len(lines)} variable(s).")

        vol_pick.clicked.connect(_browse_volumes)
        net_pick.clicked.connect(_browse_networks)
        env_import.clicked.connect(_import_env)

        def submit():
            if not image.text().strip():
                QMessageBox.warning(dlg, "Create container", "Image is required.")
                return
            if not name_wrap.is_name_available():
                QMessageBox.warning(
                    dlg, "Create container",
                    f"Container name «{name.text().strip()}» is already in use.\n"
                    "Choose a unique name, or leave Name empty for an auto-generated one.",
                )
                return
            conflicts = port_wrap.conflicts()
            if conflicts:
                detail = ", ".join(f"{p} ({why})" for p, why in conflicts)
                reply = QMessageBox.question(
                    dlg,
                    "Ports in use",
                    f"These host ports are already in use on the server:\n{detail}\n\n"
                    "Create the container anyway? Docker may fail to bind them.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            task = self._spawn(self._create_container_exec(
                image.text().strip(), name.text().strip(), ports.text().strip(),
                env.toPlainText(), command.text().strip(), restart.currentText(),
                volumes.text().strip(), networks.text().strip(),
                privileged.isChecked(), rm_flag.isChecked(),
                interactive=interactive.isChecked(),
                open_shell=open_shell_after.isChecked(),
            ))
            task.add_done_callback(lambda _: dlg.accept())
        create.clicked.connect(submit)
        for w in (image, name, ports, volumes, networks, env, command):
            self._install_copy_context(w)
        self._spawn(_refresh_cache())
        dlg.exec()


    async def _create_container_exec(
        self, image, name, ports, env_text, command, restart,
        volumes="", network="", privileged=False, rm_flag=False,
        interactive=False, open_shell=False,
    ):
        # Always detach so the SSHDock UI is not blocked; add -it when interactive
        # so shells (bash/sh) keep a TTY and do not exit immediately.
        args = ["run", "-d", "--restart", restart]
        if interactive:
            args += ["-i", "-t"]
        if name:
            args += ["--name", name]
        for mapping in [p.strip() for p in ports.split(",") if p.strip()]:
            args += ["-p", mapping]
        for vol in [v.strip() for v in volumes.split(",") if v.strip()]:
            args += ["-v", vol]
        net_list = [n.strip() for n in network.split(",") if n.strip()]
        if net_list:
            args += ["--network", net_list[0]]
        if privileged:
            args.append("--privileged")
        if rm_flag:
            args.append("--rm")
        for line in env_text.splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                args += ["-e", line]
        args.append(image)
        shell_bin = None
        if command:
            parts = command.split()
            args += parts
            base = parts[0].rsplit("/", 1)[-1].lower() if parts else ""
            if base in {"sh", "bash", "ash", "zsh", "fish", "dash", "ksh"}:
                shell_bin = parts[0]
        dm = DockerManager(self.manager.servers[self.current_id].ssh)
        r = await dm.run(args, timeout=300)
        from app.ui.notifications import NotificationBus
        if r.exit_code == 0:
            cid = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else ""
            ref = name or (cid[:12] if cid else "")
            for extra in net_list[1:]:
                ar = await dm.run(["network", "connect", extra, ref or cid], timeout=60)
                if ar.exit_code != 0:
                    NotificationBus.instance().warning(
                        "Network attach failed",
                        f"{extra}: {(ar.stderr or ar.stdout or '')[:120]}",
                    )
            NotificationBus.instance().success(
                "Container created",
                ref or image,
            )
            await self.load_containers()
            # Show interactive shell in SSHDock terminal (docker exec -it)
            if open_shell and ref:
                exe = shell_bin or "sh"
                self._open_container_terminal(ref, shell=exe)
            elif interactive and ref and not open_shell:
                NotificationBus.instance().info(
                    "Interactive container",
                    f"«{ref}» started with TTY. Use Open shell on the container to attach.",
                )
        else:
            details = (r.stderr or r.stdout or "docker run failed").strip()
            ErrorDialog(
                "Create container failed",
                f"Could not create container from «{image}».",
                f"$ {r.command}\n\n{details}",
                self,
            ).exec()


    async def close_async(self):
        if getattr(self, "_closing_app", False):
            return
        self._closing_app = True
        try:
            if hasattr(self, "_overview_timer"): self._overview_timer.stop()
            if hasattr(self, "_task_refresh_timer"): self._task_refresh_timer.stop()
            await self.task_manager.cancel_all(timeout=2.0)
            self._tasks.clear()
            for sid in list(self.manager.servers):
                try:
                    await asyncio.wait_for(self.manager.disconnect(sid), timeout=2.0)
                except Exception:
                    log.debug("Failed to disconnect server %s during shutdown", sid, exc_info=True)
        finally:
            self._shutdown_finished = True

    def closeEvent(self, event):
        if getattr(self, "_shutdown_finished", False):
            event.accept(); return
        if self._shutdown_task is None or self._shutdown_task.done():
            event.ignore()
            self._shutdown_task = self._spawn(self._graceful_close(), "Application shutdown")
        else:
            event.ignore()

    async def _graceful_close(self):
        try:
            await asyncio.wait_for(self.close_async(), timeout=5.0)
        except Exception:
            log.debug("Graceful shutdown failed", exc_info=True)
        finally:
            if self._shutdown_event is not None:
                self._shutdown_event.set()
            self.hide()

# --- SFTP file browser helpers ---
def _resolve_remote_path(base: str, name: str) -> str:
    base = (base or '/').strip() or '/'
    if base == '~': base = '.'
    import posixpath
    return posixpath.normpath(posixpath.join(base, name))

async def _file_browser_refresh(self):
    if not self.current_id:
        return
    path = (self.file_path.text().strip() or "/").replace("\\", "/")
    if not path.startswith("/"):
        path = "/" + path
    path = posixpath.normpath(path) or "/"
    ssh = self.manager.servers[self.current_id].ssh
    entries = []
    last_exc = None
    for attempt in range(2):
        sftp = None
        try:
            sftp = await ssh.sftp()
            async for entry in sftp.scandir(path):
                entries.append(entry)
            break
        except Exception as exc:
            last_exc = exc
            entries.clear()
            if attempt == 0:
                try:
                    if sftp is not None:
                        sftp.exit()
                        await sftp.wait_closed()
                except Exception:
                    pass
                await asyncio.sleep(0.15)
                continue
            break

    if last_exc is not None and not entries:
        self.statusBar().showMessage(f"File browser failed: {last_exc}", 10000)
        log.exception("SFTP file browser failed")
        return

    try:
        self.file_table.setSortingEnabled(False)
        self.file_table.setRowCount(0)
        for entry in sorted(entries, key=lambda e: (not stat.S_ISDIR(getattr(e.attrs, "permissions", 0) or 0), str(e.filename).lower())):
            row = self.file_table.rowCount()
            self.file_table.insertRow(row)
            perms = getattr(entry.attrs, "permissions", 0) or 0
            is_dir = stat.S_ISDIR(perms)
            kind = "Folder" if is_dir else "File"
            size = getattr(entry.attrs, "size", None)
            mtime = getattr(entry.attrs, "mtime", None)
            details = "—" if is_dir else (f"{size:,} bytes" if isinstance(size, int) else "—")
            if mtime:
                import datetime as _dt
                details += f" · {_dt.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')}"
            name_item = QTableWidgetItem(str(entry.filename))
            name_item.setData(Qt.ItemDataRole.UserRole, {"path": posixpath.join(path, str(entry.filename)), "is_dir": is_dir})
            self.file_table.setItem(row, 0, name_item)
            self.file_table.setItem(row, 1, QTableWidgetItem(kind))
            self.file_table.setItem(row, 2, QTableWidgetItem(details))
        self.file_table.setSortingEnabled(True)
        self.file_path.setText(path)
        self.statusBar().showMessage(f"{len(entries)} items · {path}", 2500)
    except Exception as exc:
        self.statusBar().showMessage(f"File browser failed: {exc}", 10000)
        log.exception("SFTP file browser failed")

async def _file_browser_up(self):
    import posixpath
    path=self.file_path.text().strip() or '/'
    self.file_path.setText(posixpath.dirname(path.rstrip('/')) or '/')
    await self._file_browser_refresh()

async def _file_open_row(self,row:int):
    item=self.file_table.item(row,0)
    if not item:return
    meta=item.data(Qt.ItemDataRole.UserRole) or {}
    target=meta.get("path") or _resolve_remote_path(self.file_path.text(), item.text())
    if meta.get("is_dir") or (self.file_table.item(row,1) and self.file_table.item(row,1).text()=="Folder"):
        self.file_path.setText(target)
        await self._file_browser_refresh()
    else:
        await self._file_open_selected_row(row)

async def _file_open_selected_row(self,row:int):
    if not self.current_id:return
    item=self.file_table.item(row,0)
    if not item:return
    import posixpath, tempfile, os, subprocess
    meta=item.data(Qt.ItemDataRole.UserRole) or {}
    remote=meta.get("path") or _resolve_remote_path(self.file_path.text(),item.text())
    local=os.path.join(tempfile.gettempdir(), 'sshdock_preview_'+os.path.basename(remote))
    try:
        sftp=await self.manager.servers[self.current_id].ssh.sftp(); await sftp.get(remote,local)
        data=open(local,'r',encoding='utf-8',errors='replace').read()
        dlg=QDialog(self); dlg.setWindowTitle(remote); dlg.resize(1000,700)
        lay=QVBoxLayout(dlg); edit=QPlainTextEdit(); edit.setPlainText(data); edit.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu); lay.addWidget(edit)
        dlg.exec()
    except Exception as exc:
        QMessageBox.warning(self,'Open file',str(exc))

async def _file_open_selected(self):
    row=self.file_table.currentRow()
    if row>=0: await self._file_open_row(row)

async def _file_download_selected(self):
    if not self.current_id:return
    row=self.file_table.currentRow()
    if row<0:return
    from PySide6.QtWidgets import QFileDialog
    import os
    item=self.file_table.item(row,0)
    if not item:return
    from pathlib import Path
    meta=item.data(Qt.ItemDataRole.UserRole) or {}
    remote=meta.get("path") or _resolve_remote_path(self.file_path.text(),item.text())
    local,_=QFileDialog.getSaveFileName(self,'Download',Path(item.text()).name)
    if not local:return
    try:
        sftp=await self.manager.servers[self.current_id].ssh.sftp(); await sftp.get(remote,local)
        self.statusBar().showMessage(f'Downloaded {remote}',5000)
    except Exception as exc: QMessageBox.warning(self,'Download',str(exc))

def _file_context_menu(self,pos):
    item=self.file_table.itemAt(pos); menu=QMenu(self); copy_name=menu.addAction('Copy name'); copy_path=menu.addAction('Copy path'); menu.addSeparator(); open_a=menu.addAction('Open'); dl=menu.addAction('Download')
    chosen=menu.exec(self.file_table.viewport().mapToGlobal(pos))
    if not item:return
    name=item.text(); meta=item.data(Qt.ItemDataRole.UserRole) or {}; path=meta.get("path") or _resolve_remote_path(self.file_path.text(),name)
    if chosen is copy_name: QApplication.clipboard().setText(name)
    elif chosen is copy_path: QApplication.clipboard().setText(path)
    elif chosen is open_a: self._spawn(self._file_open_selected())
    elif chosen is dl: self._spawn(self._file_download_selected())


async def _file_upload(self):
    if not self.current_id:return
    from PySide6.QtWidgets import QFileDialog
    local,_=QFileDialog.getOpenFileName(self,"Upload file")
    if not local:return
    remote=_resolve_remote_path(self.file_path.text(), os.path.basename(local))
    try:
        sftp=await self.manager.servers[self.current_id].ssh.sftp(); await sftp.put(local,remote); await self._file_browser_refresh(); self.statusBar().showMessage(f"Uploaded {remote}",5000)
    except Exception as exc: QMessageBox.warning(self,"Upload",str(exc))

async def _file_new_folder(self):
    if not self.current_id:return
    from PySide6.QtWidgets import QInputDialog
    name,ok=QInputDialog.getText(self,"New folder","Folder name:")
    if not ok or not name.strip():return
    remote=_resolve_remote_path(self.file_path.text(),name.strip())
    try:
        sftp=await self.manager.servers[self.current_id].ssh.sftp(); await sftp.mkdir(remote); await self._file_browser_refresh()
    except Exception as exc: QMessageBox.warning(self,"New folder",str(exc))

async def _file_rename_selected(self):
    if not self.current_id:return
    row=self.file_table.currentRow()
    if row<0:return
    item=self.file_table.item(row,0)
    if not item:return
    from PySide6.QtWidgets import QInputDialog
    name,ok=QInputDialog.getText(self,"Rename","New name:",text=item.text())
    if not ok or not name.strip():return
    old=_resolve_remote_path(self.file_path.text(),item.text()); new=_resolve_remote_path(self.file_path.text(),name.strip())
    try:
        sftp=await self.manager.servers[self.current_id].ssh.sftp(); await sftp.rename(old,new); await self._file_browser_refresh()
    except Exception as exc: QMessageBox.warning(self,"Rename",str(exc))

async def _file_delete_selected(self):
    if not self.current_id:return
    row=self.file_table.currentRow()
    if row<0:return
    item=self.file_table.item(row,0); typ=self.file_table.item(row,1).text() if self.file_table.item(row,1) else "File"
    if not item:return
    if QMessageBox.question(self,"Delete",f"Delete {item.text()}?") != QMessageBox.StandardButton.Yes:return
    remote=_resolve_remote_path(self.file_path.text(),item.text())
    try:
        sftp=await self.manager.servers[self.current_id].ssh.sftp()
        if typ=="Directory": await sftp.rmtree(remote)
        else: await sftp.remove(remote)
        await self._file_browser_refresh()
    except Exception as exc: QMessageBox.warning(self,"Delete",str(exc))

MainWindow._file_upload=_file_upload
MainWindow._file_new_folder=_file_new_folder
MainWindow._file_rename_selected=_file_rename_selected
MainWindow._file_delete_selected=_file_delete_selected

MainWindow._file_browser_refresh=_file_browser_refresh
MainWindow._file_browser_up=_file_browser_up
MainWindow._file_open_row=_file_open_row
MainWindow._file_open_selected_row=_file_open_selected_row
MainWindow._file_open_selected=_file_open_selected
MainWindow._file_download_selected=_file_download_selected
MainWindow._file_context_menu=_file_context_menu
