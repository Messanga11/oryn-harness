"""Mémoire persistante des erreurs et leçons apprises.

Stocke les erreurs rencontrées et leurs fixes dans ~/.oryn/lessons.json.
Le Generator lit ce fichier AVANT de coder pour ne pas refaire les mêmes erreurs.
Chaque projet enrichit la base de connaissances globale.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from rich.console import Console

console = Console()

LESSONS_DIR = Path.home() / ".oryn"
LESSONS_FILE = LESSONS_DIR / "lessons.json"


def _load_lessons() -> list[dict]:
    """Charge les leçons depuis le fichier global."""
    if not LESSONS_FILE.exists():
        return []
    try:
        return json.loads(LESSONS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_lessons(lessons: list[dict]) -> None:
    """Sauvegarde les leçons."""
    LESSONS_DIR.mkdir(parents=True, exist_ok=True)
    LESSONS_FILE.write_text(json.dumps(lessons, indent=2, ensure_ascii=False))


def add_lesson(
    category: str,
    error: str,
    fix: str,
    context: str = "",
    tech: str = "",
) -> None:
    """Ajoute une leçon apprise.

    Args:
        category: ex: "expo", "tanstack-start", "convex", "nativewind", "turborepo"
        error: Le message d'erreur exact ou le problème rencontré
        fix: La solution qui a fonctionné
        context: Contexte supplémentaire (fichier, sprint, etc.)
        tech: Technologies impliquées
    """
    lessons = _load_lessons()

    # Éviter les doublons exacts
    for existing in lessons:
        if existing.get("error") == error:
            return

    lessons.append({
        "category": category,
        "error": error,
        "fix": fix,
        "context": context,
        "tech": tech,
        "date": datetime.now().isoformat(),
    })

    # Garder les 500 dernières leçons max
    if len(lessons) > 500:
        lessons = lessons[-500:]

    _save_lessons(lessons)


def get_lessons_for_prompt(categories: list[str] | None = None, limit: int = 30) -> str:
    """Retourne les leçons formatées pour injection dans un prompt.

    Args:
        categories: Filtrer par catégories (ex: ["expo", "nativewind"])
        limit: Max de leçons à retourner
    """
    lessons = _load_lessons()

    if categories:
        lessons = [l for l in lessons if l.get("category") in categories]

    # Les plus récentes en premier
    lessons = lessons[-limit:]

    if not lessons:
        return ""

    lines = ["# Leçons apprises (erreurs passées → ne PAS les refaire)"]
    lines.append(f"({len(lessons)} leçons chargées depuis ~/.oryn/lessons.json)\n")

    for l in lessons:
        lines.append(f"## [{l.get('category', '?')}] {l.get('error', '')[:100]}")
        lines.append(f"**Fix:** {l.get('fix', '')}")
        if l.get("tech"):
            lines.append(f"**Tech:** {l['tech']}")
        lines.append("")

    return "\n".join(lines)


def extract_lessons_from_critique(critique_text: str, sprint_id: str) -> None:
    """Parse une critique de l'Evaluator et extrait les leçons.

    L'Evaluator mentionne des erreurs — on les capture pour la prochaine fois.
    """
    # Chercher les patterns d'erreur courants
    error_patterns = [
        ("nativewind", "nativewind", ["className not working", "tailwind not applied", "style not rendering"]),
        ("expo", "expo", ["metro bundler", "expo start failed", "module not found"]),
        ("tanstack-start", "tanstack-start", ["route not found", "hydration", "ssr error", "vinxi"]),
        ("convex", "convex", ["convex", "schema", "mutation", "query failed"]),
        ("turborepo", "turborepo", ["workspace", "pnpm", "package not found", "cannot resolve"]),
        ("react-native", "react-native", ["red screen", "invariant violation", "cannot read property"]),
        ("typescript", "typescript", ["type error", "ts2", "cannot find module"]),
        ("architecture", "architecture", ["div>", "<View>", "inline style", "not in packages/ui"]),
    ]

    lower = critique_text.lower()

    for category, tech, keywords in error_patterns:
        for kw in keywords:
            if kw in lower:
                # Trouver la ligne qui contient l'erreur
                for line in critique_text.split("\n"):
                    if kw in line.lower() and ("fail" in line.lower() or "error" in line.lower() or "✗" in line):
                        add_lesson(
                            category=category,
                            error=line.strip()[:200],
                            fix=f"See critique for sprint {sprint_id}",
                            context=sprint_id,
                            tech=tech,
                        )
                        break
