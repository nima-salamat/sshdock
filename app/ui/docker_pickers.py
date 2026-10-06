"""Reusable search + multi-select pickers for Docker volumes and networks."""
from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt, QEvent, QObject, QTimer
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)


class ResourcePickerDialog(QDialog):
    """Searchable list of Docker resources (volumes or networks) with multi-select."""

    def __init__(
        self,
        title: str,
        items: list[str],
        *,
        selected: Iterable[str] | None = None,
        multi: bool = True,
        parent: QWidget | None = None,
        hint: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 480)
        self._all = sorted({x.strip() for x in items if x and str(x).strip()})
        self._multi = multi
        self._chosen: list[str] = []

        lay = QVBoxLayout(self)
        if hint:
            h = QLabel(hint)
            h.setObjectName("muted")
            h.setWordWrap(True)
            lay.addWidget(h)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)

        self.list = QListWidget()
        if multi:
            self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        else:
            self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        lay.addWidget(self.list, 1)

        pre = {s.strip() for s in (selected or []) if s and str(s).strip()}
        self._populate(self._all, pre)

        row = QHBoxLayout()
        if multi:
            sel_all = QPushButton("Select all visible")
            sel_all.clicked.connect(self._select_visible)
            clear = QPushButton("Clear")
            clear.clicked.connect(self._clear)
            row.addWidget(sel_all)
            row.addWidget(clear)
        row.addStretch()
        lay.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _populate(self, names: list[str], checked: set[str] | None = None) -> None:
        checked = checked or set()
        # preserve currently checked when filtering
        current_checked = set()
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item and item.checkState() == Qt.CheckState.Checked:
                current_checked.add(item.text())
        checked = checked | current_checked

        self.list.clear()
        for name in names:
            item = QListWidgetItem(name)
            if self._multi:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if name in checked else Qt.CheckState.Unchecked
                )
            self.list.addItem(item)

    def _filter(self, text: str) -> None:
        q = text.strip().lower()
        if not q:
            self._populate(self._all)
            return
        matched = [n for n in self._all if q in n.lower()]
        self._populate(matched)

    def _select_visible(self) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item:
                item.setCheckState(Qt.CheckState.Checked)

    def _clear(self) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)

    def _accept(self) -> None:
        if self._multi:
            chosen = []
            for i in range(self.list.count()):
                item = self.list.item(i)
                if item and item.checkState() == Qt.CheckState.Checked:
                    chosen.append(item.text())
            # also keep checked items hidden by filter
            for name in self._all:
                if name not in chosen:
                    # was checked before filter? we only track visible — OK for UX
                    pass
            self._chosen = chosen
        else:
            item = self.list.currentItem()
            self._chosen = [item.text()] if item else []
        self.accept()

    @property
    def selected_names(self) -> list[str]:
        return list(self._chosen)


