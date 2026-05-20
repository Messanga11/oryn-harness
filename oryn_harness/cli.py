"""CLI entry point.

Usage :
    oryn build "construis un uber-like avec app client et app chauffeur"
    oryn build "..." --workdir ./my-app --budget-usd 100
    oryn build "..." --app "client-app:mobile:App client" --app "driver-app:mobile:App chauffeur"
    oryn build "..." --no-design-research
    oryn status --workdir ./my-app
    oryn resume --workdir ./my-app
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import AppDefinition, HarnessConfig, StackConfig, TestConfig
from .loop import HarnessLoop
from .state import SprintStatus, StateManager

app = typer.Typer(help="oryn-harness : long-running autonomous coding harness for multi-app monorepos")
console = Console()


def _parse_app_definition(value: str) -> AppDefinition:
    """Parse une app definition du format 'name:platform:description'."""
    parts = value.split(":", maxsplit=2)
    if len(parts) < 2:
        raise typer.BadParameter(f"Format invalide : '{value}'. Attendu : 'name:platform:description'")
    return AppDefinition(
        name=parts[0],
        platform=parts[1],
        description=parts[2] if len(parts) > 2 else parts[0],
    )


@app.command()
def build(
    prompt: str = typer.Argument(..., help="Prompt utilisateur (la chose à construire)"),
    workdir: Path = typer.Option(Path("./oryn-build"), help="Répertoire de travail"),
    # Models
    planner_model: str = typer.Option("claude-opus-4-7"),
    generator_model: str = typer.Option("claude-sonnet-4-6"),
    evaluator_model: str = typer.Option("claude-opus-4-7"),
    researcher_model: str = typer.Option("claude-sonnet-4-6"),
    # Limites
    budget_usd: float = typer.Option(500.0, help="Budget max en USD"),
    max_iterations: int = typer.Option(500, help="Max itérations totales"),
    max_per_sprint: int = typer.Option(8, help="Max itérations par sprint"),
    permission_mode: str = typer.Option("dangerous", help="auto | dangerous"),
    pass_threshold: float = typer.Option(0.75, help="Score min pour passer un sprint"),
    # Features
    design_research: bool = typer.Option(True, help="Chercher des apps similaires comme références"),
    # Apps (peut être répété : --app "client:mobile:App client" --app "admin:web:Dashboard")
    apps: Optional[list[str]] = typer.Option(None, "--app", help="App definition (name:platform:description)"),
    # Stack overrides
    web_framework: str = typer.Option("tanstack-start", help="Framework web"),
    mobile_framework: str = typer.Option("expo", help="Framework mobile"),
    ui_library: str = typer.Option("gluestack-ui", help="UI library"),
    cms: str = typer.Option("vex", help="CMS backend"),
):
    """Lance un nouveau run de harness."""
    workdir.mkdir(parents=True, exist_ok=True)

    # Parse app definitions
    app_definitions = []
    if apps:
        for app_str in apps:
            app_definitions.append(_parse_app_definition(app_str))

    config = HarnessConfig(
        user_prompt=prompt,
        workdir=workdir,
        planner_model=planner_model,
        generator_model=generator_model,
        evaluator_model=evaluator_model,
        researcher_model=researcher_model,
        budget_usd=budget_usd,
        max_total_iterations=max_iterations,
        max_iterations_per_sprint=max_per_sprint,
        permission_mode=permission_mode,
        pass_threshold=pass_threshold,
        enable_design_research=design_research,
        apps=app_definitions,
        stack=StackConfig(
            web_framework=web_framework,
            mobile_framework=mobile_framework,
            ui_library=ui_library,
            cms=cms,
        ),
        tests=TestConfig(),
    )

    loop = HarnessLoop(config)
    loop.run()


@app.command()
def status(
    workdir: Path = typer.Option(Path("./oryn-build"), help="Répertoire du run"),
):
    """Affiche l'état d'un run en cours ou terminé."""
    state = StateManager(workdir)
    progress = state.read_progress()

    if not progress:
        console.print(f"[red]Pas de run trouvé dans {workdir}[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Prompt :[/bold] {progress.user_prompt}")
    console.print(f"[bold]Started :[/bold] {progress.started_at}")
    console.print(f"[bold]Cost :[/bold] ${progress.total_cost_usd:.2f}")
    console.print(f"[bold]Iterations :[/bold] {progress.total_iterations}")

    if progress.apps:
        console.print(f"[bold]Apps :[/bold]")
        for a in progress.apps:
            console.print(f"  • {a.name} ({a.platform}) — {a.description}")

    table = Table(title="Sprints")
    table.add_column("ID", style="cyan")
    table.add_column("Titre")
    table.add_column("Target")
    table.add_column("Status")
    table.add_column("Iter")
    table.add_column("Score")
    table.add_column("Restarts")

    for s in progress.sprints:
        color = {
            SprintStatus.PASSED: "green",
            SprintStatus.FAILED: "red",
            SprintStatus.IN_PROGRESS: "yellow",
            SprintStatus.PENDING: "dim",
            SprintStatus.RESTARTED: "magenta",
        }.get(s.status, "white")

        targets = ", ".join(s.target_apps) if s.target_apps else "shared"

        table.add_row(
            s.id,
            s.title,
            targets,
            f"[{color}]{s.status.value}[/{color}]",
            str(s.iterations),
            f"{s.last_score:.2f}" if s.last_score else "—",
            str(s.restart_count),
        )

    console.print(table)


@app.command()
def resume(
    workdir: Path = typer.Option(Path("./oryn-build"), help="Répertoire du run à reprendre"),
    budget_usd: float = typer.Option(500.0, help="Budget max en USD"),
    max_iterations: int = typer.Option(500, help="Max itérations totales"),
):
    """Reprend un run interrompu."""
    state = StateManager(workdir)
    progress = state.read_progress()

    if not progress:
        console.print(f"[red]Pas de run à reprendre dans {workdir}[/red]")
        raise typer.Exit(1)

    config = HarnessConfig(
        user_prompt=progress.user_prompt,
        workdir=workdir,
        budget_usd=budget_usd,
        max_total_iterations=max_iterations,
        enable_design_research=False,  # skip research on resume
    )

    loop = HarnessLoop(config)
    loop.state.write_progress(progress)
    loop.resume(progress)


if __name__ == "__main__":
    app()
