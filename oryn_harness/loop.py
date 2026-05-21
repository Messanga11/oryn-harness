"""Orchestrateur principal du harness.

Enchaîne :
    Researcher → Planner → ComponentLibrary setup → pour chaque sprint :
        ContractNegotiator → loop(Generator → Evaluator) jusqu'à PASS/RESTART/budget
"""
from __future__ import annotations

import re
from datetime import datetime

from rich.console import Console
from rich.panel import Panel

from .component_library import ComponentLibraryManager
from .config import HarnessConfig
from .contract import ContractNegotiator
from .design_research import DesignResearcher
from .evaluator import Evaluator
from .generator import Generator
from .planner import Planner
from .guides import install_guides
from .playwright_runner import install_helpers
from .scaffold import scaffold_monorepo, add_web_app, add_mobile_app, install_deps
from .state import ProgressState, Sprint, SprintStatus, StateManager
from .test_pipeline import install_test_helpers

console = Console()


class HarnessLoop:
    """Le top-level loop. Gère budget, state, restarts."""

    def __init__(self, config: HarnessConfig):
        self.config = config
        self.state = StateManager(config.workdir)
        self.state.init()
        install_helpers(config.workdir)
        install_test_helpers(config.workdir)
        install_guides(config.workdir)

        self.researcher = DesignResearcher(config, self.state) if config.enable_design_research else None
        self.component_library = ComponentLibraryManager(config, self.state)
        self.planner = Planner(config, self.state)
        self.negotiator = ContractNegotiator(config, self.state)
        self.generator = Generator(config, self.state)
        self.evaluator = Evaluator(config, self.state)

    def run(self) -> ProgressState:
        """Lance le harness complet (fresh run)."""
        self._print_banner()

        # Étape 0 : Design Research (optionnel)
        design_context = ""
        research_cost = 0.0
        if self.researcher:
            references, research_result = self.researcher.research()
            research_cost = research_result.cost_usd
            if references:
                design_context = self.researcher.get_reference_summary()
                console.print(
                    f"[green]✓ {len(references)} références design collectées "
                    f"(${research_cost:.2f})[/green]"
                )

        # Étape 0.5 : Scaffold déterministe (pas d'IA ici)
        console.rule("[bold blue]SCAFFOLD")
        scaffold_monorepo(self.config.workdir)
        # Ajouter les apps par défaut (web + mobile) si pas d'apps custom
        if not self.config.apps:
            add_web_app(self.config.workdir)
            add_mobile_app(self.config.workdir)
        else:
            for app_def in self.config.apps:
                if app_def.platform in ("web", "both"):
                    add_web_app(self.config.workdir, app_def.name)
                if app_def.platform in ("mobile", "both"):
                    add_mobile_app(self.config.workdir, app_def.name)
        install_deps(self.config.workdir)

        # Étape 1 : Planner
        sprints, planner_result = self.planner.plan(design_context=design_context)
        if not sprints:
            console.print("[red]Pas de sprints produits, abandon.[/red]")
            raise RuntimeError("Planner failed to produce sprints")

        # Étape 1.5 : Component Library setup (IA enrichit ce que le scaffold a créé)
        lib_result = self.component_library.setup()
        lib_cost = lib_result.cost_usd

        # Lire les apps créées par le planner
        apps = self.state.read_apps()

        progress = ProgressState(
            started_at=datetime.now(),
            user_prompt=self.config.user_prompt,
            sprints=sprints,
            apps=[a for a in apps] if apps else [],
            total_cost_usd=research_cost + planner_result.cost_usd + lib_cost,
        )
        self.state.write_progress(progress)

        # Étape 2 : boucle sur les sprints
        self._run_sprints(progress)
        return progress

    def resume(self, progress: ProgressState) -> ProgressState:
        """Reprend un run existant — continue depuis le dernier sprint actif."""
        self._print_banner()

        console.print("[bold cyan]RESUME[/bold cyan]")

        # Utiliser current_sprint_id pour savoir où on en était
        current_id = progress.current_sprint_id
        resume_from_idx = 0

        if current_id:
            for i, s in enumerate(progress.sprints):
                if s.id == current_id:
                    resume_from_idx = i
                    break

        # Tout ce qui est AVANT le sprint courant → PASSED (code déjà produit)
        for i in range(resume_from_idx):
            s = progress.sprints[i]
            if s.status != SprintStatus.PASSED:
                console.print(f"  [dim]⏭ {s.id} — skip (code déjà produit)[/dim]")
                s.status = SprintStatus.PASSED

        # Le sprint courant et ceux après → reset si failed/restarted
        for s in progress.sprints[resume_from_idx:]:
            if s.status in (SprintStatus.FAILED, SprintStatus.RESTARTED, SprintStatus.IN_PROGRESS):
                console.print(f"  [yellow]↻ {s.id} — reset → pending[/yellow]")
                s.status = SprintStatus.PENDING
                s.iterations = 0
                s.restart_count = 0
            elif s.status == SprintStatus.PASSED:
                console.print(f"  [green]✓ {s.id} — déjà PASSED[/green]")

        # Reset les compteurs pour le nouveau run
        progress.total_iterations = 0
        progress.total_cost_usd = 0.0

        remaining = [s for s in progress.sprints if s.status != SprintStatus.PASSED]
        console.print(f"\n[green]Reprend depuis {progress.sprints[resume_from_idx].id} — {len(remaining)} sprints restants[/green]")

        self.state.write_progress(progress)
        self._run_sprints(progress)
        return progress

    def _run_sprints(self, progress: ProgressState) -> None:
        """Boucle sur les sprints (partagé entre run et resume)."""
        for sprint in progress.sprints:
            if self._budget_exceeded(progress):
                console.print("[red]Budget dépassé, on s'arrête.[/red]")
                break

            if self._iterations_exceeded(progress):
                console.print("[red]Max iterations atteint, on s'arrête.[/red]")
                break

            if sprint.status == SprintStatus.PASSED:
                continue

            self._run_sprint(sprint, progress)

            # Si le sprint n'est pas PASSED, on s'arrête (pas de skip au suivant)
            if sprint.status != SprintStatus.PASSED:
                console.print(f"[yellow]Sprint {sprint.id} non terminé ({sprint.status.value}), arrêt. Relance avec oryn resume.[/yellow]")
                break

        # Final
        progress.completed = all(
            s.status in (SprintStatus.PASSED, SprintStatus.FAILED)
            for s in progress.sprints
        )
        self.state.write_progress(progress)
        self._print_summary(progress)

    def _run_sprint(self, sprint: Sprint, progress: ProgressState) -> None:
        """Exécute un sprint complet : contract + loop generator/evaluator."""
        targets = ", ".join(sprint.target_apps) if sprint.target_apps else "shared"
        console.print(Panel.fit(
            f"[bold]{sprint.id} — {sprint.title}[/bold]\n"
            f"{sprint.description}\n"
            f"[dim]target: {targets}[/dim]",
            title="Sprint",
            border_style="blue",
        ))

        progress.current_sprint_id = sprint.id
        sprint.status = SprintStatus.IN_PROGRESS
        self.state.write_progress(progress)

        # Phase 1 : négociation contrat
        self.negotiator.negotiate(sprint, max_rounds=10)

        # Phase 2 : loop generator ⇄ evaluator
        consecutive_fails = 0
        for iteration in range(self.config.max_iterations_per_sprint):
            if self._budget_exceeded(progress):
                return
            if progress.total_iterations >= self.config.max_total_iterations:
                console.print("[red]Max iterations totales atteint.[/red]")
                return

            # Generator
            gen_result = self.generator.build(sprint, iteration)
            progress.total_cost_usd += gen_result.cost_usd
            progress.total_iterations += 1
            sprint.iterations += 1

            if "RESTART_REQUESTED" in gen_result.text:
                self._handle_restart(sprint, progress, reason="generator requested")
                return

            # Evaluator
            verdict, scores, eval_result = self.evaluator.evaluate(
                sprint, iteration, gen_result.text
            )
            progress.total_cost_usd += eval_result.cost_usd

            if scores:
                sprint.last_score = scores.weighted_total
                sprint.notes.append(
                    f"iter {iteration}: {verdict} (score={scores.weighted_total:.2f}) — {scores.feedback}"
                )

            self.state.write_progress(progress)

            # Décision
            if verdict == "PASS":
                if scores and scores.weighted_total >= self.config.pass_threshold:
                    sprint.status = SprintStatus.PASSED
                    console.print(f"[bold green]✅ Sprint {sprint.id} PASSED[/bold green]")
                    self.state.write_progress(progress)
                    return
                else:
                    console.print(
                        f"[yellow]⚠ Evaluator dit PASS mais score {scores.weighted_total if scores else '?'} "
                        f"< seuil {self.config.pass_threshold}. On itère.[/yellow]"
                    )
                    consecutive_fails = 0

            elif verdict == "REQUEST_RESTART":
                self._handle_restart(sprint, progress, reason="evaluator requested")
                return

            else:  # NEEDS_FIX
                consecutive_fails += 1
                if self.config.enable_full_restart and consecutive_fails >= self.config.restart_threshold:
                    self._handle_restart(sprint, progress, reason=f"{consecutive_fails} échecs consécutifs")
                    return

        # Si on sort de la boucle sans PASS
        sprint.status = SprintStatus.FAILED
        console.print(f"[red]❌ Sprint {sprint.id} FAILED (max iterations)[/red]")
        self.state.write_progress(progress)

    def _handle_restart(self, sprint: Sprint, progress: ProgressState, reason: str) -> None:
        sprint.restart_count += 1
        sprint.status = SprintStatus.RESTARTED
        sprint.notes.append(f"RESTART ({reason})")
        console.print(f"[bold magenta]🔄 Sprint {sprint.id} RESTART ({reason})[/bold magenta]")

        if sprint.restart_count >= self.config.max_restarts:
            console.print(f"[red]Sprint {sprint.id} restart {self.config.max_restarts}x, FAIL définitif.[/red]")
            sprint.status = SprintStatus.FAILED
        else:
            sprint.status = SprintStatus.PENDING
            sprint.iterations = 0

        self.state.write_progress(progress)

    def _budget_exceeded(self, progress: ProgressState) -> bool:
        return progress.total_cost_usd >= self.config.budget_usd

    def _iterations_exceeded(self, progress: ProgressState) -> bool:
        return progress.total_iterations >= self.config.max_total_iterations

    def _print_banner(self) -> None:
        stack = self.config.stack
        apps_info = ""
        if self.config.apps:
            apps_info = f"\napps : {', '.join(a.name for a in self.config.apps)}"

        console.print(Panel.fit(
            f"[bold cyan]oryn-harness[/bold cyan]\n"
            f"workdir : {self.config.workdir}\n"
            f"budget : ${self.config.budget_usd:.2f}\n"
            f"stack : {stack.web_framework} + {stack.mobile_framework} + {stack.ui_library}\n"
            f"grid : {stack.web_grid_columns} cols web / {stack.mobile_grid_columns} cols mobile\n"
            f"tests : {self.config.tests.unit_test_runner} + {self.config.tests.e2e_web_runner} + {self.config.tests.e2e_mobile_runner}"
            f"{apps_info}\n"
            f"max iterations : {self.config.max_total_iterations}",
            title="Démarrage",
        ))

    def _print_summary(self, progress: ProgressState) -> None:
        passed = sum(1 for s in progress.sprints if s.status == SprintStatus.PASSED)
        failed = sum(1 for s in progress.sprints if s.status == SprintStatus.FAILED)

        apps_line = ""
        if progress.apps:
            apps_line = f"\n[bold]Apps :[/bold] {', '.join(a.name for a in progress.apps)}"

        console.print(Panel.fit(
            f"[bold]Sprints PASS :[/bold] {passed}/{len(progress.sprints)}\n"
            f"[bold]Sprints FAIL :[/bold] {failed}\n"
            f"[bold]Itérations :[/bold] {progress.total_iterations}\n"
            f"[bold]Coût :[/bold] ${progress.total_cost_usd:.2f}\n"
            f"[bold]workdir :[/bold] {self.config.workdir}"
            f"{apps_line}",
            title="Run terminé",
            border_style="green" if passed == len(progress.sprints) else "yellow",
        ))
