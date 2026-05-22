"""Agent GENERATOR : construit le code d'un sprint.

Avant de passer au Reviewer, le Generator DOIT :
1. Vérifier que l'infra fonctionne (web server, convex, emulateur)
2. Vérifier que les tests passent
3. Prendre des screenshots des pages pertinentes au sprint
4. Fournir les URLs exactes et le port au Reviewer
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from rich.console import Console

from .claude_runner import ClaudeResult, ClaudeRunner
from .config import HarnessConfig
from .infra import InfraManager, get_web_base_url
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
        """Construit ou itère sur un sprint, puis vérifie avant de passer au Reviewer."""
        console.rule(f"[bold yellow]GENERATOR — sprint {sprint.id} iter {iteration}")

        # Leçons apprises
        lessons = get_lessons_for_prompt(
            categories=["expo", "tanstack-start", "convex", "nativewind", "turborepo", "react-native", "architecture"],
            limit=20,
        )
        lessons_section = f"\n{lessons}\n" if lessons else ""

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
            prompt = self._first_iteration_prompt(sprint, stack, apps_section, references_section, lessons_section)
        else:
            prompt = self._fix_iteration_prompt(sprint, iteration, stack, apps_section, lessons_section)

        # === Phase 1 : Le Generator code ===
        result = self.runner.run(prompt)
        self.state.write_trace(f"generator_{sprint.id}_iter{iteration}", result.raw_stdout)

        # === Phase 2 : Le HARNESS vérifie l'infra + prend les screenshots ===
        if "RESTART_REQUESTED" not in result.text:
            self._verify_and_capture(sprint, result)

        return result

    def _verify_and_capture(self, sprint: Sprint, gen_result: ClaudeResult) -> None:
        """Après le coding, vérifie compilation + infra et capture les preuves."""
        console.print("[bold]Post-build : compilation → tests → infra → screenshots[/bold]")

        evidence_dir = self.config.workdir / ".oryn" / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        # 1. COMPILATION — doit passer avant tout
        console.print("[blue]  TypeScript compilation check...[/blue]")
        build_output = ""
        build_ok = False
        try:
            proc = subprocess.run(
                ["pnpm", "turbo", "build"],
                cwd=str(self.config.workdir),
                capture_output=True, text=True, timeout=180,
            )
            build_output = proc.stdout[-3000:] + "\n" + proc.stderr[-1000:]
            build_ok = proc.returncode == 0
            if build_ok:
                console.print("[green]  Build: PASS[/green]")
            else:
                console.print("[red]  Build: FAIL — le projet ne compile pas[/red]")
                # Écrire les erreurs pour que le Generator puisse les lire au prochain tour
                (evidence_dir / f"gen_{sprint.id}_build_errors.txt").write_text(build_output)
        except Exception as e:
            build_output = str(e)
            console.print(f"[red]  Build: {e}[/red]")

        # Si ça ne compile pas → pas la peine de continuer
        if not build_ok:
            report = {
                "sprint_id": sprint.id,
                "build_ok": False,
                "build_errors": build_output[-3000:],
                "web_healthy": False,
                "android_running": False,
                "convex_running": False,
                "test_output": "",
                "screenshots": [],
                "urls_tested": [],
            }
            (evidence_dir / f"gen_{sprint.id}_report.json").write_text(json.dumps(report, indent=2))
            console.print("[red]  Le projet ne compile pas — le Reviewer va demander un fix[/red]")
            return

        # 2. Unit tests
        console.print("[blue]  Running unit tests...[/blue]")
        test_output = ""
        try:
            proc = subprocess.run(
                ["pnpm", "turbo", "test"],
                cwd=str(self.config.workdir),
                capture_output=True, text=True, timeout=120,
            )
            test_output = proc.stdout[-2000:] + "\n" + proc.stderr[-500:]
            passed = proc.returncode == 0
            console.print(f"  [{'green' if passed else 'red'}]  Tests: {'PASS' if passed else 'FAIL'}[/]")
        except Exception as e:
            test_output = str(e)
            console.print(f"  [yellow]  Tests: {e}[/yellow]")

        # 3. Lancer l'infra
        infra = InfraManager(self.config.workdir)
        services = infra.start_all()

        web_svc = services.get("web")
        web_healthy = web_svc.healthy if web_svc else False

        # 3. Screenshots des pages pertinentes au sprint
        screenshots_taken = []
        if web_healthy:
            urls = self._get_sprint_urls(sprint)
            console.print(f"  [blue]  Taking screenshots: {len(urls)} pages[/blue]")

            pw_script = self.config.workdir / ".oryn" / "scripts" / "pw_check.py"
            if pw_script.exists():
                for name, url in urls:
                    path = evidence_dir / f"gen_{sprint.id}_{name}.png"
                    try:
                        subprocess.run(
                            ["python3", str(pw_script), url, "--screenshot", str(path)],
                            capture_output=True, text=True, timeout=20,
                        )
                        if path.exists():
                            screenshots_taken.append((name, str(path)))
                            console.print(f"    [green]  {name} → {path.name}[/green]")
                        else:
                            console.print(f"    [yellow]  {name} → screenshot failed[/yellow]")
                    except Exception:
                        console.print(f"    [yellow]  {name} → timeout[/yellow]")

        # 4. Cleanup infra
        infra.stop_all()

        # 5. Écrire le rapport du Generator dans .oryn/evidence/
        report = {
            "sprint_id": sprint.id,
            "build_ok": True,
            "web_healthy": web_healthy,
            "android_running": services.get("android", type("", (), {"running": False})).running,
            "convex_running": services.get("convex", type("", (), {"running": False})).running,
            "test_output": test_output,
            "screenshots": [{"name": n, "path": p} for n, p in screenshots_taken],
            "urls_tested": [{"name": n, "url": u} for n, u in self._get_sprint_urls(sprint)],
        }
        report_path = evidence_dir / f"gen_{sprint.id}_report.json"
        report_path.write_text(json.dumps(report, indent=2))

        console.print(
            f"[bold]  Résumé: web={'✓' if web_healthy else '✗'} "
            f"screenshots={len(screenshots_taken)} "
            f"tests={'✓' if 'FAIL' not in test_output.upper() else '✗'}[/bold]"
        )

    def _get_sprint_urls(self, sprint: Sprint) -> list[tuple[str, str]]:
        """Détermine les URLs à screenshot en fonction du sprint."""
        base = get_web_base_url(self.config.workdir)
        urls: list[tuple[str, str]] = [("home", base)]

        sid = sprint.id.lower()
        title = sprint.title.lower()
        desc = sprint.description.lower()
        text = f"{sid} {title} {desc}"

        # Mapper les mots-clés du sprint vers des routes probables
        route_hints = {
            "auth": [("login", "/login"), ("signup", "/signup"), ("register", "/register")],
            "login": [("login", "/login")],
            "signup": [("signup", "/signup"), ("register", "/register")],
            "dashboard": [("dashboard", "/dashboard")],
            "profile": [("profile", "/profile"), ("settings", "/settings")],
            "note": [("notes", "/notes"), ("note-new", "/notes/new")],
            "editor": [("editor", "/editor"), ("notes", "/notes")],
            "chat": [("chat", "/chat")],
            "search": [("search", "/search")],
            "setting": [("settings", "/settings")],
            "admin": [("admin", "/admin")],
            "pricing": [("pricing", "/pricing")],
            "onboarding": [("onboarding", "/onboarding")],
            "checkout": [("checkout", "/checkout")],
            "cart": [("cart", "/cart")],
            "order": [("orders", "/orders")],
            "product": [("products", "/products")],
            "user": [("users", "/users")],
            "team": [("team", "/team")],
            "folder": [("folders", "/folders")],
            "tag": [("tags", "/tags")],
        }

        for keyword, routes in route_hints.items():
            if keyword in text:
                for name, path in routes:
                    urls.append((name, f"{base}{path}"))

        # Dédupliquer
        seen = set()
        unique: list[tuple[str, str]] = []
        for name, url in urls:
            if url not in seen:
                seen.add(url)
                unique.append((name, url))

        return unique

    def _first_iteration_prompt(self, sprint, stack, apps_section, references_section, lessons_section) -> str:
        return f"""Tu commences le sprint **{sprint.id} — {sprint.title}**.
{apps_section}

