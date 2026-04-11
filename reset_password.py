#!/usr/bin/env python
"""CLI script to reset a FitLocal account password by email.

Usage:
    python reset_password.py user@example.com
"""
import sys
import os
import getpass

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from app import app  # noqa: E402
from models import db, Account  # noqa: E402
from extensions import bcrypt  # noqa: E402


def reset_password(email):
    with app.app_context():
        account = Account.query.filter_by(email=email.lower()).first()
        if not account:
            print(f"No account found with email: {email}")
            sys.exit(1)

        print(f"Resetting password for: {account.email}")
        new_password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Confirm password: ")

        if new_password != confirm:
            print("Passwords do not match.")
            sys.exit(1)

        if len(new_password) < 8:
            print("Password must be at least 8 characters.")
            sys.exit(1)

        account.password_hash = bcrypt.generate_password_hash(new_password).decode("utf-8")
        db.session.commit()
        print("Password reset successfully.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python reset_password.py <email>")
        sys.exit(1)
    reset_password(sys.argv[1])
