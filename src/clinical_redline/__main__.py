"""Entry point for ``python -m clinical_redline``."""

from clinical_redline.cli import app


def main() -> None:
    """Run the Typer CLI application."""
    app()


if __name__ == "__main__":
    main()
