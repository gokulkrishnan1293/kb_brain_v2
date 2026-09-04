"""Thin CLI over the platform.

The API is the contract; this is a convenience wrapper that calls the same
objects so local operation and production behave identically.
"""

from __future__ import annotations

import asyncio
import json
import logging

import typer
from rich.console import Console
from rich.table import Table

from .agents.ingestion import IngestionRequest
from .errors import KBError
from .platform import KnowledgeBrain
from .scope import ScopeRef
from .settings import get_settings

app = typer.Typer(help="Knowledge Brain Platform", no_args_is_help=True)
sources_app = typer.Typer(help="Inspect configured MCP sources", no_args_is_help=True)
app.add_typer(sources_app, name="sources")
console = Console()


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


@sources_app.command("list")
def list_sources(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Show every configured MCP server and whether it is reachable."""
    _configure_logging(verbose)
    brain = KnowledgeBrain()
    report = asyncio.run(brain.connector.server_status())

    table = Table(title="MCP servers")
    table.add_column("server")
    table.add_column("sources")
    table.add_column("transport")
    table.add_column("status")
    table.add_column("tools", justify="right")
    table.add_column("detail", overflow="fold")

    for entry in report:
        detail = entry.get("error") or (
            f"missing env: {', '.join(entry['missing_env'])}" if entry["missing_env"] else ""
        )
        status_style = {"connected": "green", "error": "red"}.get(entry["status"], "yellow")
        table.add_row(
            entry["name"],
            ", ".join(entry["sources"]),
            entry["transport"],
            f"[{status_style}]{entry['status']}[/{status_style}]",
            str(entry.get("tool_count", "-")),
            detail,
        )
    console.print(table)


@sources_app.command("tools")
def list_tools(
    source: str = typer.Argument(..., help="Logical source, e.g. jira"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List the read tools the brain will expose for a source."""
    _configure_logging(verbose)
    brain = KnowledgeBrain()
    try:
        tools = asyncio.run(brain.connector.tools_for(source))
    except KBError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"Tools for '{source}'")
    table.add_column("tool")
    table.add_column("description", overflow="fold")
    for tool in tools:
        table.add_row(tool.name, (tool.description or "").strip().splitlines()[0][:160])
    console.print(table)


@app.command()
def ingest(
    scope: str = typer.Option(..., "--scope", help="e.g. program:payments/application:abc"),
    source: list[str] = typer.Option(..., "--source", "-s", help="Repeatable."),
    objective: str = typer.Option(..., "--objective", "-o"),
    principal: str = typer.Option("platform-admin", "--principal", "-p"),
    hint: list[str] = typer.Option([], "--hint", help="key=value, repeatable."),
    max_records: int | None = typer.Option(None, "--max-records"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Retrieve but do not persist."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Trigger an ingestion run (same harness the API drives)."""
    _configure_logging(verbose)
    hints = dict(item.split("=", 1) for item in hint if "=" in item)
    brain = KnowledgeBrain()
    try:
        request = IngestionRequest(
            scope=ScopeRef.parse(scope),
            sources=list(source),
            objective=objective,
            hints=hints,
            max_records=max_records,
            dry_run=dry_run,
        )
        result = asyncio.run(brain.ingestion.run(request, principal_id=principal))
    except KBError as exc:
        console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print_json(json.dumps(result.model_dump(mode="json"), default=str))


@app.command()
def serve(
    host: str = typer.Option(None, "--host"),
    port: int = typer.Option(None, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Run the platform API."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "kb_brain.api.app:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=reload,
    )


if __name__ == "__main__":  # pragma: no cover
    app()
