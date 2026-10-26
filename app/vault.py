"""Authenticated encryption at rest; no plaintext username/password in config files."""
import base64
import json
import os
import secrets
import tempfile

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


class CredentialVault:
    def __init__(self, settings):
        self.directory = settings.state
        self.path = settings.state / "soulseek.enc"
        salt_path = settings.state / "vault.salt"
        try:
            fd = os.open(salt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as file:
                file.write(secrets.token_bytes(16))
        except FileExistsError:
            pass
        salt = salt_path.read_bytes()
        if len(salt) != 16:
            raise RuntimeError("Invalid vault.salt; restore it from backup or reset the Soulseek vault.")
        key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(settings.password.encode())
        self.fernet = Fernet(base64.urlsafe_b64encode(key))

    @property
    def saved(self):
        return self.path.exists()

    def load(self):
        if not self.saved:
            return None
        try:
            value = json.loads(self.fernet.decrypt(self.path.read_bytes()))
            if not isinstance(value.get("username"), str) or not isinstance(value.get("password"), str):
                raise ValueError
            return value
        except (InvalidToken, ValueError, KeyError):
            raise ValueError("Could not decrypt the saved Soulseek account. APP_PASSWORD may have changed. Remove the saved account and sign in again.") from None

    def save(self, username, password):
        token = self.fernet.encrypt(json.dumps({"username": username, "password": password}).encode())
        fd, name = tempfile.mkstemp(prefix=".vault-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(token)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def forget(self):
        self.path.unlink(missing_ok=True)
