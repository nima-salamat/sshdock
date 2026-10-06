DARK = """
QWidget { background:#0f1115; color:#e9edf2; font-family:'Segoe UI'; font-size:13px; }
QMainWindow { background:#0f1115; }
#sidebar { background:#0b0d10; border-right:1px solid #1d2229; }
#brand { font-size:21px; font-weight:700; color:#f5f7fa; }
#brandSub { color:#718096; font-size:11px; }
#serverCard { background:#15191f; border:1px solid #232932; border-radius:9px; }
#serverName { font-size:13px; font-weight:700; color:#f8fafc; }
#muted { color:#8a95a4; }
#sectionLabel { color:#66717f; font-size:10px; font-weight:700; padding:9px 8px 3px; }
/* Sidebar nav (frame-based NavButton) — soft selected state, not solid blue */
#navBtnFrame { background:transparent; border:0; border-radius:8px; }
#navBtnFrame:hover { background:#171b21; }
#navBtnFrame[checked="true"] { background:#152238; border:1px solid #243a5c; }
#navBtnLabel { font-size:13px; background:transparent; }

#navIcon { background: transparent; border: 0; }
QToolButton#sidebarToggle {
  background: transparent; border: 0; border-radius: 6px; padding: 4px;
}
QToolButton#sidebarToggle:hover { background: #171b21; }

#navShortcut { font-family: "Segoe UI", "SF Mono", "Menlo", monospace; font-size:11px; color:#5c6775; letter-spacing:0.2px; background:transparent; }
#navAccent { background:transparent; border-radius:2px; }
QToolButton#navBtn { text-align:left; background:transparent; border:0; border-radius:8px; color:#9ca7b5; padding:10px 11px; spacing:8px; }
QToolButton#navBtn:hover { background:#171b21; color:#e7edf3; }
QToolButton#navBtn:checked { background:#152238; color:#e8f0ff; border:1px solid #243a5c; }

#topbar { min-height: 44px; background:#15181d; border-bottom:1px solid #242a31; }
#serverPicker { min-height: 32px; max-height: 34px; padding: 2px 8px; }
#serverPicker QAbstractItemView::item { min-height: 28px; padding: 4px 8px; }
#topTitle { font-size:15px; font-weight:700; }
#stateConnected { color:#41d17d; font-weight:600; }
#stateDisconnected { color:#8a95a4; font-weight:600; }
#stateReconnecting { color:#ffca4b; font-weight:600; }
#metricCard { background:#15191f; border:1px solid #252b33; border-radius:10px; }
#metricHeading { color:#7d8794; font-size:12px; }
#metricValue { color:#f6f8fb; font-size:22px; font-weight:700; }
#heroTitle { font-size:27px; font-weight:700; }
#heroSubtitle { color:#8d97a5; }
#panel { background:#15191f; border:1px solid #252b33; border-radius:10px; }
#panelTitle { font-size:14px; font-weight:700; }
#toolbar { background:#12161b; border:1px solid #252b33; border-radius:9px; }
#subNav { background:#11151a; border-bottom:1px solid #242b33; }
QPushButton { background:#191e24; border:1px solid #2a313a; border-radius:7px; padding:8px 12px; color:#e3e8ee; }
QPushButton:hover { background:#20262d; }
QPushButton:pressed { background:#15191e; }
QPushButton:disabled { color:#647080; background:#15191e; border-color:#232a32; }
QPushButton[accent=true] { background:#1d6fe8; border-color:#2e7cf0; color:#fff; }
QPushButton[accent=true]:hover { background:#2879ee; }
QPushButton[danger=true] { background:#251619; border-color:#5a2b32; color:#ff939b; }
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QPlainTextEdit,QTextEdit { background:#0d1014; border:1px solid #29313a; border-radius:7px; padding:7px; selection-background-color:#215fae; }
QComboBox::drop-down { border:0; width:22px; }
QTableWidget { background:#11151a; border:1px solid #252c34; border-radius:9px; gridline-color:#1d232a; selection-background-color:#1a385f; }
QHeaderView::section { background:#171b20; color:#8b96a4; padding:9px; border:0; border-bottom:1px solid #252c34; font-weight:600; }
QTableWidget::item { padding:7px 6px; }
QTableWidget::item:alternate { background:#141920; }
QTableWidget::item:selected { background:#1a385f; }
QTabWidget::pane { border:1px solid #252c34; background:#11151a; }
QTabBar::tab { background:#15191f; color:#7e8997; padding:8px 13px; border:0; }
QTabBar::tab:selected { background:#1d6fe8; color:#fff; }
#statusPill { border-radius:10px; padding:3px 8px; font-size:11px; }
#statusRunning { background:#173b29; color:#66e39a; }
#statusStopped { background:#22272d; color:#aab3be; }
#statusProblem { background:#3a2022; color:#ff939b; }
#subtleAction { padding:5px 7px; font-size:11px; min-height:30px; }
#subtleAction:hover { border-color:#4c83c4; background:#1b2735; }
#emptyState { color:#788493; font-size:14px; }
#terminalToolbar { background:#101419; border-bottom:1px solid #252b33; min-height:30px; }
#terminalToolbar QToolButton, #terminalToolbar QPushButton { min-height:26px; max-height:28px; padding:2px 7px; border-radius:5px; }
#terminalToolbarStatus { color:#93a1b1; font-size:11px; padding-left:6px; }
QStatusBar { background:#11151a; color:#768291; border-top:1px solid #252b33; }
QScrollBar:vertical { background:#0f1115; width:10px; }
QScrollBar::handle:vertical { background:#667585; min-height:46px; border-radius:6px; border:1px solid #8392a3; }
QScrollBar::handle:vertical:hover { background:#8ea0b2; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QScrollBar:horizontal { background:#0f1115; height:12px; }
QScrollBar::handle:horizontal { background:#667585; min-width:46px; border-radius:6px; border:1px solid #8392a3; }
QScrollBar::handle:horizontal:hover { background:#8ea0b2; }

# Terminal panel / accessibility overrides
#terminalPanel { background:#0b0f14; border-top:1px solid #2b333d; min-height:120px; }
#terminalFooter { background:#11151a; border-top:1px solid #303944; }
QToolButton#terminalToggle { background:transparent; border:0; padding:3px 8px; color:#cbd5e1; }
QToolButton#terminalToggle:hover { background:#1b2430; }
QTableWidget QPushButton { min-height:26px; max-height:28px; padding:2px 8px; font-weight:600; }
QTableWidget QWidget { background: transparent; }
QTableWidget QToolButton#rowMenuButton {
  min-height:26px; max-height:28px; padding:2px 10px; font-size:12px;
  background:#191e24; border:1px solid #2a313a; border-radius:6px; color:#e3e8ee;
}
QTableWidget QToolButton#rowMenuButton:hover { background:#20262d; border-color:#3a4552; }
QTableWidget::item { padding:2px 4px; }
#subtleAction { min-height:26px; max-height:28px; padding:2px 6px; }

QScrollBar:vertical { background:#0b0f14; width:14px; margin:0; border:1px solid #2b333d; }
QScrollBar::groove:vertical { background:#171d24; border:1px solid #242c35; border-radius:6px; }
QScrollBar::handle:vertical { background:#7f8fa3; min-height:54px; border-radius:6px; border:2px solid #b2bfcc; }
QScrollBar::handle:vertical:hover { background:#a7b6c8; border-color:#d7e0ea; }
QScrollBar:horizontal { background:#0b0f14; height:14px; margin:0; border:1px solid #2b333d; }
QScrollBar::groove:horizontal { background:#171d24; border:1px solid #242c35; border-radius:6px; }
QScrollBar::handle:horizontal { background:#7f8fa3; min-width:54px; border-radius:6px; border:2px solid #b2bfcc; }
QScrollBar::handle:horizontal:hover { background:#a7b6c8; border-color:#d7e0ea; }

/* Splitter - easy to grab for terminal resize */
QSplitter::handle:vertical {
  height: 8px;
  background: #252b33;
  margin: 0 8px;
  border-radius: 3px;
}
QSplitter::handle:vertical:hover { background: #3b82f6; }
QSplitter::handle:vertical:pressed { background: #2563eb; }
"""


