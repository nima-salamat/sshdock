"""Async Qt dialogs that do not run a nested event loop inside qasync tasks.

Calling QDialog.exec() (or static QMessageBox/QFileDialog helpers) from a coroutine
re-enters Qt's event loop while an asyncio Task is still on the stack. qasync may
then try to drive another Task from that same stack and raise
"Cannot enter into task ... while another task ... is being executed".
Use QDialog.open() and await its finished signal instead.
"""
from __future__ import annotations

import asyncio

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMessageBox,
)


async def await_dialog(dialog: QDialog) -> int:
    """Open a modal dialog without blocking/re-entering the asyncio event loop."""
    loop = asyncio.get_running_loop()
    result_future: asyncio.Future[int] = loop.create_future()

    def on_finished(result: int) -> None:
        if not result_future.done():
            result_future.set_result(int(result))

    dialog.finished.connect(on_finished)
    try:
        dialog.open()
        return await result_future
    except asyncio.CancelledError:
        # Shutdown cancels in-flight UI operations. Dismiss their child dialogs
        # as part of cancellation, otherwise an orphan modal can keep the window
        # and application alive after MainWindow starts closing.
        if dialog.isVisible():
            dialog.reject()
        raise
    finally:
        try:
            dialog.finished.disconnect(on_finished)
        except (RuntimeError, TypeError):
            pass
        if not result_future.done():
            result_future.cancel()
        if result_future.cancelled() and dialog.isVisible():
            dialog.reject()


async def ask_yes_no(parent, title: str, text: str, *, default_no: bool = True) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(
        QMessageBox.StandardButton.No if default_no else QMessageBox.StandardButton.Yes
    )
    result = await await_dialog(box)
    return result == int(QMessageBox.StandardButton.Yes)


async def show_message(parent, title: str, text: str, *, icon=QMessageBox.Icon.Information) -> None:
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    await await_dialog(box)


async def choose_file(
    parent,
    title: str,
    *,
    save: bool = False,
    directory: str = "",
    name_filter: str = "",
) -> tuple[str, str]:
    """Return (selected path, selected filter); empty path means cancelled."""
    dialog = QFileDialog(parent)
    dialog.setWindowTitle(title)
    if directory:
        dialog.setDirectory(directory)
    if name_filter:
        dialog.setNameFilter(name_filter)
    if save:
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    else:
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)

    result = await await_dialog(dialog)
    if result != int(QDialog.DialogCode.Accepted):
        return "", dialog.selectedNameFilter()
    files = dialog.selectedFiles()
    return (files[0] if files else ""), dialog.selectedNameFilter()


async def input_text(
    parent,
    title: str,
    label: str,
    *,
    text: str = "",
) -> tuple[str, bool]:
    """Asynchronously ask for text and return (value, accepted)."""
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setInputMode(QInputDialog.InputMode.TextInput)
    dialog.setTextValue(text)
    result = await await_dialog(dialog)
    return dialog.textValue(), result == int(QDialog.DialogCode.Accepted)
