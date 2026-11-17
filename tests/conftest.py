import os

# Tests must never read or write the real user preferences (recent files, window layout).
os.environ["OPENREVU_NO_SETTINGS"] = "1"
os.environ["OPENREVU_AUTOSAVE_SECONDS"] = "100000"   # tests call autosave_now() themselves
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


import pytest


@pytest.fixture(autouse=True)
def no_unexpected_dialogs(monkeypatch):
    """A modal dialog would block a headless test forever. Make unexpected ones fail at once.
    Tests that expect a dialog patch it themselves (their patch replaces this one)."""
    try:
        from PyQt5 import QtWidgets as W
    except ImportError:
        return

    def boom(name):
        def f(*a, **k):
            raise AssertionError(f"unexpected dialog: {name} {a[1:3] if len(a) > 2 else ''}")
        return staticmethod(f)

    for cls, names in ((W.QMessageBox, ("warning", "critical", "information", "question")),
                       (W.QInputDialog, ("getText", "getInt", "getDouble", "getItem", "getMultiLineText")),
                       (W.QFileDialog, ("getOpenFileName", "getOpenFileNames", "getSaveFileName", "getExistingDirectory"))):
        for n in names:
            monkeypatch.setattr(cls, n, boom(f"{cls.__name__}.{n}"))


@pytest.fixture(autouse=True)
def fail_on_unhandled_exceptions():
    """An exception in a Qt event handler would abort the whole test run. Record it and fail only that test."""
    import sys
    errors = []
    old = sys.excepthook
    sys.excepthook = lambda t, v, tb: errors.append("".join(__import__("traceback").format_exception(t, v, tb)))
    yield
    sys.excepthook = old
    assert not errors, "unhandled exception in an event handler:\n" + errors[0]


@pytest.fixture(autouse=True)
def isolated_recovery_dir(tmp_path, monkeypatch):
    """Snapshots go to a fresh folder for each test, never to the real recovery folder."""
    d = tmp_path / "recovery"
    monkeypatch.setenv("OPENREVU_RECOVERY_DIR", str(d))
    return d


@pytest.fixture
def signed_pdf(tmp_path):
    """A one-page PDF with a valid digital signature (self-signed certificate, made with pyHanko)."""
    pytest.importorskip("pyhanko")
    import datetime

    import fitz
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12

    from openrevu.sign import sign_pdf

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "Sealing Engineer")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30)).sign(key, hashes.SHA256()))
    p12 = tmp_path / "seal.p12"
    p12.write_bytes(pkcs12.serialize_key_and_certificates(b"id", key, cert, None, serialization.BestAvailableEncryption(b"pw")))
    plain = tmp_path / "unsigned.pdf"
    d = fitz.open()
    d.new_page().insert_text((72, 100), "Sealed permit set")
    d.save(plain)
    out = tmp_path / "signed.pdf"
    sign_pdf(str(plain), str(out), str(p12), "pw")
    return str(out)
