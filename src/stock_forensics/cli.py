"""Command line interface: ``forensics AAPL`` writes a forensic report for one US stock.

Progress goes to stderr, the report to a file (or to stdout with ``--out -``). Exit codes:
0 success, 1 the analysis failed (unknown ticker, SEC not reachable, file not writable),
2 usage problem (no ``SEC_USER_AGENT``).
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn

from stock_forensics import __version__
from stock_forensics.providers.sec_edgar import (
    USER_AGENT_ENV,
    MissingUserAgentError,
    SecEdgarClient,
    SecEdgarError,
    TickerNotFoundError,
)
from stock_forensics.report import ForensicReport, analyze, render
from stock_forensics.report.models import Light
from stock_forensics.report.render import LIGHT_LABELS

EXIT_FAILED = 1
EXIT_USAGE = 2

app = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


class OutputFormat(StrEnum):
    HTML = "html"
    MD = "md"
    JSON = "json"


def load_env_file(path: Path | None = None) -> None:
    """Read ``KEY=VALUE`` lines of a ``.env`` file into the environment.

    Minimal on purpose: comments, blank lines, an optional ``export`` prefix and quotes around
    the value are understood. Variables that are already set are never overwritten.
    """
    path = path or Path(".env")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def build_client(*, no_cache: bool) -> SecEdgarClient:
    """The SEC client for a CLI run; the User-Agent comes from ``SEC_USER_AGENT``."""
    return SecEdgarClient(use_cache=not no_cache)


def default_output_path(ticker: str, fmt: OutputFormat) -> Path:
    return Path(f"{ticker.upper().replace('.', '-')}-forensics.{fmt.value}")


def _version_callback(show: bool) -> None:
    if show:
        typer.echo(f"stock-forensics {__version__}")
        raise typer.Exit()


class _Reporter:
    """Progress on stderr: a live bar in a terminal, one line per step otherwise."""

    def __init__(self, console: Console) -> None:
        self._console = console
        self._progress: Progress | None = None
        self._task: TaskID | None = None

    def __enter__(self) -> _Reporter:
        if self._console.is_terminal:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[{task.completed}/{task.total}] {task.description}"),
                BarColumn(bar_width=24),
                console=self._console,
                transient=True,
            )
            self._progress.start()
            self._task = self._progress.add_task("Starting", total=7)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._progress is not None:
            self._progress.stop()

    def __call__(self, step: int, total: int, message: str) -> None:
        if self._progress is not None and self._task is not None:
            self._progress.update(self._task, description=message, total=total, completed=step - 1)
        else:
            self._console.print(f"[{step}/{total}] {message}")


@app.command(help="Forensic check of a US stock from SEC filings. Not financial advice.")
def main(
    ticker: Annotated[str, typer.Argument(help="US stock ticker, for example AAPL.")],
    fmt: Annotated[
        OutputFormat, typer.Option("--format", "-f", help="Report format.")
    ] = OutputFormat.HTML,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out", "-o", help="Output file; '-' writes to stdout. Default: ./TICKER-forensics.EXT"
        ),
    ] = None,
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache", help="Do not read cached SEC responses (still refreshes the cache)."
        ),
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show the version."
        ),
    ] = False,
) -> None:
    del version  # handled by the callback
    load_env_file()
    err = Console(stderr=True)
    ticker = ticker.strip()
    if not ticker:
        err.print("[red]Please give a ticker, for example: forensics AAPL[/red]")
        raise typer.Exit(EXIT_USAGE)

    try:
        client = build_client(no_cache=no_cache)
    except MissingUserAgentError:
        err.print(
            f"[red]{USER_AGENT_ENV} is not set.[/red] The SEC blocks requests without contact "
            f"information. Set it in your environment or in a .env file, for example:\n"
            f'  {USER_AGENT_ENV}="stock-forensics you@example.com"'
        )
        raise typer.Exit(EXIT_USAGE) from None

    try:
        with client, _Reporter(err) as reporter:
            report = analyze(ticker, client=client, progress=reporter)
    except TickerNotFoundError as error:
        err.print(f"[red]{error}[/red]")
        raise typer.Exit(EXIT_FAILED) from None
    except SecEdgarError as error:
        err.print(f"[red]Could not load data from the SEC:[/red] {error}")
        raise typer.Exit(EXIT_FAILED) from None

    text = render(report, fmt.value)
    if out is not None and str(out) == "-":
        typer.echo(text, nl=False)
        _print_result(err, report, None)
        return
    path = out or default_output_path(report.ticker, fmt)
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as error:
        err.print(f"[red]Could not write {path}:[/red] {error}")
        raise typer.Exit(EXIT_FAILED) from None
    _print_result(err, report, path)


_STYLES = {
    Light.GREEN: "green",
    Light.YELLOW: "yellow",
    Light.RED: "red",
    Light.GRAY: "dim",
    Light.INFO: "blue",
}


def _print_result(console: Console, report: ForensicReport, path: Path | None) -> None:
    light = report.summary.overall
    style = _STYLES[light]
    console.print(
        f"[bold]{report.ticker}[/bold] {report.company_name}: "
        f"[{style}]{LIGHT_LABELS[light]}[/{style}]. {report.summary.headline}"
    )
    for area in report.summary.areas:
        console.print(
            f"  {area.label}: {LIGHT_LABELS[area.light]} ({area.summary})", highlight=False
        )
    if report.errors:
        console.print(f"[yellow]Missing sections: {', '.join(sorted(report.errors))}[/yellow]")
    if path is not None:
        console.print(f"Report written to [bold]{path}[/bold]")
    console.print(f"[dim]{report.disclaimer}[/dim]")
