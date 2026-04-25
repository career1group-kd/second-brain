"""One-shot CLI: complete the Google OAuth2 flow and persist the token."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from cryptography.fernet import Fernet

from .config import get_settings

SCOPES = ["https://www.googleapis.com/auth/tasks"]


@click.group()
def cli() -> None:
    """Google Tasks auth helpers."""


@cli.command()
def auth() -> None:
    """Run the InstalledAppFlow and persist an encrypted token."""
    settings = get_settings()
    secrets_path = Path(settings.google_client_secrets_path)
    if not secrets_path.exists():
        click.echo(
            f"Missing client secrets at {secrets_path}. "
            "Download client_secret.json from the Google Cloud Console.",
            err=True,
        )
        sys.exit(1)

    if not settings.gtasks_token_key:
        click.echo(
            "GTASKS_TOKEN_KEY is unset. Generate one with:\n"
            "  python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'",
            err=True,
        )
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path = Path(settings.gtasks_token_path)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    fernet = Fernet(settings.gtasks_token_key.encode("utf-8"))
    encrypted = fernet.encrypt(json.dumps(json.loads(creds.to_json())).encode("utf-8"))
    token_path.write_bytes(encrypted)
    click.echo(f"Encrypted token written to {token_path}")


@cli.command()
def keygen() -> None:
    """Print a new Fernet key for GTASKS_TOKEN_KEY."""
    click.echo(Fernet.generate_key().decode("utf-8"))


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