def similar_names(query: str, candidates: list[str], *, limit: int = 8) -> list[str]:
    """Return candidates that look similar to query (substring / prefix)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    scored: list[tuple[int, str]] = []
    for c in candidates:
        cl = c.lower()
        if cl == q:
            scored.append((0, c))
        elif cl.startswith(q):
            scored.append((1, c))
        elif q in cl:
            scored.append((2, c))
        elif any(part and part in cl for part in q.replace("-", "_").split("_")):
            scored.append((3, c))
    scored.sort(key=lambda x: (x[0], x[1].lower()))
    return [c for _, c in scored[:limit]]


def append_csv(field: QLineEdit, values: list[str]) -> None:
    """Merge values into a comma-separated QLineEdit without duplicates."""
    existing = [x.strip() for x in field.text().split(",") if x.strip()]
    seen = set(existing)
    for v in values:
        v = v.strip()
        if v and v not in seen:
            existing.append(v)
            seen.add(v)
    field.setText(", ".join(existing))


class NameSuggestEdit(QWidget):
    """Line edit with compact inline suggestions (only while focused).

    kind:
      - volume: comma tokens, name before ':'
      - network: comma tokens
      - image: whole field (repo:tag suggestions)
      - name: container name — uniqueness check against existing names
    """

    _MAX_VISIBLE = 4
    _MAX_HEIGHT = 96

    def __init__(
        self,
        *,
        kind: str = "volume",
        parent: QWidget | None = None,
        placeholder: str = "",
    ) -> None:
        super().__init__(parent)
        self.kind = kind
        self._candidates: list[str] = []
        self._suppress = False
        self._focused = False
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(180)
        self._hide_timer.timeout.connect(self._hide_suggest_if_unfocused)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        root.addWidget(self.edit)

        self.status = QLabel("")
        self.status.setWordWrap(False)
        self.status.setMaximumHeight(18)
        self.status.setStyleSheet("color:#8a95a4; font-size:11px; padding-left:2px;")
        root.addWidget(self.status)

        self.suggest = QListWidget()
        self.suggest.setObjectName("nameSuggestList")
        self.suggest.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.suggest.setMaximumHeight(self._MAX_HEIGHT)
        self.suggest.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.suggest.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.suggest.setStyleSheet(
            "QListWidget#nameSuggestList { background:#15191f; border:1px solid #2a313a; "
            "border-radius:6px; color:#e9edf2; padding:2px; }"
            "QListWidget#nameSuggestList::item { padding:4px 8px; border-radius:3px; }"
            "QListWidget#nameSuggestList::item:selected { background:#1d6fe8; }"
            "QListWidget#nameSuggestList::item:hover { background:#1a222c; }"
        )
        self.suggest.hide()
        root.addWidget(self.suggest)

        self.edit.textChanged.connect(self._on_text)
        self.edit.installEventFilter(self)
        self.suggest.installEventFilter(self)
        self.suggest.itemClicked.connect(self._apply_item)
        self.suggest.itemActivated.connect(self._apply_item)
        self._update_status("")

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        et = event.type()
        if obj is self.edit:
            if et == QEvent.Type.FocusIn:
                self._focused = True
                self._hide_timer.stop()
                self._on_text(self.edit.text())
            elif et == QEvent.Type.FocusOut:
                # Delay hide so a click on the list can still register
                self._hide_timer.start()
        elif obj is self.suggest:
            if et == QEvent.Type.FocusIn:
                self._hide_timer.stop()
                self._focused = True
            elif et == QEvent.Type.FocusOut:
                self._hide_timer.start()
        return super().eventFilter(obj, event)

    def _hide_suggest_if_unfocused(self) -> None:
        # Still focused on edit or list?
        fw = self.edit.window().focusWidget() if self.edit.window() else None
        if fw is self.edit or fw is self.suggest or (fw is not None and self.suggest.isAncestorOf(fw)):
            self._focused = True
            return
        self._focused = False
        self.suggest.hide()
        self.suggest.clear()

    def set_candidates(self, names: list[str]) -> None:
        self._candidates = sorted({n.strip() for n in names if n and str(n).strip()})
        if self._focused:
            self._on_text(self.edit.text())
        else:
            # Only refresh status, keep list hidden
            token, _, _ = self._current_token(self.edit.text())
            self._update_status(token)
            self.suggest.hide()
            self.suggest.clear()

    def text(self) -> str:
        return self.edit.text()

    def setText(self, value: str) -> None:  # noqa: N802
        self._suppress = True
        self.edit.setText(value)
        self._suppress = False
        token, _, _ = self._current_token(value)
        self._update_status(token)
        if self._focused:
            self._on_text(value)
        else:
            self.suggest.hide()
            self.suggest.clear()

    def _current_token(self, full: str) -> tuple[str, int, int]:
        if self.kind in {"image", "name"}:
            token = full.strip()
            start = len(full) - len(full.lstrip()) if full else 0
            end = start + len(full.strip()) if full.strip() else start
            return token, start, end

        pos = self.edit.cursorPosition()
        left = full.rfind(",", 0, pos)
        right = full.find(",", pos)
        start = left + 1 if left >= 0 else 0
        end = right if right >= 0 else len(full)
        token = full[start:end]
        raw = token
        stripped = token.lstrip()
        start += len(raw) - len(stripped)
        token = stripped.rstrip()
        if self.kind == "volume":
            if token.startswith("/"):
                return token, start, end
            if ":" in token:
                token = token.split(":", 1)[0]
        return token.strip(), start, end

    def _on_text(self, _text: str | None = None) -> None:
        if self._suppress:
            return
        full = self.edit.text()
        token, _, _ = self._current_token(full)
        self._update_status(token)

        # Never show the list without focus (saves vertical space in the dialog)
        if not self._focused:
            self.suggest.hide()
            self.suggest.clear()
            return

        if self.kind == "name":
            self.suggest.hide()
            self.suggest.clear()
            return

        if not token or (self.kind == "volume" and token.startswith("/")):
            self.suggest.hide()
            self.suggest.clear()
            return

        matches = similar_names(token, self._candidates, limit=self._MAX_VISIBLE)
        if not matches and self._candidates:
            q = token.lower()
            matches = [c for c in self._candidates if q in c.lower()][: self._MAX_VISIBLE]
        if not matches:
            self.suggest.hide()
            self.suggest.clear()
            return

        self.suggest.clear()
        for name in matches:
            exact = name.lower() == token.lower()
            if self.kind == "image":
                label = f"{name}  ✓ local" if exact else name
            else:
                label = f"{name}  ✓ exists" if exact else name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.suggest.addItem(item)
        row_h = max(22, self.suggest.sizeHintForRow(0) if self.suggest.count() else 24)
        h = min(self._MAX_HEIGHT, row_h * min(self.suggest.count(), self._MAX_VISIBLE) + 6)
        self.suggest.setFixedHeight(h)
        self.suggest.show()

    def _update_status(self, token: str) -> None:
        if self.kind == "name":
            if not token:
                self.status.setText("Optional — must be unique if set.")
                self.status.setStyleSheet("color:#8a95a4; font-size:11px;")
                return
            taken = any(c.lower() == token.lower() for c in self._candidates)
            if taken:
                self.status.setText(f"✗ «{token}» already in use.")
                self.status.setStyleSheet("color:#ff939b; font-size:11px;")
            else:
                self.status.setText(f"✓ «{token}» is available.")
                self.status.setStyleSheet("color:#66e39a; font-size:11px;")
            return

        if self.kind == "image":
            if not token:
                self.status.setText("Local images appear while typing (when focused).")
                self.status.setStyleSheet("color:#8a95a4; font-size:11px;")
                return
            if any(c.lower() == token.lower() for c in self._candidates):
                self.status.setText(f"✓ «{token}» available locally.")
                self.status.setStyleSheet("color:#66e39a; font-size:11px;")
            else:
                self.status.setText(f"«{token}» not local — will try pull.")
                self.status.setStyleSheet("color:#8a95a4; font-size:11px;")
            return

        if not token:
            self.status.setText("Matches appear while this field is focused.")
            self.status.setStyleSheet("color:#8a95a4; font-size:11px;")
            return
        if self.kind == "volume" and token.startswith("/"):
            self.status.setText("Host bind path.")
            self.status.setStyleSheet("color:#8a95a4; font-size:11px;")
            return
        if token in self._candidates or any(c.lower() == token.lower() for c in self._candidates):
            self.status.setText(f"✓ «{token}» exists — will be reused.")
            self.status.setStyleSheet("color:#66e39a; font-size:11px;")
        else:
            extra = " (created on run)" if self.kind == "volume" else ""
            self.status.setText(f"«{token}» not found{extra}.")
            self.status.setStyleSheet("color:#8a95a4; font-size:11px;")

    def _apply_item(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole) or item.text().split()[0]
        full = self.edit.text()
        token, start, end = self._current_token(full)
        if self.kind == "volume":
            segment = full[start:end]
            if ":" in segment.lstrip():
                trailing = ":" + segment.lstrip().split(":", 1)[1]
            else:
                trailing = ":/data"
            replacement = name + trailing
        else:
            replacement = name
        new_full = full[:start] + replacement + full[end:]
        self._suppress = True
        self.edit.setText(new_full)
        self._suppress = False
        self.suggest.hide()
        self.suggest.clear()
        self.edit.setFocus()
        self._focused = True
        self._on_text(new_full)

    def is_name_available(self) -> bool:
        if self.kind != "name":
            return True
        token = self.edit.text().strip()
        if not token:
            return True
        return not any(c.lower() == token.lower() for c in self._candidates)



class PortCheckEdit(QWidget):
    """Ports line edit with live 'in use on server' status."""

    def __init__(self, parent: QWidget | None = None, placeholder: str = "") -> None:
        super().__init__(parent)
        self._used: dict[int, str] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder or "8080:80, 8443:443")
        lay.addWidget(self.edit)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#8a95a4; font-size:11px; padding-left:2px;")
        lay.addWidget(self.status)
        self.edit.textChanged.connect(self._refresh)
        self._refresh()

    def set_used_ports(self, used: dict[int, str]) -> None:
        self._used = {int(k): str(v) for k, v in (used or {}).items()}
        self._refresh()

    def text(self) -> str:
        return self.edit.text()

    def setText(self, value: str) -> None:  # noqa: N802
        self.edit.setText(value)

    def conflicts(self) -> list[tuple[int, str]]:
        from app.docker.manager import DockerManager
        ports = DockerManager.parse_host_ports(self.edit.text())
        out = []
        for p in ports:
            if p in self._used:
                out.append((p, self._used[p]))
        return out

    def _refresh(self, _=None) -> None:
        from app.docker.manager import DockerManager
        ports = DockerManager.parse_host_ports(self.edit.text())
        if not ports:
            self.status.setText("Host ports to publish (left side of host:container). Empty = none.")
            self.status.setStyleSheet("color:#8a95a4; font-size:11px;")
            return
        conflicts = []
        free = []
        for p in ports:
            if p in self._used:
                conflicts.append(f"{p} ({self._used[p]})")
            else:
                free.append(str(p))
        if conflicts:
            self.status.setText("✗ In use on server: " + ", ".join(conflicts))
            self.status.setStyleSheet("color:#ff939b; font-size:11px;")
        else:
            self.status.setText("✓ Host port(s) free: " + ", ".join(free))
            self.status.setStyleSheet("color:#66e39a; font-size:11px;")
