"""The test harness itself: an unexpected modal dialog must fail the test, never block it (issue #23)."""
import pytest

pytest.importorskip("PyQt5")
from PyQt5 import QtCore, QtPrintSupport, QtWidgets


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _close_modal_soon():
    """Without the fixture, exec_() would wait forever; close the dialog so a broken fixture fails instead of hanging."""
    def close():
        w = QtWidgets.QApplication.activeModalWidget() or QtWidgets.QApplication.activePopupWidget()
        if w:
            w.close()
    QtCore.QTimer.singleShot(500, close)


@pytest.mark.parametrize("make", [
    lambda: QtWidgets.QMessageBox(QtWidgets.QMessageBox.Question, "Recover?", "Restore the unsaved work?"),
    lambda: QtWidgets.QDialog(),
    lambda: QtPrintSupport.QPrintDialog(QtPrintSupport.QPrinter()),
    lambda: QtWidgets.QMenu(),
], ids=["QMessageBox", "QDialog", "QPrintDialog", "QMenu"])
def test_an_unpatched_exec_fails_with_unexpected_dialog(app, make):
    dlg = make()
    _close_modal_soon()
    with pytest.raises(AssertionError, match="unexpected dialog"):
        dlg.exec_()
