from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QLabel,
)

from app.core.models import ProxySettings, ProxyType


class ProxySettingsWidget(QWidget):
    """Reusable SSH proxy editor shared by Connect and Settings."""

    changed = Signal()

    def __init__(
        self,
        parent=None,
        *,
        enabled_label: str = "Use proxy for this SSH connection",
    ):
        super().__init__(parent)
        self.setObjectName("proxySettingsWidget")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self.enabled = QCheckBox(enabled_label)
        self.enabled.toggled.connect(self._sync_enabled)
        self.enabled.toggled.connect(self.changed)
        root.addWidget(self.enabled)

        form = QFormLayout()
        form.setContentsMargins(8, 0, 0, 0)
        form.setSpacing(8)

        self.type = QComboBox()
        self.type.addItem("SOCKS5", ProxyType.SOCKS5.value)
        self.type.addItem("HTTP CONNECT", ProxyType.HTTP_CONNECT.value)
        self.type.currentIndexChanged.connect(self._type_changed)
        self.type.currentIndexChanged.connect(self.changed)

        self.host = QLineEdit()
        self.host.setPlaceholderText("proxy hostname or IP")
        self.host.textChanged.connect(self.changed)

        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(1080)
        self.port.valueChanged.connect(self.changed)

        form.addRow("Type", self.type)
        form.addRow("Host", self.host)
        form.addRow("Port", self.port)
        root.addLayout(form)

        hint = QLabel(
            "The proxy is used only for the SSH transport. SSH traffic is still "
            "end-to-end encrypted. Supported modes: SOCKS5 and HTTP CONNECT."
        )
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        root.addWidget(hint)

        self._last_default_port = 1080
        self._sync_enabled(False)

    def set_config(self, config: ProxySettings | None) -> None:
        cfg = config or ProxySettings()
        self.enabled.blockSignals(True)
        self.type.blockSignals(True)
        self.host.blockSignals(True)
        self.port.blockSignals(True)
        try:
            idx = self.type.findData(cfg.type.value)
            self.type.setCurrentIndex(idx if idx >= 0 else 0)
            self.host.setText(cfg.host)
            self.port.setValue(max(1, min(65535, int(cfg.port))))
            self.enabled.setChecked(bool(cfg.enabled))
            self._last_default_port = 1080 if cfg.type is ProxyType.SOCKS5 else 8080
        finally:
            self.enabled.blockSignals(False)
            self.type.blockSignals(False)
            self.host.blockSignals(False)
            self.port.blockSignals(False)
        self._sync_enabled(self.enabled.isChecked())

    def config(self) -> ProxySettings:
        try:
            proxy_type = ProxyType(self.type.currentData())
        except ValueError:
            proxy_type = ProxyType.SOCKS5
        return ProxySettings(
            enabled=self.enabled.isChecked(),
            type=proxy_type,
            host=self.host.text().strip(),
            port=int(self.port.value()),
        )

    def validation_error(self) -> str | None:
        cfg = self.config()
        if not cfg.enabled:
            return None
        if not cfg.host:
            return "Enter a proxy hostname or IP address."
        if not (1 <= cfg.port <= 65535):
            return "Enter a valid proxy port."
        return None

    def _sync_enabled(self, enabled: bool) -> None:
        for widget in (self.type, self.host, self.port):
            widget.setEnabled(bool(enabled))

    def _type_changed(self, index: int) -> None:
        current = self.port.value()
        old_default = self._last_default_port
        new_type = self.type.itemData(index)
        new_default = 1080 if new_type == ProxyType.SOCKS5.value else 8080
        if current == old_default:
            self.port.blockSignals(True)
            self.port.setValue(new_default)
            self.port.blockSignals(False)
        self._last_default_port = new_default
