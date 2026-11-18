"""Workarounds that let PyMuPDF (checked with 2.1) work on a document with MuPDF's undo journal enabled.

1. PyMuPDF checks for an open journal operation by asking MuPDF for the name of undo step 0, and MuPDF refuses to answer
   while an operation is open. So get_new_xref, update_object, insert_font and show_pdf_page (and with them
   insert_text, set_toc and set_metadata) fail inside every operation. MuPDF itself already refuses any change outside an
   operation, so the check is removed.
2. The compiled versions of new_page and insert_pdf make the same check. `operation()` switches PyMuPDF to its Python
   versions while an operation is open.
3. Text extraction and drawing extraction set a rotated page's rotation to 0 and back. Outside an operation MuPDF refuses
   that change. The net change is nothing, so the journal is detached while they run.
4. Writing a journalled document (save, tobytes, even MuPDF's snapshot write) changes MuPDF's internal state so that
   later undo steps no longer restore every object, and PyMuPDF's save can change the catalog. So `Document.save` (which
   tobytes and write also use) writes a copy instead, made by `copy()`. Copying pages with their annotations out of a
   journalled document changes it too: copy pages from `copy()` as well.
5. MuPDF does not journal the trailer. If an operation creates the Info dictionary (the document metadata), undo removes
   it but the trailer still points at it, and the next metadata change fails. So the journal starts with an Info
   dictionary in place.
"""
import contextlib
import functools

import pymupdf as fitz

mupdf = fitz.mupdf

fitz.JM_have_operation = lambda pdf: 1                                                     # 1


def _pdf(doc: fitz.Document):
    return mupdf.pdf_document_from_fz_document(doc.this)


def _journal(doc: fitz.Document):
    pdf = _pdf(doc)
    return pdf.m_internal.journal if pdf.m_internal else None


@contextlib.contextmanager
def operation(doc: fitz.Document, name: str):
    """A journal operation (the journal starts with the first one). If the body raises, every change it made is undone
    and dropped from the journal."""
    if not _journal(doc):
        if doc.xref_get_key(-1, "Info")[0] != "xref":                                     # 5
            doc.set_metadata(doc.metadata)
        doc.journal_enable()
    use_extra, fitz.g_use_extra = fitz.g_use_extra, False                                # 2
    try:
        doc.journal_start_op(name)
        try:
            yield
        except BaseException:
            mupdf.pdf_abandon_operation(_pdf(doc))
            raise
        doc.journal_stop_op()
    finally:
        fitz.g_use_extra = use_extra


def _without_journal(method):                                                              # 3
    @functools.wraps(method)
    def wrapper(page, *a, **kw):
        journal = _journal(page.parent) if page.rotation else None
        if not journal:
            return method(page, *a, **kw)
        pdf = _pdf(page.parent)
        pdf.m_internal.journal = None
        try:
            return method(page, *a, **kw)
        finally:
            pdf.m_internal.journal = journal
    return wrapper


for _name in ("get_textpage", "get_texttrace", "get_bboxlog", "get_cdrawings"):
    setattr(fitz.Page, _name, _without_journal(getattr(fitz.Page, _name)))


def copy(doc: fitz.Document) -> fitz.Document:                                             # 4
    """A copy of `doc` (decrypted, without a journal): the objects its trailer refers to, and the file identifier."""
    new = fitz.open()
    src, dst = _pdf(doc), _pdf(new)
    graft = mupdf.pdf_new_graft_map(dst)
    for key in ("Root", "Info", "ID"):
        obj = mupdf.pdf_dict_gets(mupdf.pdf_trailer(src), key)
        if mupdf.pdf_is_dict(obj) or mupdf.pdf_is_array(obj):
            mupdf.pdf_dict_puts(mupdf.pdf_trailer(dst), key, mupdf.pdf_graft_mapped_object(graft, obj))
    new.init_doc()
    return new


_save = fitz.Document.save


@functools.wraps(_save)
def _save_a_copy(doc, *a, **kw):                                                           # 4
    if not _journal(doc):
        return _save(doc, *a, **kw)
    with copy(doc) as c:
        return _save(c, *a, **kw)


fitz.Document.save = _save_a_copy
