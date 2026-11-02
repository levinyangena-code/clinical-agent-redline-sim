"""Typer CLI: ``python -m clinical_redline {run-ipi,run-rag,run-all}``."""

from pathlib import Path
from typing import Annotated

import typer

from clinical_redline.attacks import run_ipi_scenario, run_rag_poison_scenario
from clinical_redline.eval import compute_metrics, write_metrics
from clinical_redline.models import AttackResult

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Defensive, sandboxed clinical-agent red-team simulation (synthetic data only).",
)

SeedOpt = Annotated[int, typer.Option("--seed", help="Seed for deterministic runs.")]
OutdirOpt = Annotated[Path, typer.Option("--outdir", help="Output directory.")]


def _finish(results: list[AttackResult], outdir: Path) -> None:
    """Write per-scenario JSON and metrics.json, then echo the metrics."""
    outdir.mkdir(parents=True, exist_ok=True)
    for result in results:
        (outdir / f"{result.scenario}_result.json").write_text(
            result.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
    metrics = compute_metrics(results)
    path = write_metrics(metrics, outdir)
    typer.echo(f"wrote {path}")
    for name, value in metrics.items():
        typer.echo(f"{name}: {value}")


@app.command("run-ipi")
def run_ipi(
    seed: SeedOpt = 0,
    outdir: OutdirOpt = Path("out"),
    trials: Annotated[int, typer.Option(min=1, help="Number of trials.")] = 20,
    susceptibility: Annotated[
        float, typer.Option(min=0.0, max=1.0, help="Simulated agent deviation probability.")
    ] = 0.5,
    guardrails: Annotated[
        bool, typer.Option("--guardrails/--no-guardrails", help="Toggle guardrails.")
    ] = True,
) -> None:
    """Run the indirect prompt-injection scenario (benign marker only)."""
    result = run_ipi_scenario(
        seed, outdir, trials=trials, susceptibility=susceptibility, guardrails_enabled=guardrails
    )
    _finish([result], outdir)


@app.command("run-rag")
def run_rag(
    seed: SeedOpt = 0,
    outdir: OutdirOpt = Path("out"),
    trials: Annotated[int, typer.Option(min=1, help="Number of trials.")] = 8,
) -> None:
    """Run the RAG-poisoning scenario (synthetic documents only)."""
    _finish([run_rag_poison_scenario(seed, outdir, trials=trials)], outdir)


@app.command("run-all")
def run_all(seed: SeedOpt = 0, outdir: OutdirOpt = Path("out")) -> None:
    """Run both scenarios and write metrics.json."""
    _finish(
        [run_ipi_scenario(seed, outdir), run_rag_poison_scenario(seed, outdir)],
        outdir,
    )
