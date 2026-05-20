"""Agent EVALUATOR : critique le travail du Generator avec tests complets."""
from __future__ import annotations

import json
import re
from typing import Literal

from rich.console import Console

from .claude_runner import ClaudeResult, ClaudeRunner
from .config import HarnessConfig
from .prompts import EVALUATOR_PROMPT
from .state import RubricScore, Sprint, StateManager

console = Console()

Verdict = Literal["PASS", "NEEDS_FIX", "REQUEST_RESTART"]


class Evaluator:
    """Critique adversariale d'un sprint avec tests complets."""

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
        """Évalue le travail du Generator et retourne verdict + scores."""
        console.rule(f"[bold red]EVALUATOR — sprint {sprint.id} iter {iteration}")

        # Check si des références design existent
        refs_dir = self.config.workdir / ".oryn" / "references"
        has_references = (refs_dir / "design_brief.md").exists()

        references_instruction = ""
        if has_references:
            references_instruction = (
                "4. `.oryn/references/design_brief.md` — compare le résultat aux apps de référence\n"
                "5. Les screenshots dans `.oryn/references/` — LIS-LES pour comparer visuellement"
            )

        # Info sur les apps
        apps = self.state.read_apps()
        apps_info = ""
        if apps:
            apps_lines = [f"  - {a.name} ({a.platform})" for a in apps]
            apps_info = f"Ce projet a {len(apps)} apps : {', '.join(a.name for a in apps)}"

        # Test config
        test_cfg = self.config.tests
        test_instructions = f"""
# Tests à exécuter
1. `pnpm turbo test` (Vitest unit tests)
2. `pnpm turbo test:e2e --filter=web` (Playwright E2E, si app web)
3. `.oryn/scripts/maestro_helper.sh android` (Maestro mobile E2E, si app mobile)
4. `.oryn/scripts/lighthouse_helper.sh http://localhost:3000` (Lighthouse, si app web)
   - Seuils : Perf ≥ {test_cfg.lighthouse_perf_threshold}, A11y ≥ {test_cfg.lighthouse_a11y_threshold}
5. `.oryn/scripts/security_scan.sh http://localhost:3000` (Sécurité)
   - 0 critical, 0 high vulns
"""

        stack = self.config.stack
        arch_check = f"""
# Vérifications d'architecture OBLIGATOIRES
- [ ] packages/ui/ contient les composants de base (Box, Typography, Grid, Row, Column, Button, etc.)
- [ ] ZÉRO <div>/<View>/<p>/<h1> dans apps/ ou packages/features/ — tout via @oryn/ui
- [ ] packages/features/ suit la structure feature-based (components/ + hooks/ + services/)
- [ ] Les routes web importent depuis @repo/features (pas de logique dans apps/web/)
- [ ] Les routes mobile importent depuis @repo/features (mêmes composants que web)
- [ ] Le grid fonctionne : {stack.web_grid_columns} colonnes web, {stack.mobile_grid_columns} colonnes mobile
- [ ] NativeWind est utilisé pour le styling (pas de StyleSheet.create inline)
- [ ] Les tests sont co-localisés (.test.ts dans le même dossier)
- [ ] UpdateGate wraps le root layout de CHAQUE app (force update comme WhatsApp)
- [ ] Table appVersions existe dans le schema Convex
- [ ] Blocks CMS enregistrés dans BLOCK_REGISTRY + BlockRenderer fonctionnel
"""

        prompt = f"""Mode : ÉVALUATION.

Tu évalues le sprint **{sprint.id} — {sprint.title}**, itération {iteration}.
{apps_info}

Lis dans l'ordre :
1. `.oryn/spec.md` (vision)
2. `.oryn/apps.json` (architecture multi-app)
3. `.oryn/contracts/sprint_{sprint.id}.md` (le contrat — c'est la référence)
{references_instruction}

# Résumé du Generator
```
{generator_output[:3000]}
```

{test_instructions}

{arch_check}

# Ta mission — OBLIGATOIRE, dans cet ordre

## Étape 1 : LANCER les apps (OBLIGATOIRE)
Tu DOIS lancer les apps et vérifier qu'elles tournent. Pas de review sans lancer.

### App web (TanStack Start)
```bash
# Lancer avec logs redirigés pour analyse
cd apps/web && pnpm dev > /tmp/eval_web_server.log 2>&1 &
WEB_PID=$!
sleep 5

# Vérifier que le serveur répond
HTTP_CODE=$(curl -s -o /dev/null -w "%{{http_code}}" http://localhost:3000)
echo "Web server HTTP: $HTTP_CODE"

# Capturer les logs serveur
cat /tmp/eval_web_server.log

# Screenshot de la home
python .oryn/scripts/pw_check.py http://localhost:3000 --screenshot /tmp/eval_web_home.png --dump-console --dump-html > /tmp/eval_web_console.log 2>&1

# Lire les logs console du navigateur (erreurs JS, warnings, network errors)
cat /tmp/eval_web_console.log
```

### App mobile (Expo) — lancer dans l'émulateur
```bash
# Android : démarrer l'émulateur si pas déjà running
if ! adb devices 2>/dev/null | grep -q "emulator"; then
    AVDS=$(emulator -list-avds 2>/dev/null | head -1)
    if [ -n "$AVDS" ]; then
        emulator @"$AVDS" -no-window -no-audio &
        sleep 15
        adb wait-for-device
    fi
fi

# Lancer l'app Expo avec logs
cd apps/mobile && npx expo start --android > /tmp/eval_mobile_expo.log 2>&1 &
MOBILE_PID=$!
sleep 15

# Lire les logs Expo (build errors, runtime errors, warnings)
cat /tmp/eval_mobile_expo.log

# Capturer les logs Android (logcat) pour voir les crashes/erreurs JS
adb logcat -d -s ReactNativeJS:* ReactNative:* > /tmp/eval_mobile_logcat.log 2>/dev/null
cat /tmp/eval_mobile_logcat.log | tail -50

# iOS alternative :
# xcrun simctl boot "iPhone 16 Pro" 2>/dev/null
# cd apps/mobile && npx expo start --ios > /tmp/eval_mobile_expo.log 2>&1 &
# sleep 15 && cat /tmp/eval_mobile_expo.log
```
Tu DOIS lancer au moins UNE des deux plateformes (Android ou iOS).
Si l'émulateur n'est pas dispo, lance au moins `npx expo start` et vérifie les logs.

## Étape 2 : TESTER visuellement + analyser les logs
- Screenshot chaque écran important avec Playwright (web) ou l'émulateur (mobile)
- Clique sur les boutons, remplis les formulaires, vérifie que ça fonctionne
- Vérifie le responsive (mobile viewport sur web)
- **LIRE LES LOGS** : tu DOIS analyser les logs pour trouver :
  - Erreurs JS console (web) : `cat /tmp/eval_web_console.log`
  - Erreurs serveur (web) : `cat /tmp/eval_web_server.log`
  - Erreurs React Native (mobile) : `cat /tmp/eval_mobile_logcat.log`
  - Erreurs Expo build (mobile) : `cat /tmp/eval_mobile_expo.log`
  - Si tu trouves des erreurs dans les logs, cite-les EXACTEMENT dans ta critique
  - Pas d'erreur dans les logs = bon signe, mentionne-le aussi

## Étape 3 : Tests automatisés
- `pnpm turbo test` (unit tests)
- `pnpm turbo test:e2e --filter=web` (Playwright E2E si configuré)
- `.oryn/scripts/maestro_helper.sh android` (Maestro si flows existent)
- `.oryn/scripts/lighthouse_helper.sh http://localhost:3000` (Lighthouse)
- `npm audit` (sécurité)

## Étape 4 : Vérifier le contrat
- Vérifie CHAQUE critère du contrat un par un
- Vérifie l'architecture (packages/ui/, packages/features/, imports)

## Étape 5 : Écrire la critique
- Écris dans `.oryn/critiques/sprint_{sprint.id}_iter_{iteration:03d}.md`

## Étape 6 : Kill les serveurs
```bash
# Nettoyer les processus lancés
pkill -f "pnpm dev" 2>/dev/null || true
pkill -f "expo start" 2>/dev/null || true
```

# RAPPEL : tu es adversarial
{"- COMPARE le résultat aux screenshots de référence. Le design doit être AU NIVEAU." if has_references else ""}
- Bouton sans action → FAIL
- API retourne 200 mais payload vide → FAIL
- Design générique Inter/ombre douce → originality ≤ 3
- Composant dans apps/ au lieu de packages/ui/ → ARCH FAIL
- Feature pas dans packages/features/ → ARCH FAIL
- Tests manquants → tests ≤ 3
- npm audit avec critical/high → security ≤ 3

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

        return verdict, scores, result

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
