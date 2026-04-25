"""CLI for the LiveSync bridge."""

from __future__ import annotations

import asyncio
import logging
import sys

import click
import structlog

from .bridge import LiveSyncBridge
from .config import get_settings


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_log_level,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO),
        ),
        cache_logger_on_first_use=True,
    )


@click.group()
def cli() -> None:
    """LiveSync bridge: keep CouchDB and the vault filesystem in sync."""


@cli.command()
def reconcile() -> None:
    """Pull all CouchDB docs into the filesystem once and exit."""
    settings = get_settings()
    _setup_logging(settings.log_level)
    bridge = LiveSyncBridge(settings)

    async def _run() -> None:
        try:
            await bridge.reconcile_initial()
        finally:
            await bridge.couch.aclose()

    asyncio.run(_run())


@cli.command()
def run() -> None:
    """Run the bidirectional bridge as a daemon."""
    settings = get_settings()
    _setup_logging(settings.log_level)
    bridge = LiveSyncBridge(settings)
    asyncio.run(bridge.run())


if __name__ == "__main__":
    cli()
