from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class MetricCard(QFrame):
    def __init__(self, title: str, value: str = "—"):
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        self.heading = QLabel(str(title))
        self.heading.setObjectName("metricHeading")
        self.value = QLabel(str(value) if value is not None else "—")
        self.value.setObjectName("metricValue")
        layout.addWidget(self.heading)
        layout.addWidget(self.value)

    def set_value(self, text: str):
        self.value.setText(str(text) if text is not None else "—")
