"""Helpers shared by several test modules."""


def selfsigned(tmp_path, cn="Test Signer"):
    """A self-signed certificate: (PKCS#12 file with passphrase "pw", PEM certificate file)."""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, cn)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(7).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30)).sign(key, hashes.SHA256()))
    p12 = tmp_path / f"{cn}.p12"
    p12.write_bytes(pkcs12.serialize_key_and_certificates(b"id", key, cert, None, serialization.BestAvailableEncryption(b"pw")))
    pem = tmp_path / f"{cn}.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(p12), str(pem)


def choose(monkeypatch, *labels):
    """Make custom QMessageBoxes answer with the buttons called `labels`, in turn; the last one repeats.
    Returns a dict that receives the text and the default button's text of the last box shown, and how many were shown."""
    from PyQt5 import QtWidgets
    shown = {"count": 0}

    def exec_(self):
        label = labels[min(shown["count"], len(labels) - 1)]
        shown.update(text=self.text(), default=self.defaultButton().text() if self.defaultButton() else None,
                     count=shown["count"] + 1)
        for b in self.buttons():
            if b.text() == label:
                self._clicked = b
        return 0
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec_", exec_)
    monkeypatch.setattr(QtWidgets.QMessageBox, "clickedButton", lambda self: getattr(self, "_clicked", None))
    return shown
