"""PDF permissions and passwords survive a save (issue #5)."""
import fitz

from openrevu.core import Document

PRINT_ONLY = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_ACCESSIBILITY   # no annotate, no modify


def make_pdf(path, user_pw="", owner_pw="issuer-secret", permissions=PRINT_ONLY):
    d = fitz.open()
    d.new_page().insert_text((72, 100), "Issued for construction")
    d.save(str(path), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=user_pw, owner_pw=owner_pw, permissions=permissions)
    return str(path)


def user_permissions(path, user_pw=""):
    """The permission bits of `path` as a reader with the user password sees them."""
    f = fitz.open(path)
    if f.needs_pass:
        assert f.authenticate(user_pw) == 2
    return f.permissions


def owner_rights(path, password) -> bool:
    """True if `password` opens `path` with full (owner) rights."""
    return bool(fitz.open(path).authenticate(password) & 4)


def test_owner_only_restricted_pdf_stays_restricted_after_save(tmp_path):
    src = make_pdf(tmp_path / "issued.pdf")
    expected = user_permissions(src)
    d = Document(src)
    d.add_rect(0, fitz.Rect(10, 10, 50, 50))
    d.save()
    assert fitz.open(src).metadata["encryption"]              # still encrypted
    assert user_permissions(src) == expected                  # same permission bits
    assert not expected & fitz.PDF_PERM_ANNOTATE


def test_password_protected_pdf_does_not_get_a_guessable_owner_password(tmp_path):
    src = make_pdf(tmp_path / "both.pdf", user_pw="user-pw")
    expected = user_permissions(src, "user-pw")
    d = Document(src, "user-pw")
    d.add_rect(0, fitz.Rect(10, 10, 50, 50))
    out = str(tmp_path / "copy.pdf")
    d.save(out)
    assert user_permissions(out, "user-pw") == expected
    assert not owner_rights(out, "user-pw-owner")             # the old, derived owner password
    assert not owner_rights(out, "user-pw")


def test_the_owner_password_is_kept_when_the_owner_opens_the_file(tmp_path):
    src = make_pdf(tmp_path / "issued.pdf")
    d = Document(src, "issuer-secret")
    d.add_rect(0, fitz.Rect(10, 10, 50, 50))
    d.save()
    assert owner_rights(src, "issuer-secret")
    assert user_permissions(src) == user_permissions(make_pdf(tmp_path / "ref.pdf"))


def test_restricted_means_no_markups_or_changes_allowed_and_not_the_owner(tmp_path):
    assert Document(make_pdf(tmp_path / "a.pdf")).restricted
    assert Document(make_pdf(tmp_path / "b.pdf", user_pw="u"), "u").restricted
    assert Document(make_pdf(tmp_path / "c.pdf", permissions=PRINT_ONLY | fitz.PDF_PERM_ANNOTATE)).restricted
    assert not Document(make_pdf(tmp_path / "d.pdf"), "issuer-secret").restricted
    assert not Document(make_pdf(tmp_path / "e.pdf", permissions=-4)).restricted
    assert not Document(make_pdf(tmp_path / "f.pdf", owner_pw="", permissions=-1)).restricted
    plain = fitz.open(); plain.new_page(); plain.save(str(tmp_path / "plain.pdf"))
    assert not Document(str(tmp_path / "plain.pdf")).restricted     # not encrypted at all


def test_an_encrypted_copy_gets_a_random_owner_password(tmp_path):
    plain = fitz.open(); plain.new_page(); plain.save(str(tmp_path / "plain.pdf"))
    a, b = str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")
    Document(str(tmp_path / "plain.pdf")).save_encrypted(a, "pw")
    Document(str(tmp_path / "plain.pdf")).save_encrypted(b, "pw", allow_modify=True)
    assert not owner_rights(a, "pw-owner") and not owner_rights(a, "pw")
    assert user_permissions(b, "pw") & fitz.PDF_PERM_MODIFY


def test_an_encrypted_copy_of_a_restricted_pdf_grants_nothing_more(tmp_path):
    src = make_pdf(tmp_path / "issued.pdf", permissions=fitz.PDF_PERM_ACCESSIBILITY)    # not even printing
    out = str(tmp_path / "copy.pdf")
    Document(src).save_encrypted(out, "pw", allow_print=True, allow_modify=True)
    p = user_permissions(out, "pw")
    assert not p & (fitz.PDF_PERM_PRINT | fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ANNOTATE)


def test_a_compare_overlay_of_a_protected_pdf_has_no_guessable_owner_password(tmp_path):
    from openrevu.compare import compare_documents
    src = make_pdf(tmp_path / "both.pdf", user_pw="user-pw")
    out = str(tmp_path / "overlay.pdf")
    compare_documents(src, src, out, old_password="user-pw", new_password="user-pw")
    assert fitz.open(out).needs_pass
    assert not owner_rights(out, "user-pw-owner")
