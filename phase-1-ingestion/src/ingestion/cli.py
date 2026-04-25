"""CLI for the ingestion pipeline."""

from __future__ import annotations

from pathlib import Path

import click

from .config import get_settings
from .embedder import VoyageEmbedder
from .indexer import Indexer
from .logging_setup import setup_logging
from .store import VaultStore
from .watcher import watch as watch_loop


def _build_indexer() -> Indexer:
    settings = get_settings()
    setup_logging(settings.log_level)
    store = VaultStore(
        url=settings.qdrant_url,
        collection=settings.qdrant_collection,
        api_key=settings.qdrant_api_key,
        dense_dim=settings.embed_dim,
    )
    embedder = VoyageEmbedder(
        api_key=settings.voyage_api_key,
        model=settings.embed_model,
        dim=settings.embed_dim,
    )
    return Indexer(settings, store, embedder)


@click.group()
def cli() -> None:
    """Second Brain ingestion pipeline."""


@cli.command()
def init() -> None:
    """Ensure the Qdrant collection exists with the correct schema."""
    indexer = _build_indexer()
    indexer.store.ensure_collection()
    click.echo(f"Collection '{indexer.store.collection}' ready.")


@cli.command("reindex-all")
def reindex_all() -> None:
    """Walk the vault and (re)index every markdown file."""
    indexer = _build_indexer()
    indexer.store.ensure_collection()
    stats = indexer.reindex_all()
    click.echo(f"Indexed {stats['ok']}/{stats['total']} files.")


@cli.command()
@click.option("--path", "path", required=True, type=click.Path(path_type=Path))
def reindex(path: Path) -> None:
    """Reindex a single file."""
    indexer = _build_indexer()
    indexer.store.ensure_collection()
    result = indexer.index_file(path)
    click.echo(result)


@cli.command()
def watch() -> None:
    """Run as a daemon, watching the vault for changes."""
    indexer = _build_indexer()
    indexer.store.ensure_collection()
    watch_loop(indexer)


if __name__ == "__main__":
    cli()
