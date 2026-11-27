"""Issue #7: an in-place save must not silently overwrite a file another program changed since it was opened."""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import fitz
import pytest

from openrevu.core import Document, FileChangedError


def _pdf(path, text="original"):
    d = fitz.open()
    d.new_page().insert_text((72, 100), text)
    d.save(str(path))
    return str(path)


def colleague_edits(path):
    """Another program rewrites the file. The size changes, so the test does not depend on mtime granularity."""
    with open(path, "ab") as f:
        f.write(b"\n% a colleague's edit\n")


def colleague_touches(path):
    """Another program rewrites the file with the same size: only the modification time moves (set explicitly)."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


@pytest.fixture
def pdf(tmp_path):
    return _pdf(tmp_path / "a.pdf")


def test_in_place_save_refuses_when_the_file_changed_on_disk_and_force_overwrites(pdf):
    d = Document(pdf)
    d.add_rect(0, fitz.Rect(100, 100, 200, 200))
    colleague_edits(pdf)
    theirs = Path(pdf).read_bytes()

    with pytest.raises(FileChangedError):
        d.save()
    assert Path(pdf).read_bytes() == theirs           # nothing of theirs was overwritten
    assert d.modified
    assert not Path(pdf + ".tmp").exists()

    d.save(force=True)
    assert len(Document(pdf).markups()) == 1
    assert not d.modified


def test_a_change_of_modification_time_alone_counts_as_a_change(pdf):
    d = Document(pdf)
    colleague_touches(pdf)
    with pytest.raises(FileChangedError):
        d.save()


def test_file_changed_error_is_an_os_error_so_existing_error_handling_reports_it(pdf):
    d = Document(pdf)
    colleague_edits(pdf)
    with pytest.raises(OSError) as e:
        d.save()
    assert "changed by another program" in str(e.value)


def test_saving_in_place_twice_does_not_trip_over_its_own_save(pdf):
    d = Document(pdf)
    d.add_rect(0, fitz.Rect(100, 100, 200, 200))
    d.save()
    d.add_rect(0, fitz.Rect(300, 300, 400, 400))
    d.save()
    assert len(Document(pdf).markups()) == 2


def test_after_save_as_the_new_file_is_the_one_watched(pdf, tmp_path):
    d = Document(pdf)
    copy = str(tmp_path / "copy.pdf")
    d.save(copy)
    colleague_edits(pdf)          # the old file is no longer this document's file
    d.save()
    colleague_edits(copy)
    with pytest.raises(FileChangedError):
        d.save()


def test_a_new_document_saved_to_a_path_is_watched_from_then_on(tmp_path):
    d = Document()
    d.doc.new_page()
    p = str(tmp_path / "new.pdf")
    d.save(p)
    colleague_edits(p)
    with pytest.raises(FileChangedError):
        d.save()


def test_saving_over_a_file_deleted_since_open_recreates_it(pdf):
    # Nothing of anyone else's is lost when the file is gone, so the save goes ahead.
    d = Document(pdf)
    d.add_rect(0, fitz.Rect(100, 100, 200, 200))
    os.remove(pdf)
    d.save()
    assert len(Document(pdf).markups()) == 1


def test_naming_the_documents_own_path_is_still_an_in_place_save(pdf):
    d = Document(pdf)
    colleague_edits(pdf)
    with pytest.raises(FileChangedError):
        d.save(pdf)


# --- GUI ---

pytest.importorskip("PyQt5")
from PyQt5 import QtWidgets  # noqa: E402

from openrevu.gui import Main  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(app, pdf):
    w = Main(pdf); w.show()
    app.processEvents()
    w.doc.add_rect(0, fitz.Rect(100, 100, 200, 200))
    return w


def answer_with(monkeypatch, label):
    """Answer every custom QMessageBox with the button called `label`; return what the boxes showed."""
    seen = {"asked": 0}

    def exec_(self):
        seen["asked"] += 1
        seen.update(text=self.text(), default=self.defaultButton().text())
        self._clicked = next(b for b in self.buttons() if b.text() == label)
        return 0
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec_", exec_)
    monkeypatch.setattr(QtWidgets.QMessageBox, "clickedButton", lambda self: getattr(self, "_clicked", None))
    return seen


def test_gui_cancel_keeps_the_other_programs_file_and_the_unsaved_changes(win, pdf, monkeypatch):
    colleague_edits(pdf)
    theirs = Path(pdf).read_bytes()
    seen = answer_with(monkeypatch, "Cancel")
    assert win._save(win.doc) is False
    assert seen["asked"] == 1
    assert seen["text"].startswith("The file was changed by another program since you opened it.")
    assert seen["default"] == "Save As…"
    assert Path(pdf).read_bytes() == theirs and win.doc.modified


def test_gui_overwrite_replaces_the_file(win, pdf, monkeypatch):
    colleague_edits(pdf)
    seen = answer_with(monkeypatch, "Overwrite")
    win.save()
    assert seen["asked"] == 1
    assert len(Document(pdf).markups()) == 1 and not win.doc.modified
    win.doc.add_rect(0, fitz.Rect(300, 300, 400, 400))
    win.save()                       # its own save is not a change by another program: no second question
    assert seen["asked"] == 1 and len(Document(pdf).markups()) == 2


def test_gui_save_as_writes_a_copy_and_leaves_the_other_programs_file(win, pdf, monkeypatch, tmp_path):
    colleague_edits(pdf)
    theirs = Path(pdf).read_bytes()
    answer_with(monkeypatch, "Save As…")
    copy = str(tmp_path / "mine.pdf")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (copy, "")))
    win.save()
    assert Path(pdf).read_bytes() == theirs
    assert win.doc.path == copy and len(Document(copy).markups()) == 1
    assert win.tabs.tabText(win.tabs.currentIndex()) == "mine.pdf"


def test_gui_save_as_then_cancel_in_the_file_dialog_saves_nothing(win, pdf, monkeypatch):
    colleague_edits(pdf)
    theirs = Path(pdf).read_bytes()
    answer_with(monkeypatch, "Save As…")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    assert win._save(win.doc) is False
    assert Path(pdf).read_bytes() == theirs and win.doc.modified


def test_closing_a_tab_and_cancelling_the_overwrite_question_keeps_the_tab(win, pdf, monkeypatch):
    colleague_edits(pdf)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Save))
    answer_with(monkeypatch, "Cancel")
    win.close_tab(0)
    assert win.tabs.count() == 1 and win.doc.modified


def test_save_as_menu_to_the_same_file_overwrites_without_asking_again(win, pdf, monkeypatch):
    # The Save As dialog already asked the user whether to replace that file.
    colleague_edits(pdf)
    seen = answer_with(monkeypatch, "Cancel")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (pdf, "")))
    win.save_as()
    assert seen["asked"] == 0
    assert len(Document(pdf).markups()) == 1