LIGHT = """
QWidget { background:#f4f6f9; color:#1e293b; font-family:'Segoe UI'; font-size:13px; }
QMainWindow { background:#f4f6f9; }
#sidebar { background:#eef1f6; border-right:1px solid #d5dbe6; }
#brand { font-size:21px; font-weight:700; color:#0f172a; }
#brandSub { color:#64748b; font-size:11px; }
#serverCard { background:#ffffff; border:1px solid #d5dbe6; border-radius:9px; }
#serverName { font-size:13px; font-weight:700; color:#0f172a; }
#muted { color:#64748b; }
#sectionLabel { color:#64748b; font-size:10px; font-weight:700; padding:9px 8px 3px; }
#navBtnFrame { background:transparent; border:0; border-radius:8px; }
#navBtnFrame:hover { background:#e2e8f0; }
#navBtnFrame[checked="true"] { background:#dbeafe; border:1px solid #93c5fd; }
#navBtnLabel { font-size:13px; background:transparent; color:#334155; }
#navIcon { background: transparent; border: 0; }
QToolButton#sidebarToggle { background: transparent; border: 0; border-radius: 6px; padding: 4px; }
QToolButton#sidebarToggle:hover { background: #e2e8f0; }
#navShortcut { font-family: "Segoe UI", "SF Mono", "Menlo", monospace; font-size:11px; color:#94a3b8; background:transparent; }
QToolButton#navBtn { text-align:left; background:transparent; border:0; border-radius:8px; color:#475569; padding:10px 11px; }
QToolButton#navBtn:hover { background:#e2e8f0; color:#0f172a; }
QToolButton#navBtn:checked { background:#dbeafe; color:#1e40af; border:1px solid #93c5fd; }
#topbar { min-height: 44px; background:#ffffff; border-bottom:1px solid #d5dbe6; }
#heroTitle { font-size:22px; font-weight:700; color:#0f172a; }
#panel { background:#ffffff; border:1px solid #d5dbe6; border-radius:10px; }
#metricHeading { color:#64748b; font-size:12px; font-weight:600; }
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
  background:#ffffff; border:1px solid #cbd5e1; border-radius:8px; padding:6px 8px; color:#1e293b;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {
  border-color:#3b82f6;
}
QPushButton {
  background:#e2e8f0; border:1px solid #cbd5e1; border-radius:8px; padding:6px 12px; color:#1e293b; font-weight:600;
}
QPushButton:hover { background:#cbd5e1; }
QPushButton[accent="true"] { background:#2563eb; border-color:#1d4ed8; color:#fff; }
QPushButton[accent="true"]:hover { background:#1d4ed8; }
QPushButton[danger="true"] { background:#fee2e2; border-color:#fca5a5; color:#b91c1c; }
QTableWidget { background:#ffffff; border:1px solid #d5dbe6; border-radius:9px; gridline-color:#e2e8f0; selection-background-color:#dbeafe; color:#1e293b; }
QHeaderView::section { background:#f1f5f9; color:#475569; padding:9px; border:0; border-bottom:1px solid #d5dbe6; font-weight:600; }
QTableWidget::item:alternate { background:#f8fafc; }
QTableWidget::item:selected { background:#dbeafe; color:#0f172a; }
QTabWidget::pane { border:1px solid #d5dbe6; background:#ffffff; }
QTabBar::tab { background:#eef1f6; color:#64748b; padding:8px 13px; border:0; }
QTabBar::tab:selected { background:#2563eb; color:#fff; }
QStatusBar { background:#ffffff; color:#64748b; border-top:1px solid #d5dbe6; }
QScrollBar:vertical { background:#f1f5f9; width:10px; }
QScrollBar::handle:vertical { background:#94a3b8; min-height:46px; border-radius:6px; }
QMenu { background:#ffffff; border:1px solid #d5dbe6; color:#1e293b; }
QMenu::item:selected { background:#dbeafe; }
QToolTip { padding:6px 8px; border:1px solid #d5dbe6; background:#ffffff; color:#1e293b; }
#terminalToolbar { background:#f1f5f9; border-bottom:1px solid #d5dbe6; min-height:30px; }
QSplitter::handle:vertical { height: 8px; background: #d5dbe6; margin: 0 8px; border-radius: 3px; }
QSplitter::handle:vertical:hover { background: #3b82f6; }
"""


def theme_stylesheet(name: str) -> str:
    """Return stylesheet for 'dark' or 'light'."""
    return LIGHT if str(name).lower() == "light" else DARK
