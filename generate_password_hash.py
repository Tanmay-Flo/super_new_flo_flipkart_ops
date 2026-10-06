"""
generate_password_hash.py
-------------------------
Creates the [auth] block for secrets.toml from a username + password.

Run from the project folder:
    python generate_password_hash.py

Copy the printed block into .streamlit/secrets.toml (local) and into the
app's Secrets box on Streamlit Community Cloud. Run it again any time you
want to change the password.
"""

import getpass
import hashlib
import secrets

PBKDF2_ITERATIONS = 310_000  # must match utils/auth.py


def main() -> None:
    username = input("Username: ").strip()
    password = getpass.getpass("Password (hidden): ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("\nPasswords don't match. Nothing generated.")
        return
    if len(password) < 10:
        print("\nPlease use at least 10 characters.")
        return

    salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()

    print("\nPaste this into your secrets:\n")
    print("[auth]")
    print(f'username = "{username}"')
    print(f'password_salt = "{salt}"')
    print(f'password_hash = "{pw_hash}"')


if __name__ == "__main__":
    main()
