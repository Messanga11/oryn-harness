"""Agent GENERATOR : construit le code d'un sprint.

Améliorations clés :
1. Recherche la doc en ligne AVANT de coder
2. Lit les leçons apprises (erreurs passées) pour ne pas les refaire
3. Injecte les références design si dispo
"""
from __future__ import annotations

from rich.console import Console

from .claude_runner import ClaudeResult, ClaudeRunner
from .config import HarnessConfig
from .lessons import get_lessons_for_prompt
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

        # Leçons apprises (erreurs passées)
        lessons = get_lessons_for_prompt(
            categories=["expo", "tanstack-start", "convex", "nativewind", "turborepo", "react-native", "architecture"],
            limit=20,
        )
        lessons_section = ""
        if lessons:
            lessons_section = f"\n{lessons}\n"

        # Références design
        refs_dir = self.config.workdir / ".oryn" / "references"
        has_references = (refs_dir / "design_brief.md").exists()
        references_section = ""
        if has_references:
            references_section = """
# Références design
- Lis `.oryn/references/design_brief.md` pour les patterns design.
- LIS les screenshots dans `.oryn/references/` pour t'inspirer visuellement.
"""

        # Apps info
        apps = self.state.read_apps()
        apps_section = ""
        if apps:
            apps_lines = [f"  - {a.name} ({a.platform}): {a.description}" for a in apps]
            apps_section = f"\nCe projet est un MONOREPO multi-app :\n" + "\n".join(apps_lines) + f"\nCe sprint cible : {', '.join(sprint.target_apps)}\n"

        stack = self.config.stack

        if iteration == 0:
            prompt = f"""Tu commences le sprint **{sprint.id} — {sprint.title}**.
{apps_section}

# ÉTAPE 0 : LIRE LES GUIDES (OBLIGATOIRE)
AVANT TOUT, lis ces fichiers de référence :
1. `.oryn/guides/coding-patterns.md` — patterns senior dev (compound components, caching, error handling, performance)
2. `.oryn/guides/ui-ux-quality.md` — standards UI/UX (4 états obligatoires, micro-interactions, typo, couleurs)
3. `.oryn/guides/design-system-compliance.md` — règles du design system (zéro div/p/h1, pas de styles inline)

Ces guides sont la LOI. Chaque pattern, chaque règle DOIT être suivie.

# ÉTAPE 1 : RECHERCHE (OBLIGATOIRE avant de coder)
AVANT d'écrire une seule ligne de code, utilise WebSearch pour :
1. Chercher la doc officielle des technologies que tu vas utiliser dans ce sprint
   - Si auth : cherche "better-auth convex integration" ou "convex authentication"
   - Si TanStack Start : cherche "tanstack start file routes tutorial"
   - Si Expo : cherche "expo router v4 tabs layout"
   - Si NativeWind : cherche "nativewind v4 setup tailwind react native"
   - Si Gluestack : cherche "gluestack ui v2 [composant] example"
2. Chercher des exemples concrets similaires à ce que tu dois construire
3. Lire les guides/tutorials trouvés pour comprendre les patterns corrects

NE CODE PAS À L'AVEUGLE. Lis la doc d'abord.

# ÉTAPE 2 : Lire le contexte du projet
1. `.oryn/spec.md` (la vision)
2. `.oryn/apps.json` (les apps)
3. `.oryn/contracts/sprint_{sprint.id}.md` (le contrat — c'est la loi)
{references_section}

# ÉTAPE 3 : Coder
Stack : {stack.web_framework} | {stack.mobile_framework} + {stack.mobile_router} | {stack.ui_library} + {stack.styling}
Grid : {stack.web_grid_columns} cols web / {stack.mobile_grid_columns} cols mobile

RAPPELS CRITIQUES :
- ZÉRO <div>/<View>/<p>/<h1> → utilise Box, Typography, etc. depuis @repo/ui
- Le MÊME composant de feature marche sur web ET mobile
- Icônes : lucide-react-native (import {{ Icon }} from 'lucide-react-native')
- Chaque hook/service/composant a son .test.ts co-localisé
- Ce sprint est une TRANCHE VERTICALE : backend + frontend + routes + tests

# ÉTAPE 4 : Vérifier
- `pnpm turbo test` doit passer
- L'app web doit démarrer (`cd apps/web && pnpm dev`)
- L'app mobile doit builder (`cd apps/mobile && npx expo start`)

Git commit : `[sprint-{sprint.id}] <résumé>`
{lessons_section}
Termine par le bloc structuré (SPRINT_ID, STATUS, WHAT_I_DID, TEST_INSTRUCTIONS).
"""
        else:
            prompt = f"""Tu itères sur le sprint **{sprint.id} — {sprint.title}** (itération {iteration}).
{apps_section}

# CRITIQUE DE L'EVALUATOR
Lis la critique : `.oryn/critiques/sprint_{sprint.id}_iter_{iteration - 1:03d}.md`

L'Evaluator a LANCÉ les apps (browser + émulateur Android) et capturé les logs/erreurs.
Ses observations sont basées sur des FAITS, pas sur une review de code.

# CE QUE TU DOIS FAIRE
1. Si l'Evaluator mentionne une erreur de DÉPENDANCE ou de CONFIG :
   → Utilise WebSearch pour trouver la bonne config/version
2. Si l'Evaluator mentionne un CRASH au lancement :
   → Lis les logs d'erreur exacts et fixe la cause racine
3. Pour chaque critère FAIL du contrat → fixe-le
4. Re-teste localement : `pnpm turbo test`, lance l'app web + mobile

Lis aussi `.oryn/contracts/sprint_{sprint.id}.md` pour rappel du contrat.
{lessons_section}
Si tu patches la même chose 3 fois → `STATUS: RESTART_REQUESTED`.

Git commit et termine par le bloc structuré.
"""

        result = self.runner.run(prompt)
        self.state.write_trace(f"generator_{sprint.id}_iter{iteration}", result.raw_stdout)

        return result
