"""
Re-chiffre les mots de passe des équipements après un changement de SECRET_KEY.

Usage (dans le conteneur backend, une seule fois, avant de redémarrer les services) :
    OLD_SECRET_KEY='<ancienne clé>' SECRET_KEY='<nouvelle clé>' python -m scripts.rotate_secret_key

Les mots de passe qui ne se déchiffrent pas avec l'ancienne clé sont listés et laissés tels quels :
ils devront être ressaisis depuis l'interface.
"""
import base64
import os
import sys
from hashlib import sha256

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from app.core.database import engine


def _fernet(key: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(sha256(key.encode()).digest()))


def main() -> int:
    old_key = os.getenv("OLD_SECRET_KEY")
    new_key = os.getenv("SECRET_KEY")
    if not old_key or not new_key or old_key == new_key:
        print("OLD_SECRET_KEY et SECRET_KEY doivent être définies et différentes.")
        return 1

    old, new = _fernet(old_key), _fernet(new_key)
    rotated, failed = 0, []

    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, display_name, encrypted_password FROM infrastructure_devices")).all()
        for device_id, name, token in rows:
            try:
                clear = old.decrypt(token.encode())
            except (InvalidToken, AttributeError):
                failed.append(name)
                continue
            conn.execute(
                text("UPDATE infrastructure_devices SET encrypted_password = :p WHERE id = :id"),
                {"p": new.encrypt(clear).decode(), "id": device_id},
            )
            rotated += 1

    print(f"{rotated} mot(s) de passe re-chiffré(s).")
    if failed:
        print("À ressaisir depuis l'interface : " + ", ".join(failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
