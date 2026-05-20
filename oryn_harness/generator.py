"""Agent GENERATOR : construit le code d'un sprint."""
from __future__ import annotations

from rich.console import Console

from .claude_runner import ClaudeResult, ClaudeRunner
from .config import HarnessConfig
from .prompts import GENERATOR_PROMPT
from .state import Sprint, StateManager

console = Console()


class Generator:
    """Lance le builder sur un sprint donné, avec contexte propre."""

    def __init__(self, config: HarnessConfig, state: StateManager):
        self.config = config
        self.state = state
        self.runner = ClaudeRunner(
            cwd=config.workdir,
            model=config.generator_model,
            system_prompt=GENERATOR_PROMPT,
            permission_mode=config.permission_mode,
            timeout=config.cli_timeout,
            allowed_tools=None,
        )

    def build(
        self,
        sprint: Sprint,
        iteration: int,
        last_critique: str | None = None,
    ) -> ClaudeResult:
        """Construit ou itère sur un sprint."""
        console.rule(f"[bold yellow]GENERATOR — sprint {sprint.id} iter {iteration}")

        # Check si des références design existent
        refs_dir = self.config.workdir / ".oryn" / "references"
        has_references = (refs_dir / "design_brief.md").exists()

        references_instruction = ""
        if has_references:
            references_instruction = """
IMPORTANT — Références design :
- Lis `.oryn/references/design_brief.md` pour les patterns design à suivre.
- Les screenshots de référence sont dans `.oryn/references/` — LIS-LES.
- `.oryn/references/references.json` contient les notes détaillées par app.
- Utilise ces références pour guider tes choix de design (couleurs, typo, spacing, layout).
- Le design web et mobile doivent être pensés SÉPARÉMENT.
"""

        # Info sur les apps du projet
        apps = self.state.read_apps()
        apps_info = ""
        if apps:
            apps_lines = [f"  - {a.name} ({a.platform}): {a.description}" for a in apps]
            apps_info = f"""
Ce projet est un MONOREPO multi-app :
{chr(10).join(apps_lines)}

Ce sprint cible : {', '.join(sprint.target_apps)}
"""

        # Stack info rapide
        stack = self.config.stack
        stack_info = f"""
Stack : {stack.monorepo_tool} + {stack.package_manager} | Web: {stack.web_framework} | Mobile: {stack.mobile_framework} + {stack.mobile_router}
UI: {stack.ui_library} + {stack.styling} | CMS: {stack.cms} | Grid: {stack.web_grid_columns} cols web / {stack.mobile_grid_columns} cols mobile
FormBuilder: {stack.form_builder_repo} | TablePage: {stack.table_page_repo}
"""

        if iteration == 0:
            prompt = f"""Tu commences l'implémentation du sprint **{sprint.id} — {sprint.title}**.
{stack_info}
{apps_info}

Avant tout, LIS dans cet ordre :
1. `.oryn/spec.md` (la vision)
2. `.oryn/apps.json` (les apps du monorepo)
3. `.oryn/feature_list.json` (où ce sprint s'inscrit)
4. `.oryn/contracts/sprint_{sprint.id}.md` (ce que tu DOIS livrer — c'est la loi)
{references_instruction}

Puis CODE. Implémente chaque critère du contrat :
- Les composants UI dans packages/ui/
- Les features dans packages/features/<domain>/
- Les routes web dans apps/web/src/routes/
- Les routes mobile dans apps/mobile/app/
- Les tests co-localisés (.test.ts/.test.tsx)

RAPPELS CRITIQUES :
- Le MÊME composant de feature doit fonctionner sur web ET mobile
- Le grid utilise {stack.web_grid_columns} colonnes sur web, {stack.mobile_grid_columns} sur mobile
- Les imports depuis @repo/ui et @repo/features, pas de chemins relatifs inter-packages
- CHAQUE hook, service, et composant a son fichier .test.ts

Commit en git : `[sprint-{sprint.id}] <résumé>`.
Termine par le bloc structuré (voir ton system prompt).
"""
        else:
            prompt = f"""Tu itères sur le sprint **{sprint.id} — {sprint.title}** (itération {iteration}).
{stack_info}
{apps_info}

EVALUATOR a posté une critique. Lis-la :
`.oryn/critiques/sprint_{sprint.id}_iter_{iteration - 1:03d}.md`

Lis aussi `.oryn/contracts/sprint_{sprint.id}.md` pour rappel du contrat.

Pour CHAQUE issue bloquante, fixe-la. Pour chaque critère FAIL, fixe-le.
Re-teste localement (vitest, curl, etc.).

ATTENTION :
- Vérifie que l'architecture feature-based est respectée
- Vérifie que les composants sont dans packages/ui/ pas dans les features
- Vérifie que le grid fonctionne ({stack.web_grid_columns} cols web, {stack.mobile_grid_columns} cols mobile)
- Vérifie que les tests passent

Si tu patches la même chose 3 fois → `STATUS: RESTART_REQUESTED`.

Commit et termine par le bloc structuré.
"""

        result = self.runner.run(prompt)
        self.state.write_trace(f"generator_{sprint.id}_iter{iteration}", result.raw_stdout)

        return result
