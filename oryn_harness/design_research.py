"""Agent RESEARCHER : recherche de références design d'apps similaires.

Utilise la recherche web + Playwright pour constituer un dossier de
références visuelles avant que le Planner ne commence. Ces références
sont ensuite injectées dans le contexte du Planner et du Generator
pour guider la direction créative.
"""
from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from .claude_runner import ClaudeResult, ClaudeRunner
from .config import HarnessConfig
from .prompts import RESEARCHER_PROMPT
from .state import StateManager

console = Console()


class DesignReference:
    """Une référence design trouvée par le Researcher."""

    def __init__(
        self,
        name: str,
        url: str,
        screenshot: str,
        why: str,
        design_notes: str,
    ):
        self.name = name
        self.url = url
        self.screenshot = screenshot
        self.why = why
        self.design_notes = design_notes


class DesignResearcher:
    """Cherche des apps similaires en ligne et prend des screenshots comme références."""

    def __init__(self, config: HarnessConfig, state: StateManager):
        self.config = config
        self.state = state
        self.references_dir = config.workdir / ".oryn" / "references"
        self.runner = ClaudeRunner(
            cwd=config.workdir,
            model=config.researcher_model,
            system_prompt=RESEARCHER_PROMPT,
            permission_mode=config.permission_mode,
            timeout=config.cli_timeout,
            # Le researcher a besoin de WebSearch, Bash (Playwright), Read/Write
            allowed_tools=None,
        )

    def research(self) -> tuple[list[DesignReference], ClaudeResult]:
        """Lance la recherche de références design."""
        console.rule("[bold magenta]RESEARCHER — Design References")

        self.references_dir.mkdir(parents=True, exist_ok=True)

        prompt = f"""Prompt utilisateur :
\"\"\"
{self.config.user_prompt}
\"\"\"

Tu travailles dans `{self.config.workdir}`. Le dossier `.oryn/references/` existe déjà.
Le script Playwright est disponible : `python .oryn/scripts/pw_screenshot.py <url> --output <path>`

Ta mission :
1. Réfléchis au type de produit demandé. Quelles apps existantes sont similaires ou inspirantes ?
2. Utilise WebSearch pour trouver les meilleures apps dans ce domaine.
3. Visite 3 à 6 des meilleurs sites avec le script Playwright et prends des screenshots.
4. Écris `.oryn/references/references.json` avec les métadonnées.
5. Écris `.oryn/references/design_brief.md` avec ta synthèse design.

Sois SÉLECTIF. On veut des apps avec un VRAI bon design, pas des templates génériques.
Privilégie les apps connues pour leur craft (Linear, Notion, Stripe, Vercel, Arc, etc.)
qui sont pertinentes pour le type de produit demandé.
"""

        result = self.runner.run(prompt)
        self.state.write_trace("researcher", result.raw_stdout)

        if not result.success:
            console.print(f"[yellow]Researcher a rencontré des problèmes : {result.raw_stderr}[/yellow]")

        references = self._load_references()

        if references:
            console.print(f"[green]✓ Researcher a trouvé {len(references)} références design[/green]")
            for ref in references:
                console.print(f"  • [bold]{ref.name}[/bold] — {ref.why[:80]}")
        else:
            console.print("[yellow]⚠ Aucune référence trouvée, on continue sans.[/yellow]")

        return references, result

    def _load_references(self) -> list[DesignReference]:
        """Charge les références depuis le fichier JSON."""
        refs_path = self.references_dir / "references.json"
        if not refs_path.exists():
            return []

        try:
            data = json.loads(refs_path.read_text())
            return [
                DesignReference(
                    name=r.get("name", ""),
                    url=r.get("url", ""),
                    screenshot=r.get("screenshot", ""),
                    why=r.get("why", ""),
                    design_notes=r.get("design_notes", ""),
                )
                for r in data
            ]
        except (json.JSONDecodeError, KeyError) as e:
            console.print(f"[yellow]⚠ references.json parse error : {e}[/yellow]")
            return []

    def get_design_brief(self) -> str:
        """Retourne le contenu du design brief s'il existe."""
        brief_path = self.references_dir / "design_brief.md"
        if brief_path.exists():
            return brief_path.read_text()
        return ""

    def get_reference_summary(self) -> str:
        """Construit un résumé des références pour injection dans les prompts des autres agents."""
        references = self._load_references()
        if not references:
            return ""

        lines = ["# Références design (apps similaires analysées par le Researcher)\n"]

        for ref in references:
            lines.append(f"## {ref.name}")
            lines.append(f"- URL : {ref.url}")
            lines.append(f"- Screenshot : `{ref.screenshot}`")
            lines.append(f"- Pertinence : {ref.why}")
            lines.append(f"- Notes design : {ref.design_notes}")
            lines.append("")

        brief = self.get_design_brief()
        if brief:
            lines.append("## Synthèse design\n")
            lines.append(brief)

        return "\n".join(lines)