# ÉTAPE 0 : LIRE LES GUIDES (OBLIGATOIRE)
AVANT TOUT, lis ces fichiers de référence :
1. `.oryn/guides/coding-patterns.md` — patterns senior dev
2. `.oryn/guides/ui-ux-quality.md` — standards UI/UX
3. `.oryn/guides/design-system-compliance.md` — règles du design system

# ÉTAPE 1 : RECHERCHE (OBLIGATOIRE avant de coder)
Utilise WebSearch pour chercher la doc des technologies de ce sprint :
- TanStack Start : "tanstack start file routes tutorial"
- Expo : "expo router v4 tabs layout"
- NativeWind : "nativewind v4 setup"
- Convex : "convex [feature] tutorial"
- Gluestack : "gluestack ui v2 [composant]"

NE CODE PAS À L'AVEUGLE. Lis la doc.

# ÉTAPE 2 : Lire le contexte
1. `.oryn/spec.md`
2. `.oryn/apps.json`
3. `.oryn/contracts/sprint_{sprint.id}.md` (c'est la loi)
{references_section}

# ÉTAPE 3 : Coder
Stack : {stack.web_framework} | {stack.mobile_framework} + {stack.mobile_router} | {stack.ui_library} + {stack.styling}
Grid : {stack.web_grid_columns} cols web / {stack.mobile_grid_columns} cols mobile

RAPPELS :
- ZÉRO <div>/<View>/<p>/<h1> → Box, Typography depuis @repo/ui
- Le MÊME composant marche web ET mobile
- Icônes : lucide-react-native
- Tests co-localisés (.test.ts)
- TRANCHE VERTICALE : backend + frontend + routes + tests

# ÉTAPE 4 : VÉRIFIER TOI-MÊME (OBLIGATOIRE avant de finir)
AVANT de terminer, tu DOIS vérifier que ton code fonctionne :

1. **Tests unitaires** : `pnpm turbo test`
   → Si des tests FAIL, fixe-les AVANT de finir

2. **App web** : `cd apps/web && pnpm dev`
   → Vérifie que le serveur démarre sans erreur
   → Navigue vers les routes que tu as créées/modifiées
   → Vérifie visuellement que ça rend correctement

3. **App mobile** : `cd apps/mobile && npx expo start`
   → Vérifie que Metro bundler démarre sans erreur

4. **Si tu trouves des bugs pendant ta vérification** → fixe-les immédiatement

Le harness va ensuite lancer l'infra, prendre des screenshots des pages liées au sprint,
et tout passer au Reviewer. Si l'app ne démarre pas = FAIL automatique.

Git commit : `[sprint-{sprint.id}] <résumé>`
{lessons_section}
# OUTPUT OBLIGATOIRE
```
SPRINT_ID: {sprint.id}
STATUS: <READY_FOR_REVIEW | NEEDS_MORE_WORK | RESTART_REQUESTED>
WHAT_I_DID:
- <bullets concrets>
WHAT_I_DIDN'T_DO:
- <ce qui manque et pourquoi>
TESTS_WRITTEN:
- <liste des tests>
ROUTES_CREATED:
- <routes web créées/modifiées, ex: /login, /dashboard, /notes>
- <routes mobile créées/modifiées, ex: app/(tabs)/notes.tsx>
WEB_PORT: <port du serveur web, default 3000>
TEST_INSTRUCTIONS_FOR_EVALUATOR:
- <commandes exactes pour lancer et tester>
```
"""

    def _fix_iteration_prompt(self, sprint, iteration, stack, apps_section, lessons_section) -> str:
        return f"""Tu itères sur le sprint **{sprint.id} — {sprint.title}** (itération {iteration}).
{apps_section}

# CRITIQUE DE L'EVALUATOR
Lis la critique : `.oryn/critiques/sprint_{sprint.id}_iter_{iteration - 1:03d}.md`

L'Evaluator a LANCÉ les apps et capturé des screenshots + logs.
Regarde les screenshots dans `.oryn/evidence/` pour voir ce que l'app rend RÉELLEMENT.

# CE QUE TU DOIS FAIRE
1. LIS la critique et les screenshots d'evidence
2. Si erreur de dépendance/config → WebSearch pour la bonne solution
3. Si crash → lis les logs exacts et fixe la cause racine
4. Pour chaque critère FAIL → fixe-le
5. **VÉRIFIE TOI-MÊME** :
   - `pnpm turbo test` passe ?
   - L'app web démarre ?
   - Les pages du sprint rendent correctement ?

Lis aussi `.oryn/contracts/sprint_{sprint.id}.md` pour rappel.
{lessons_section}
Si tu patches la même chose 3 fois → `STATUS: RESTART_REQUESTED`.

Git commit et termine par le bloc structuré (inclus ROUTES_CREATED + WEB_PORT).
"""
