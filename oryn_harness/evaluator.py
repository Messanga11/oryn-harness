"""Agent EVALUATOR : critique le travail du Generator avec tests complets.

Le harness lance les apps et capture les preuves AVANT d'appeler l'Evaluator.
L'Evaluator reçoit les screenshots, logs, et résultats de tests comme contexte
factuel — pas besoin de lui faire confiance pour lancer les apps.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Literal

from rich.console import Console

from .claude_runner import ClaudeResult, ClaudeRunner
from .config import HarnessConfig
from .infra import InfraManager, get_web_base_url
from .lessons import extract_lessons_from_critique
from .prompts import EVALUATOR_PROMPT
from .state import RubricScore, Sprint, StateManager

console = Console()

Verdict = Literal["PASS", "NEEDS_FIX", "REQUEST_RESTART"]


class _Removed:
    """placeholder"""

    def _removed(self) -> dict:
        """Lance les apps, capture tout, retourne un résumé."""
        evidence: dict = {
            "web": {},
            "mobile": {},
            "tests": {},
            "security": {},
        }

        # 1. Web app
        if (self.workdir / "apps" / "web").exists():
            evidence["web"] = self._test_web()

        # 2. Mobile app
        if (self.workdir / "apps" / "mobile").exists():
            evidence["mobile"] = self._test_mobile()

        # 3. Unit tests
        evidence["tests"]["unit"] = self._run_unit_tests()

        # 4. Security
        evidence["security"]["npm_audit"] = self._run_npm_audit()

        # Cleanup
        self._kill_all()

        return evidence

    def _test_web(self) -> dict:
        """Lance l'app web, screenshot, capture logs."""
        result: dict = {"launched": False, "logs": "", "screenshots": [], "errors": []}

        console.print("[blue]  Lancement app web...[/blue]")

        # Lancer pnpm dev en background
        web_dir = self.workdir / "apps" / "web"
        try:
            proc = subprocess.Popen(
                ["pnpm", "dev"],
                cwd=str(web_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._procs.append(proc)
        except FileNotFoundError:
            result["errors"].append("pnpm not found")
            return result

        # Attendre que le serveur démarre (max 30s)
        logs_lines: list[str] = []
        started = False
        start_time = time.time()

        while time.time() - start_time < 30:
            if proc.poll() is not None:
                # Process died
                remaining = proc.stdout.read() if proc.stdout else ""
                logs_lines.append(remaining)
                result["errors"].append(f"Web server crashed: {remaining[-500:]}")
                break

            line = ""
            try:
                line = proc.stdout.readline() if proc.stdout else ""
            except Exception:
                pass

            if line:
                logs_lines.append(line.rstrip())
                console.print(f"  [dim]{line.rstrip()[:120]}[/dim]")

                # Détecter que le serveur est prêt
                lower = line.lower()
                if any(kw in lower for kw in ["ready", "listening", "localhost:", "started", "http://"]):
                    started = True
                    break

                if any(kw in lower for kw in ["error", "failed", "cannot"]):
                    result["errors"].append(line.rstrip())

        result["logs"] = "\n".join(logs_lines[-50:])  # Dernières 50 lignes

        if not started:
            result["errors"].append("Web server did not start within 30s")
            console.print("[red]  Web server failed to start[/red]")
            return result

        result["launched"] = True
        console.print("[green]  Web server started[/green]")

        # Attendre un peu que l'app soit stable
        time.sleep(3)

        # Screenshot avec Playwright
        for page_name, url in [("home", "http://localhost:3000"), ("login", "http://localhost:3000/login")]:
            screenshot_path = self.evidence_dir / f"web_{page_name}.png"
            console_log = self._playwright_screenshot(url, str(screenshot_path))
            if screenshot_path.exists():
                result["screenshots"].append(str(screenshot_path))
                console.print(f"  [green]  Screenshot: {page_name}[/green]")
            if console_log:
                result[f"console_{page_name}"] = console_log

        # Curl check
        try:
            resp = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:3000"],
                capture_output=True, text=True, timeout=10,
            )
            result["http_status"] = resp.stdout.strip()
            console.print(f"  [dim]  HTTP {result['http_status']}[/dim]")
        except Exception:
            result["http_status"] = "error"

        return result

    def _test_mobile(self) -> dict:
        """Lance l'app mobile sur émulateur Android, capture logs."""
        result: dict = {"launched": False, "logs": "", "errors": [], "emulator": False}

        console.print("[blue]  Lancement app mobile...[/blue]")

        # Check si un emulateur Android tourne
        try:
            adb_out = subprocess.run(
                ["adb", "devices"], capture_output=True, text=True, timeout=5,
            )
            if "emulator" in adb_out.stdout:
                result["emulator"] = True
                console.print("[green]  Emulateur Android détecté[/green]")
            else:
                # Essayer de lancer un emulateur
                console.print("[yellow]  Pas d'émulateur, tentative de lancement...[/yellow]")
                avd_out = subprocess.run(
                    ["emulator", "-list-avds"], capture_output=True, text=True, timeout=5,
                )
                avds = [a.strip() for a in avd_out.stdout.strip().split("\n") if a.strip()]
                if avds:
                    console.print(f"  [dim]  AVD trouvé: {avds[0]}[/dim]")
                    emu_proc = subprocess.Popen(
                        ["emulator", f"@{avds[0]}", "-no-window", "-no-audio", "-no-boot-anim"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    self._procs.append(emu_proc)
                    # Attendre le boot
                    console.print("  [dim]  Attente boot émulateur...[/dim]")
                    subprocess.run(["adb", "wait-for-device"], timeout=60)
                    time.sleep(10)  # Laisser le temps au boot
                    result["emulator"] = True
                    console.print("[green]  Emulateur Android démarré[/green]")
                else:
                    result["errors"].append("Aucun AVD Android trouvé. Créer via Android Studio > Device Manager")
                    console.print("[yellow]  Pas d'AVD disponible, skip mobile[/yellow]")
                    return result
        except (FileNotFoundError, subprocess.TimeoutExpired):
            result["errors"].append("adb/emulator not found or timeout")
            console.print("[yellow]  adb/emulator non disponible, skip mobile[/yellow]")
            return result

        # Lancer Expo
        mobile_dir = self.workdir / "apps" / "mobile"
        try:
            proc = subprocess.Popen(
                ["npx", "expo", "start", "--android", "--no-dev"],
                cwd=str(mobile_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._procs.append(proc)
        except FileNotFoundError:
            result["errors"].append("npx not found")
            return result

        # Capturer les logs Expo (30s)
        logs_lines: list[str] = []
        start_time = time.time()

        while time.time() - start_time < 30:
            if proc.poll() is not None:
                remaining = proc.stdout.read() if proc.stdout else ""
                logs_lines.append(remaining)
                break

            line = ""
            try:
                line = proc.stdout.readline() if proc.stdout else ""
            except Exception:
                pass

            if line:
                logs_lines.append(line.rstrip())
                console.print(f"  [dim]{line.rstrip()[:120]}[/dim]")

                lower = line.lower()
                if any(kw in lower for kw in ["error", "failed", "cannot", "red screen"]):
                    result["errors"].append(line.rstrip())

                if any(kw in lower for kw in ["bundled", "running", "open on", "started"]):
                    result["launched"] = True

        result["logs"] = "\n".join(logs_lines[-50:])

        # Capturer logcat
        if result["emulator"]:
            try:
                logcat = subprocess.run(
                    ["adb", "logcat", "-d", "-s", "ReactNativeJS:*", "ReactNative:*"],
                    capture_output=True, text=True, timeout=10,
                )
                result["logcat"] = logcat.stdout[-2000:]  # Derniers 2000 chars
                # Chercher les erreurs
                for line in logcat.stdout.split("\n"):
                    if "error" in line.lower() or "fatal" in line.lower():
                        result["errors"].append(f"logcat: {line.strip()}")
            except Exception:
                pass

        if result["launched"]:
            console.print("[green]  App mobile lancée sur émulateur[/green]")
        else:
            console.print("[yellow]  App mobile: lancement incertain[/yellow]")

        return result

    def _run_unit_tests(self) -> dict:
        """Lance les tests unitaires."""
        result: dict = {"output": "", "pass_count": 0, "fail_count": 0, "ran": False}

        console.print("[blue]  Unit tests...[/blue]")

        try:
            proc = subprocess.run(
                ["pnpm", "turbo", "test", "--", "--reporter=verbose"],
                cwd=str(self.workdir),
                capture_output=True, text=True, timeout=120,
            )
            result["output"] = proc.stdout[-3000:] + "\n" + proc.stderr[-1000:]
            result["ran"] = True

            # Compter pass/fail
            for line in proc.stdout.split("\n"):
                if "✓" in line or "✅" in line or " pass" in line.lower():
                    result["pass_count"] += 1
                if "✗" in line or "❌" in line or " fail" in line.lower():
                    result["fail_count"] += 1

            console.print(f"  [dim]  {result['pass_count']} pass, {result['fail_count']} fail[/dim]")
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            result["output"] = str(e)
            console.print(f"  [yellow]  Tests: {e}[/yellow]")

        return result

    def _run_npm_audit(self) -> dict:
        """Lance npm audit."""
        result: dict = {"output": "", "critical": 0, "high": 0}

        console.print("[blue]  npm audit...[/blue]")

        try:
            proc = subprocess.run(
                ["pnpm", "audit", "--json"],
                cwd=str(self.workdir),
                capture_output=True, text=True, timeout=30,
            )
            result["output"] = proc.stdout[-2000:]
            try:
                audit = json.loads(proc.stdout)
                vuln = audit.get("metadata", {}).get("vulnerabilities", {})
                result["critical"] = vuln.get("critical", 0)
                result["high"] = vuln.get("high", 0)
            except (json.JSONDecodeError, KeyError):
                pass
            console.print(f"  [dim]  critical={result['critical']}, high={result['high']}[/dim]")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return result

    def _playwright_screenshot(self, url: str, output: str) -> str:
        """Prend un screenshot avec Playwright et retourne les logs console."""
        script = self.workdir / ".oryn" / "scripts" / "pw_check.py"
        if not script.exists():
            return ""

        try:
            proc = subprocess.run(
                ["python3", str(script), url, "--screenshot", output, "--dump-console"],
                capture_output=True, text=True, timeout=20,
            )
            return proc.stdout[-2000:]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""

    def _kill_all(self) -> None:
        """Kill tous les processus lancés."""
        for proc in self._procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        # Safety: kill pnpm dev et expo start
        for pattern in ["pnpm dev", "expo start", "metro"]:
            try:
                subprocess.run(
                    ["pkill", "-f", pattern],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass

        self._procs.clear()


class Evaluator:
    """Critique adversariale d'un sprint avec tests complets.

    Le harness lance les apps et capture les preuves AVANT d'appeler
    l'Evaluator Claude. L'Evaluator reçoit les screenshots, logs,
    et résultats comme FAITS, pas comme suggestions.
    """

    def __init__(self, config: HarnessConfig, state: StateManager):
        self.config = config
        self.state = state
        self.runner = ClaudeRunner(
            cwd=config.workdir,
            model=config.evaluator_model,
            system_prompt=EVALUATOR_PROMPT,
            permission_mode=config.permission_mode,
            timeout=config.cli_timeout,
            allowed_tools=None,
        )

    def evaluate(
        self,
        sprint: Sprint,
        iteration: int,
        generator_output: str,
    ) -> tuple[Verdict, RubricScore | None, ClaudeResult]:
        """Lance les apps, capture les preuves, puis évalue."""
        console.rule(f"[bold red]EVALUATOR — sprint {sprint.id} iter {iteration}")

        # Phase 1 : Le HARNESS lance les apps et capture les preuves
        console.print("[bold]Phase 1 : Lancement infra + apps + capture preuves[/bold]")
        infra, evidence = self._launch_and_capture(sprint)
        evidence_report = self._build_evidence_report(evidence)

        # Check si le projet compile (le Generator a déjà vérifié)
        gen_report_path = self.config.workdir / ".oryn" / "evidence" / f"gen_{sprint.id}_report.json"
        if gen_report_path.exists():
            try:
                gen_report = json.loads(gen_report_path.read_text())
                if not gen_report.get("build_ok", True):
                    console.print("[red]Le projet ne compile pas — NEEDS_FIX automatique[/red]")
                    build_errors = gen_report.get("build_errors", "")
                    # Écrire une critique automatique
                    critique = f"# Critique sprint {sprint.id} iter {iteration}\n\nLE PROJET NE COMPILE PAS.\n\nBuild errors:\n```\n{build_errors[:3000]}\n```\n\nFix les erreurs de compilation avant toute chose.\n"
                    self.state.write_critique(sprint.id, iteration, critique)
                    infra.stop_all()
                    return "NEEDS_FIX", None, ClaudeResult(
                        text=f"SPRINT_ID: {sprint.id}\nVERDICT: NEEDS_FIX\nSCORES_JSON: {{}}",
                        raw_stdout="", raw_stderr="Build failed",
                        cost_usd=0.0, duration_ms=0, num_turns=0,
                        session_id=None, success=False,
                    )
            except (json.JSONDecodeError, OSError):
                pass

        # Phase 2 : L'Evaluator Claude analyse les preuves
        console.print("[bold]Phase 2 : Analyse par l'Evaluator[/bold]")

        refs_dir = self.config.workdir / ".oryn" / "references"
        has_references = (refs_dir / "design_brief.md").exists()

        references_instruction = ""
        if has_references:
            references_instruction = (
                "- `.oryn/references/design_brief.md` — compare le résultat aux apps de référence\n"
                "- Les screenshots dans `.oryn/references/` — LIS-LES pour comparer visuellement"
            )

        # Screenshots capturés
        screenshots_instruction = ""
        screenshots = evidence.get("web", {}).get("screenshots", [])
        if screenshots:
            screenshots_instruction = "## Screenshots capturés par le harness\nLIS ces images :\n"
            for s in screenshots:
                screenshots_instruction += f"- `{s}`\n"

        stack = self.config.stack
        prompt = f"""Mode : ÉVALUATION.

Tu évalues le sprint **{sprint.id} — {sprint.title}**, itération {iteration}.

Lis dans l'ordre :
1. `.oryn/spec.md` (vision)
2. `.oryn/apps.json` (architecture multi-app)
3. `.oryn/contracts/sprint_{sprint.id}.md` (le contrat — c'est la référence)
{references_instruction}

{screenshots_instruction}

# Résumé du Generator
```
{generator_output[:3000]}
```

# PREUVES FACTUELLES (capturées par le harness, pas par toi)
Le harness a lancé les apps et capturé les résultats suivants.
Ce sont des FAITS. Utilise-les pour ton évaluation.

{evidence_report}

# Vérifications d'architecture OBLIGATOIRES
- [ ] packages/ui/ contient les composants de base (Box, Typography, Grid, etc.)
- [ ] ZÉRO <div>/<View>/<p>/<h1> dans apps/ ou packages/features/
- [ ] packages/features/ suit la structure feature-based
- [ ] Le grid fonctionne : {stack.web_grid_columns} colonnes web, {stack.mobile_grid_columns} colonnes mobile
- [ ] UpdateGate dans le root layout de CHAQUE app
- [ ] Blocks CMS dans BLOCK_REGISTRY

# Ta mission
1. LIS `.oryn/guides/coding-patterns.md` et `.oryn/guides/ui-ux-quality.md`
2. LIS les screenshots capturés (si disponibles)
3. Analyse les logs et erreurs ci-dessus
4. Vérifie CHAQUE critère du contrat
5. Vérifie l'architecture + design system compliance
6. **Vérifie la qualité du code** :
   - Compound components là où c'est pertinent ?
   - Error boundaries en place ?
   - Query key factories colocalisées ?
   - Optimistic updates pour les mutations ?
   - Pas de copie de query data dans du state local ?
7. **Vérifie l'UI/UX** :
   - Les 4 états (loading skeleton, data, empty+illustration, error+retry) ?
   - Micro-interactions (press feedback, loading sur boutons) ?
   - Touch targets >= 44px ?
   - Typographie cohérente (hiérarchie h1>h2>body>caption) ?
   - Icônes Lucide cohérentes (taille, stroke) ?
   - Illustrations undraw pour les empty states ?
8. **Design system violations** : regarde le score ESLint forbid-elements ci-dessus
9. Écris ta critique dans `.oryn/critiques/sprint_{sprint.id}_iter_{iteration:03d}.md`
10. Si l'app crash au lancement → NEEDS_FIX immédiat

# Output OBLIGATOIRE
```
SPRINT_ID: {sprint.id}
VERDICT: <PASS|NEEDS_FIX|REQUEST_RESTART>
SCORES_JSON: {{"design": 0-10, "originality": 0-10, "craft": 0-10, "functionality": 0-10, "tests": 0-10, "security": 0-10, "feedback": "résumé"}}
```
"""

        result = self.runner.run(prompt)
        self.state.write_trace(f"evaluator_{sprint.id}_iter{iteration}", result.raw_stdout)

        verdict, scores = self._parse_evaluator_output(result.text)

        if scores:
            console.print(
                f"[bold]Scores[/bold] design={scores.design} "
                f"originality={scores.originality} craft={scores.craft} "
                f"functionality={scores.functionality} "
                f"tests={scores.tests} security={scores.security} "
                f"→ [cyan]{scores.weighted_total:.2f}[/cyan]"
            )
        console.print(f"[bold]Verdict :[/bold] {verdict}")

        # Phase 3 : Cleanup infra + extraire les leçons
        infra.stop_all()
        extract_lessons_from_critique(result.text, sprint.id)

        return verdict, scores, result

    def _launch_and_capture(self, sprint: Sprint) -> tuple:
        """Lance l'infra sur le port configuré et capture les preuves."""
        infra = InfraManager(self.config.workdir)
        services = infra.start_all()
        evidence = self._collect_evidence(services, infra, sprint)
        return infra, evidence

    def _collect_evidence(self, services: dict, infra: InfraManager, sprint: Sprint) -> dict:
        """Collecte les preuves depuis les services lancés."""
        evidence: dict = {"web": {}, "mobile": {}, "tests": {}, "security": {}}

        # Lire le rapport du Generator (il a déjà vérifié + pris des screenshots)
        gen_report_path = self.config.workdir / ".oryn" / "evidence" / f"gen_{sprint.id}_report.json"
        gen_report: dict = {}
        if gen_report_path.exists():
            try:
                gen_report = json.loads(gen_report_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        # Screenshots du Generator (déjà pris sur les bonnes URLs)
        gen_screenshots = gen_report.get("screenshots", [])

        # Web
        web_svc = services.get("web")
        if web_svc:
            evidence["web"] = {
                "launched": web_svc.running,
                "healthy": web_svc.healthy,
                "http_status": "200" if web_svc.healthy else "error",
                "logs": web_svc.logs,
                "errors": web_svc.errors,
                "screenshots": [s["path"] for s in gen_screenshots if Path(s["path"]).exists()],
            }

            # Prendre des screenshots supplémentaires du Reviewer sur les mêmes URLs
            if web_svc.healthy:
                evidence_dir = self.config.workdir / ".oryn" / "evidence"
                evidence_dir.mkdir(parents=True, exist_ok=True)

                # URLs depuis le rapport du Generator
                urls_to_check = gen_report.get("urls_tested", [])
                if not urls_to_check:
                    # Fallback : URLs par défaut + sprint-aware
                    urls_to_check = [{"name": "home", "url": get_web_base_url(self.config.workdir)}]

                browser_console: dict[str, str] = {}
                for url_info in urls_to_check:
                    name = url_info.get("name", "page")
                    url = url_info.get("url", get_web_base_url(self.config.workdir))
                    path = evidence_dir / f"eval_{sprint.id}_{name}.png"
                    console_output = self._playwright_screenshot_with_console(url, str(path))
                    if path.exists():
                        evidence["web"]["screenshots"].append(str(path))
                    if console_output:
                        browser_console[name] = console_output
                evidence["web"]["browser_console"] = browser_console

        # Server logs dans l'evidence web
        if web_svc:
            evidence["web"]["server_logs"] = web_svc.logs[-2000:]
            evidence["web"]["server_errors"] = web_svc.errors[:10]

        # Mobile
        android_svc = services.get("android")
        expo_svc = services.get("expo")
        if android_svc or expo_svc:
            evidence["mobile"] = {
                "emulator": android_svc.running if android_svc else False,
                "launched": expo_svc.running if expo_svc else False,
                "healthy": expo_svc.healthy if expo_svc else False,
                "logs": expo_svc.logs if expo_svc else "",
                "errors": (android_svc.errors if android_svc else []) + (expo_svc.errors if expo_svc else []),
            }
            # Logcat Android
            if android_svc and android_svc.running:
                try:
                    proc = subprocess.run(
                        ["adb", "logcat", "-d", "-s", "ReactNativeJS:*", "ReactNative:*"],
                        capture_output=True, text=True, timeout=10,
                    )
                    evidence["mobile"]["logcat"] = proc.stdout[-2000:]
                except Exception:
                    pass

        # Convex
        convex_svc = services.get("convex")
        if convex_svc:
            evidence["convex"] = {
                "running": convex_svc.running,
                "healthy": convex_svc.healthy,
                "errors": convex_svc.errors,
            }

        # Unit tests
        evidence["tests"]["unit"] = self._run_unit_tests()

        # Security
        evidence["security"]["npm_audit"] = self._run_npm_audit()

        # Code quality
        evidence["quality"] = self._run_quality_checks()

        # Accessibility (axe-core via Playwright if web is healthy)
        if evidence.get("web", {}).get("healthy"):
            evidence["accessibility"] = self._run_axe_audit()

        return evidence

    def _run_unit_tests(self) -> dict:
        result: dict = {"output": "", "pass_count": 0, "fail_count": 0, "ran": False}
        console.print("[blue]  Unit tests...[/blue]")
        try:
            import subprocess
            proc = subprocess.run(
                ["pnpm", "turbo", "test", "--", "--reporter=verbose"],
                cwd=str(self.config.workdir),
                capture_output=True, text=True, timeout=120,
            )
            result["output"] = proc.stdout[-3000:] + "\n" + proc.stderr[-1000:]
            result["ran"] = True
            for line in proc.stdout.split("\n"):
                if "✓" in line or "pass" in line.lower():
                    result["pass_count"] += 1
                if "✗" in line or "fail" in line.lower():
                    result["fail_count"] += 1
            console.print(f"  [dim]  {result['pass_count']} pass, {result['fail_count']} fail[/dim]")
        except Exception as e:
            result["output"] = str(e)
        return result

    def _run_npm_audit(self) -> dict:
        result: dict = {"output": "", "critical": 0, "high": 0}
        try:
            import subprocess
            proc = subprocess.run(
                ["pnpm", "audit", "--json"],
                cwd=str(self.config.workdir),
                capture_output=True, text=True, timeout=30,
            )
            result["output"] = proc.stdout[-2000:]
            try:
                audit = json.loads(proc.stdout)
                vuln = audit.get("metadata", {}).get("vulnerabilities", {})
                result["critical"] = vuln.get("critical", 0)
                result["high"] = vuln.get("high", 0)
            except (json.JSONDecodeError, KeyError):
                pass
        except Exception:
            pass
        return result

    def _playwright_screenshot_with_console(self, url: str, output: str) -> str:
        """Screenshot + capture console logs du browser. Retourne les logs."""
        script = self.config.workdir / ".oryn" / "scripts" / "pw_check.py"
        if not script.exists():
            return ""
        try:
            proc = subprocess.run(
                ["python3", str(script), url, "--screenshot", output, "--dump-console"],
                capture_output=True, text=True, timeout=20,
            )
            return proc.stdout[-2000:] if proc.stdout else ""
        except Exception:
            return ""

    def _run_quality_checks(self) -> dict:
        """Lance Biome + ESLint + Knip pour la qualité du code."""
        result: dict = {"biome": "", "eslint": "", "knip": "", "errors": 0, "warnings": 0}
        console.print("[blue]  Code quality checks...[/blue]")

        # Biome
        try:
            proc = subprocess.run(
                ["npx", "biome", "check", "--reporter=summary", "."],
                cwd=str(self.config.workdir),
                capture_output=True, text=True, timeout=60,
            )
            result["biome"] = proc.stdout[-2000:]
            result["errors"] += proc.stdout.lower().count("error")
            result["warnings"] += proc.stdout.lower().count("warn")
            console.print(f"  [dim]  Biome: done[/dim]")
        except Exception:
            pass

        # ESLint (design system enforcement)
        try:
            proc = subprocess.run(
                ["npx", "eslint", "--format=compact", "packages/", "apps/"],
                cwd=str(self.config.workdir),
                capture_output=True, text=True, timeout=60,
            )
            result["eslint"] = proc.stdout[-2000:]
            # Count forbidden elements (design system violations)
            ds_violations = proc.stdout.count("forbid-elements")
            result["design_system_violations"] = ds_violations
            if ds_violations > 0:
                console.print(f"  [yellow]  ESLint: {ds_violations} design system violations (<div>, <p>, etc.)[/yellow]")
            else:
                console.print(f"  [green]  ESLint: 0 design system violations[/green]")
        except Exception:
            pass

        # Knip (dead code)
        try:
            proc = subprocess.run(
                ["npx", "knip", "--no-progress"],
                cwd=str(self.config.workdir),
                capture_output=True, text=True, timeout=60,
            )
            result["knip"] = proc.stdout[-1500:]
            unused = proc.stdout.count("unused")
            result["unused_exports"] = unused
            console.print(f"  [dim]  Knip: {unused} unused items[/dim]")
        except Exception:
            pass

        return result

    def _run_axe_audit(self) -> dict:
        """Lance un audit accessibilité avec axe-core via Playwright."""
        result: dict = {"violations": 0, "passes": 0, "details": ""}
        console.print("[blue]  Accessibility audit (axe-core)...[/blue]")

        # Script Python inline pour axe-core
        axe_script = """
import json, sys
from playwright.sync_api import sync_playwright

url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"  # fallback, real URL passed as arg

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    try:
        page.goto(url, wait_until="networkidle", timeout=15000)
    except Exception:
        page.goto(url, timeout=15000)

    # Inject axe-core
    axe_js = page.evaluate('''async () => {
        const script = document.createElement("script");
        script.src = "https://cdn.jsdelivr.net/npm/axe-core@latest/axe.min.js";
        document.head.appendChild(script);
        await new Promise(r => script.onload = r);
        const results = await axe.run();
        return {
            violations: results.violations.length,
            passes: results.passes.length,
            details: results.violations.map(v => ({
                id: v.id,
                impact: v.impact,
                description: v.description,
                nodes: v.nodes.length,
            }))
        };
    }''')
    print(json.dumps(axe_js))
    browser.close()
"""
        try:
            proc = subprocess.run(
                ["python3", "-c", axe_script, get_web_base_url(self.config.workdir)],
                capture_output=True, text=True, timeout=30,
            )
            if proc.stdout.strip():
                data = json.loads(proc.stdout.strip())
                result["violations"] = data.get("violations", 0)
                result["passes"] = data.get("passes", 0)
                result["details"] = json.dumps(data.get("details", [])[:10], indent=2)
                console.print(
                    f"  [{'red' if result['violations'] > 0 else 'green'}]"
                    f"  a11y: {result['violations']} violations, {result['passes']} passes[/]"
                )
        except Exception as e:
            result["details"] = str(e)

        return result

    def _build_evidence_report(self, evidence: dict) -> str:
        """Construit un rapport texte des preuves pour l'Evaluator."""
        lines: list[str] = []

        # Web
        web = evidence.get("web", {})
        if web:
            lines.append("## App Web")
            lines.append(f"- Lancée : {'OUI' if web.get('launched') else 'NON'}")
            lines.append(f"- HTTP status : {web.get('http_status', 'N/A')}")

            if web.get("server_errors"):
                lines.append("- ERREURS SERVEUR :")
                for e in web["server_errors"][:10]:
                    lines.append(f"  - {e[:200]}")

            if web.get("server_logs"):
                lines.append("- Logs serveur :")
                lines.append(f"```\n{web['server_logs'][-1500:]}\n```")

            # Console browser par page
            browser_console = web.get("browser_console", {})
            if browser_console:
                lines.append("- Console browser (erreurs JS capturées par page) :")
                for page, logs in browser_console.items():
                    error_lines = [l for l in logs.split("\n") if "[error]" in l.lower() or "uncaught" in l.lower() or "failed" in l.lower()]
                    if error_lines:
                        lines.append(f"  **{page}** :")
                        for el in error_lines[:5]:
                            lines.append(f"    - {el.strip()[:200]}")
                if not any(
                    "[error]" in l.lower() or "uncaught" in l.lower()
                    for logs in browser_console.values()
                    for l in logs.split("\n")
                ):
                    lines.append("  (aucune erreur JS détectée)")

        # Mobile
        mobile = evidence.get("mobile", {})
        if mobile:
            lines.append("\n## App Mobile")
            lines.append(f"- Émulateur : {'OUI' if mobile.get('emulator') else 'NON'}")
            lines.append(f"- Lancée : {'OUI' if mobile.get('launched') else 'NON'}")

            if mobile.get("errors"):
                lines.append("- ERREURS :")
                for e in mobile["errors"][:10]:
                    lines.append(f"  - {e[:200]}")

            if mobile.get("logcat"):
                logcat_errors = [l for l in mobile["logcat"].split("\n") if "error" in l.lower() or "fatal" in l.lower()]
                if logcat_errors:
                    lines.append("- Erreurs React Native (logcat) :")
                    for l in logcat_errors[:10]:
                        lines.append(f"  - {l.strip()[:200]}")
                else:
                    lines.append("- Logcat : aucune erreur JS détectée")

            if mobile.get("logs"):
                lines.append("- Logs Expo :")
                lines.append(f"```\n{mobile['logs'][-1500:]}\n```")

            if mobile.get("logcat"):
                lines.append("- Logcat Android :")
                lines.append(f"```\n{mobile['logcat'][-1000:]}\n```")

        # Tests
        tests = evidence.get("tests", {})
        unit = tests.get("unit", {})
        if unit.get("ran"):
            lines.append("\n## Unit Tests")
            lines.append(f"- Pass : {unit.get('pass_count', 0)}")
            lines.append(f"- Fail : {unit.get('fail_count', 0)}")
            if unit.get("output"):
                lines.append(f"```\n{unit['output'][-2000:]}\n```")

        # Security
        sec = evidence.get("security", {})
        audit = sec.get("npm_audit", {})
        if audit:
            lines.append("\n## Sécurité")
            lines.append(f"- npm audit critical : {audit.get('critical', '?')}")
            lines.append(f"- npm audit high : {audit.get('high', '?')}")

        # Code quality
        quality = evidence.get("quality", {})
        if quality:
            lines.append("\n## Code Quality")
            ds_v = quality.get("design_system_violations", 0)
            lines.append(f"- Design system violations (<div>, <p>, etc.) : {ds_v}")
            lines.append(f"- Biome errors : {quality.get('errors', '?')}")
            lines.append(f"- Biome warnings : {quality.get('warnings', '?')}")
            lines.append(f"- Unused exports (Knip) : {quality.get('unused_exports', '?')}")
            if quality.get("eslint"):
                lines.append(f"- ESLint output :\n```\n{quality['eslint'][-1000:]}\n```")

        # Accessibility
        a11y = evidence.get("accessibility", {})
        if a11y:
            lines.append("\n## Accessibilité (axe-core)")
            lines.append(f"- Violations : {a11y.get('violations', '?')}")
            lines.append(f"- Passes : {a11y.get('passes', '?')}")
            if a11y.get("details"):
                lines.append(f"- Détails :\n```\n{a11y['details'][:1500]}\n```")

        # Convex
        convex = evidence.get("convex", {})
        if convex:
            lines.append("\n## Backend (Convex)")
            lines.append(f"- Running : {'OUI' if convex.get('running') else 'NON'}")
            lines.append(f"- Healthy : {'OUI' if convex.get('healthy') else 'NON'}")
            if convex.get("errors"):
                for e in convex["errors"][:5]:
                    lines.append(f"  - {e[:200]}")

        if not lines:
            return "Aucune preuve capturée."

        return "\n".join(lines)

    def _parse_evaluator_output(self, text: str) -> tuple[Verdict, RubricScore | None]:
        """Parse les lignes VERDICT et SCORES_JSON de la sortie."""
        verdict: Verdict = "NEEDS_FIX"
        scores: RubricScore | None = None

        verdict_match = re.search(r"VERDICT:\s*(PASS|NEEDS_FIX|REQUEST_RESTART)", text)
        if verdict_match:
            verdict = verdict_match.group(1)  # type: ignore

        scores_match = re.search(r"SCORES_JSON:\s*(\{.*?\})", text, re.DOTALL)
        if scores_match:
            try:
                raw = json.loads(scores_match.group(1))
                normalized = {
                    "design": float(raw.get("design", 0)) / 10.0,
                    "originality": float(raw.get("originality", 0)) / 10.0,
                    "craft": float(raw.get("craft", 0)) / 10.0,
                    "functionality": float(raw.get("functionality", 0)) / 10.0,
                    "tests": float(raw.get("tests", 0)) / 10.0,
                    "security": float(raw.get("security", 0)) / 10.0,
                    "feedback": raw.get("feedback", ""),
                }
                scores = RubricScore.from_dict(normalized, self.config.rubric_weights)
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                console.print(f"[yellow]⚠ SCORES_JSON parse fail : {e}[/yellow]")

        return verdict, scores
