"""Tidemark CLI entrypoint.

Read-only copilot: the CLI can evaluate the rulebook and emit alerts, but
it never places orders and never touches exchange trading permissions.
"""

from __future__ import annotations

import typer

from tidemark import __version__

app = typer.Typer(
    name="tidemark",
    help=(
        "Tidemark: a rule-based market-structure copilot for crypto markets. "
        "Read-only. Never places orders."
    ),
    no_args_is_help=True,
)


@app.command()
def run() -> None:
    """Evaluate the rulebook against closed candles and emit alerts.

    Not implemented in Phase 0 — this is a repository/tooling skeleton
    only. Strategy logic lands in a later phase.
    """
    raise NotImplementedError("Pipeline execution is not implemented in Phase 0.")


@app.command()
def version() -> None:
    """Print the installed Tidemark version."""
    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
