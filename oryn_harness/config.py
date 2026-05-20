"""Configuration centralisée du harness."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StackConfig:
    """Stack technique imposée par le harness."""

    # Web
    web_framework: str = "tanstack-start"
    web_meta_framework: str = "vinxi"  # TanStack Start est basé sur Vinxi

    # Mobile
    mobile_framework: str = "expo"
    mobile_router: str = "expo-router"

    # UI universelle
    ui_library: str = "gluestack-ui"
    styling: str = "nativewind"  # Tailwind CSS via NativeWind
    css_framework: str = "tailwindcss"

    # CMS / Backend
    cms: str = "vex"  # VexCMS sur Convex
    database: str = "convex"
    auth: str = "better-auth"

    # Component library (repo GitHub séparé, cloné dans le monorepo)
    # Le harness crée/met à jour ce repo. Tous les composants UI vivent ici.
    # Aucun <div>, <View>, <p>, <h1> dans le code app — tout passe par la lib.
    component_library_repo: str = "Messanga11/oryn-ui"
    component_library_package_name: str = "@oryn/ui"
    # Repos existants à intégrer comme patterns dans la lib
    form_builder_repo: str = "Messanga11/formbuilder"
    table_page_repo: str = "Messanga11/table-page"

    # Grid
    web_grid_columns: int = 12
    mobile_grid_columns: int = 6

    # Monorepo
    monorepo_tool: str = "turborepo"
    package_manager: str = "pnpm"


@dataclass
class TestConfig:
    """Configuration de la pipeline de tests."""

    # Unit tests
    unit_test_runner: str = "vitest"

    # E2E web
    e2e_web_runner: str = "playwright"

    # E2E mobile
    e2e_mobile_runner: str = "maestro"

    # Performance web
    lighthouse_enabled: bool = True
    lighthouse_perf_threshold: int = 80
    lighthouse_a11y_threshold: int = 90
    lighthouse_seo_threshold: int = 80
    lighthouse_bp_threshold: int = 80

    # Load / stress
    load_test_runner: str = "k6"
    load_test_vus: int = 50  # virtual users
    load_test_duration: str = "30s"

    # Sécurité
    security_scanner: str = "zap"  # OWASP ZAP
    npm_audit_enabled: bool = True

    # Mobile testing
    android_emulator: bool = True
    ios_simulator: bool = True

    # Seuils
    max_critical_vulnerabilities: int = 0
    max_high_vulnerabilities: int = 0


@dataclass
class AppDefinition:
    """Définition d'une app dans le monorepo.

    Exemple pour un projet type Uber :
    - AppDefinition(name="client-app", platform="mobile", description="App client pour commander")
    - AppDefinition(name="driver-app", platform="mobile", description="App livreur")
    - AppDefinition(name="admin-web", platform="web", description="Dashboard admin")
    """

    name: str
    platform: str  # "web" | "mobile" | "both"
    description: str
    route_prefix: str = ""  # Pour le web, ex: "/admin"


@dataclass
class HarnessConfig:
    """Toute la config du run dans un seul objet."""

    # Le prompt utilisateur initial
    user_prompt: str

    # Répertoire de travail (où l'app sera construite)
    workdir: Path

    # Modèle Claude utilisé pour chaque rôle
    planner_model: str = "claude-opus-4-7"
    generator_model: str = "claude-sonnet-4-6"
    evaluator_model: str = "claude-opus-4-7"
    researcher_model: str = "claude-sonnet-4-6"

    # Stack technique
    stack: StackConfig = field(default_factory=StackConfig)

    # Tests
    tests: TestConfig = field(default_factory=TestConfig)

    # Apps explicites (si vide, le Planner décide)
    apps: list[AppDefinition] = field(default_factory=list)

    # Limites
    max_iterations_per_sprint: int = 10
    max_total_iterations: int = 500
    budget_usd: float = 500.0

    # Comportement
    enable_full_restart: bool = True
    restart_threshold: int = 5  # 5 échecs consécutifs avant restart (pas 3)
    max_restarts: int = 3  # 3 restarts avant fail définitif (pas 2)

    # Design research
    enable_design_research: bool = True

    # Permission mode pour claude code CLI
    permission_mode: str = "dangerous"

    # Timeout par appel CLI (secondes)
    cli_timeout: int = 1800  # 30 min par tour

    # Rubric weights (somme = 1.0)
    rubric_weights: dict[str, float] = field(
        default_factory=lambda: {
            "design": 0.25,
            "originality": 0.15,
            "craft": 0.15,
            "functionality": 0.20,
            "tests": 0.15,
            "security": 0.10,
        }
    )

    # Seuil minimal pour qu'un sprint soit considéré PASS
    pass_threshold: float = 0.75

    def state_dir(self) -> Path:
        return self.workdir / ".oryn"

    def traces_dir(self) -> Path:
        return self.state_dir() / "traces"

    def __post_init__(self):
        self.workdir = Path(self.workdir).resolve()
        weight_sum = sum(self.rubric_weights.values())
        if abs(weight_sum - 1.0) > 0.01:
            raise ValueError(f"rubric_weights doit sommer à 1.0, actuel : {weight_sum}")
