from __future__ import annotations

from app.core.runtime import create_task
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QFrame, QComboBox, QPushButton, QScrollArea, QWidget


class GaugeWidget(QWidget):
    def __init__(self, title: str, suffix: str = "%", parent=None):
        super().__init__(parent)
        self.title = title
        self.suffix = suffix
        self.value = 0.0
        self.setMinimumSize(160, 150)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def setValue(self, value: float):
        self.value = max(0.0, min(100.0, float(value)))
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#111722"))
        cx = self.width() / 2
        cy = self.height() / 2 + 8
        radius = min(self.width(), self.height()) * 0.34
        pen = QPen(QColor("#273247"), 11)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        p.drawArc(rect, 225 * 16, -270 * 16)
        color = QColor("#32d583")
        if self.value >= 85: color = QColor("#ff525e")
        elif self.value >= 65: color = QColor("#ffb84d")
        p.setPen(QPen(color, 11, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 225 * 16, int(-270 * 16 * self.value / 100.0))
        p.setPen(QColor("#f4f7fb"))
        f = QFont(); f.setPointSize(19); f.setBold(True); p.setFont(f)
        p.drawText(QRectF(0, cy - 16, self.width(), 35), Qt.AlignmentFlag.AlignCenter, f"{self.value:.0f}{self.suffix}")
        f2 = QFont(); f2.setPointSize(9); f2.setBold(False); p.setFont(f2); p.setPen(QColor("#8e9bb2"))
        p.drawText(QRectF(0, 12, self.width(), 22), Qt.AlignmentFlag.AlignCenter, self.title)


class ResourceMonitorDialog(QDialog):
    def __init__(self, monitor, parent=None):
        super().__init__(parent)
        self.monitor = monitor
        self.setWindowTitle("Host Resource Monitor")
        self.resize(1220, 820)
        self.setMinimumSize(900, 650)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._gauges: dict[int, GaugeWidget] = {}
        self._pending = False
        self._build()
        self._set_interval("2 s")
        self.refresh()

    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(18,18,18,18); root.setSpacing(12)
        head = QHBoxLayout()
        title = QLabel("Host Resource Monitor"); title.setStyleSheet("font-size:22px;font-weight:700;")
        head.addWidget(title); head.addStretch()
        self.interval = QComboBox(); self.interval.addItems(["Off","2 s","5 s","10 s"]); self.interval.setCurrentText("2 s")
        self.interval.currentTextChanged.connect(self._set_interval); head.addWidget(QLabel("Refresh")); head.addWidget(self.interval)
        refresh = QPushButton("↻ Refresh"); refresh.clicked.connect(self.refresh); head.addWidget(refresh)
        self.status = QLabel("Ready"); self.status.setObjectName("resourceStatus"); self.status.setStyleSheet("color:#8e9bb2"); head.addWidget(self.status)
        close = QPushButton("Close"); close.clicked.connect(self.close); head.addWidget(close)
        root.addLayout(head)

        summary = QHBoxLayout()
        self.cpu = GaugeWidget("CPU total")
        self.memory = GaugeWidget("Memory")
        self.swap = GaugeWidget("Swap")
        summary.addWidget(self.cpu, 1); summary.addWidget(self.memory, 1); summary.addWidget(self.swap, 1)
        self.load_lbl = QLabel("Load: —\nProcesses: —\nDisk: —\nUptime: —")
        self.load_lbl.setStyleSheet("color:#b8c1d1;font-size:13px;padding:12px;")
        summary.addWidget(self.load_lbl, 1)
        root.addLayout(summary)

        core_header = QHBoxLayout(); lbl=QLabel("CPU cores"); lbl.setStyleSheet("font-size:17px;font-weight:700;"); core_header.addWidget(lbl); core_header.addStretch(); self.core_meta=QLabel("— cores"); self.core_meta.setStyleSheet("color:#7f8da5"); core_header.addWidget(self.core_meta); root.addLayout(core_header)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.core_container = QWidget(); self.core_grid = QGridLayout(self.core_container); self.core_grid.setContentsMargins(0,0,0,0); self.core_grid.setSpacing(10)
        scroll.setWidget(self.core_container); root.addWidget(scroll, 1)

    def _set_interval(self, text):
        seconds = {"2 s":2, "5 s":5, "10 s":10}.get(text)
        if seconds is None: self._timer.stop()
        else: self._timer.start(seconds * 1000)

    async def _refresh_async(self):
        self.status.setText("Loading…")
        data = await self.monitor.detailed_snapshot()
        self.cpu.setValue(data.get("cpu_percent", 0))
        self.memory.setValue(data.get("memory_percent", 0))
        self.swap.setValue(data.get("swap_percent", 0))
        self.load_lbl.setText(f"Load: {data.get('load','—')}\nProcesses: {data.get('processes','—')}\nDisk: {data.get('disk','—')}\nUptime: {data.get('uptime','—')}")
        cores = data.get("core_percent", [])
        self.core_meta.setText(f"{len(cores)} cores")
        for i, value in enumerate(cores):
            if i not in self._gauges:
                g = GaugeWidget(f"Core {i}")
                self._gauges[i] = g
                row, col = divmod(i, 4)
                self.core_grid.addWidget(g, row, col)
            self._gauges[i].setValue(value)
        for i, g in list(self._gauges.items()):
            g.setVisible(i < len(cores))
        self.status.setText("Updated")

    def refresh(self):
        import asyncio
        if self._pending:
            return
        self._pending = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = create_task(self._refresh_async())
        def done(t):
            self._pending = False
            if not t.cancelled():
                try: t.result()
                except Exception as exc: self.findChild(QLabel, "resourceStatus").setText(str(exc)[:180]) if self.findChild(QLabel, "resourceStatus") else None
        task.add_done_callback(done)

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)
