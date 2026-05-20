"""Agent PLANNER : décompose le prompt utilisateur en sprints."""
from __future__ import annotations

import json

from rich.console import Console

from .claude_runner import ClaudeResult, ClaudeRunner
from .config import HarnessConfig
from .prompts import PLANNER_PROMPT
from .state import AppInfo, Sprint, StateManager

console = Console()


class Planner:
    """Tourne UNE fois au début du run pour poser spec.md + feature_list.json + apps.json."""

    def __init__(self, config: HarnessConfig, state: StateManager):
        self.config = config
        self.state = state
        self.runner = ClaudeRunner(
            cwd=config.workdir,
            model=config.planner_model,
            system_prompt=PLANNER_PROMPT,
            permission_mode=config.permission_mode,
            timeout=config.cli_timeout,
            allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
        )

    def plan(self, design_context: str = "") -> tuple[list[Sprint], ClaudeResult]:
        """Lance le planner et retourne les sprints créés."""
        console.rule("[bold cyan]PLANNER")

        # Construire la section apps si l'utilisateur en a spécifié
        apps_section = ""
        if self.config.apps:
            apps_json = json.dumps(
                [{"name": a.name, "platform": a.platform, "description": a.description}
                 for a in self.config.apps],
                indent=2,
            )
            apps_section = f"""
# Apps demandées par l'utilisateur
L'utilisateur a explicitement demandé ces apps :
```json
{apps_json}
```
Respecte cette décomposition. Écris `.oryn/apps.json` avec ces apps.
"""

        # Construire la section références design
        references_section = ""
        if design_context:
            references_section = f"""
# Références design disponibles
Le RESEARCHER a analysé des apps similaires. Lis attentivement :
- `.oryn/references/design_brief.md` — synthèse des patterns design
- `.oryn/references/references.json` — liste des apps analysées
- Les screenshots dans `.oryn/references/` — **LIS-LES** (tu peux lire les images)

UTILISE ces références pour ta direction créative. Ne copie pas, inspire-toi.

Résumé :
{design_context[:3000]}
"""

        # Construire la section stack
        stack = self.config.stack
        stack_section = f"""
# Stack imposée
- Monorepo : {stack.monorepo_tool} + {stack.package_manager}
- Web : {stack.web_framework}
- Mobile : {stack.mobile_framework} + {stack.mobile_router}
- UI : {stack.ui_library} + {stack.styling}
- CMS : {stack.cms} sur {stack.database}
- Auth : {stack.auth}
- Component library repo : {stack.component_library_repo}
- FormBuilder : {stack.form_builder_repo}
- TablePage : {stack.table_page_repo}
- Grid : {stack.web_grid_columns} cols web / {stack.mobile_grid_columns} cols mobile
"""

        prompt = f"""Prompt utilisateur :
\"\"\"
{self.config.user_prompt}
\"\"\"

Tu travailles dans `{self.config.workdir}`. Le dossier `.oryn/` existe déjà.
{stack_section}
{apps_section}
{references_section}
Ta mission :
1. Analyse le prompt. Identifie combien d'apps sont nécessaires (web, mobile, multi-app).
2. {"Consulte les screenshots de référence dans `.oryn/references/`." if design_context else ""}
3. Écris `.oryn/apps.json` avec la liste des apps.
4. Écris `.oryn/spec.md` avec la vision, direction créative, et architecture.
5. Écris `.oryn/feature_list.json` avec 5 à 8 sprints.

Rappel :
- Sprint 0 = scaffolding monorepo + component library (cloner formbuilder + table-page)
- Sprint 1 = backend/API (Convex + Vex CMS)
- Sprints 2+ = features (dans packages/features/)
- Avant-dernier = wiring des apps (routes TanStack Start + Expo Router)
- Dernier = testing + polish (Playwright, Maestro, Lighthouse, sécurité)

Sois OPINIONÉ. Pas de gradients violets. Pas d'AI slop.
"""

        result = self.runner.run(prompt)
        self.state.write_trace("planner", result.raw_stdout)

        if not result.success:
            console.print(f"[red]Planner a échoué : {result.raw_stderr}[/red]")
            return [], result

        # Lire les sprints et apps produits
        sprints = self.state.read_feature_list()
        apps = self.state.read_apps()

        if not sprints:
            console.print("[red]⚠ Planner n'a pas produit feature_list.json[/red]")
            return [], result

        console.print(f"[green]✓ Planner a créé {len(sprints)} sprints[/green]")
        if apps:
            console.print(f"[green]✓ {len(apps)} apps définies :[/green]")
            for a in apps:
                console.print(f"  • [bold]{a.name}[/bold] ({a.platform}) — {a.description}")

        for s in sprints:
            targets = ", ".join(s.target_apps) if s.target_apps else "shared"
            console.print(f"  • [bold]{s.id}[/bold] — {s.title} [{targets}]")

        return sprints, result
