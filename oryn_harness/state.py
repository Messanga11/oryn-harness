"""State management sur le file system.

Principe (du talk) : on utilise le FS comme état partagé entre agents
plutôt que de tout pousser dans les context windows. Les modèles ont
moins tendance à écraser des .json que des .md, donc on stocke en JSON.
"""
from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class SprintStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    RESTARTED = "restarted"


class Sprint(BaseModel):
    """Un sprint = une unité de travail livrable par le générateur."""

    id: str
    title: str
    description: str
    status: SprintStatus = SprintStatus.PENDING
    iterations: int = 0
    last_score: float | None = None
    restart_count: int = 0
    notes: list[str] = Field(default_factory=list)
    target_apps: list[str] = Field(default_factory=lambda: ["shared"])


class RubricScore(BaseModel):
    """Score d'un sprint sur les 6 critères."""

    design: float
    originality: float
    craft: float
    functionality: float
    tests: float = 0.0
    security: float = 0.0
    weighted_total: float
    feedback: str

    @classmethod
    def from_dict(cls, data: dict, weights: dict[str, float]) -> "RubricScore":
        total = sum(data.get(k, 0.0) * weights.get(k, 0.0) for k in weights)
        return cls(
            design=data.get("design", 0.0),
            originality=data.get("originality", 0.0),
            craft=data.get("craft", 0.0),
            functionality=data.get("functionality", 0.0),
            tests=data.get("tests", 0.0),
            security=data.get("security", 0.0),
            weighted_total=total,
            feedback=data.get("feedback", ""),
        )


class AppInfo(BaseModel):
    """Définition d'une app dans le monorepo."""

    name: str
    platform: str  # "web" | "mobile" | "both"
    description: str
    route_prefix: str = ""


class TestResults(BaseModel):
    """Résultats agrégés des tests pour un sprint."""

    unit_pass: int = 0
    unit_fail: int = 0
    e2e_web_pass: int = 0
    e2e_web_fail: int = 0
    e2e_mobile_pass: int = 0
    e2e_mobile_fail: int = 0
    lighthouse_perf: int | None = None
    lighthouse_a11y: int | None = None
    lighthouse_seo: int | None = None
    lighthouse_bp: int | None = None
    npm_audit_critical: int = 0
    npm_audit_high: int = 0
    k6_p95_ms: float | None = None
    k6_error_rate: float | None = None


class ProgressState(BaseModel):
    """État global du run, persisté dans progress.json."""

    started_at: datetime
    user_prompt: str
    sprints: list[Sprint]
    apps: list[AppInfo] = Field(default_factory=list)
    current_sprint_id: str | None = None
    total_cost_usd: float = 0.0
    total_iterations: int = 0
    completed: bool = False
    test_results: dict[str, TestResults] = Field(default_factory=dict)


class StateManager:
    """Gère la persistence FS de tout l'état du harness.

    Layout :
        workdir/.oryn/
        ├── feature_list.json
        ├── apps.json
        ├── progress.json
        ├── spec.md
        ├── references/
        │   ├── references.json
        │   ├── design_brief.md
        │   └── *.png
        ├── contracts/
        │   └── sprint_<id>.md
        ├── critiques/
        │   └── sprint_<id>_iter_<n>.md
        ├── scripts/
        │   ├── pw_check.py
        │   ├── pw_screenshot.py
        │   └── maestro_helper.sh
        └── traces/
            └── <agent>_<timestamp>.log
    """

    def __init__(self, workdir: Path):
        self.workdir = Path(workdir).resolve()
        self.state_dir = self.workdir / ".oryn"
        self.contracts_dir = self.state_dir / "contracts"
        self.critiques_dir = self.state_dir / "critiques"
        self.traces_dir = self.state_dir / "traces"
        self.references_dir = self.state_dir / "references"
        self.scripts_dir = self.state_dir / "scripts"

    def init(self) -> None:
        """Crée tous les répertoires."""
        for d in (
            self.state_dir,
            self.contracts_dir,
            self.critiques_dir,
            self.traces_dir,
            self.references_dir,
            self.scripts_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    # ---------- feature_list.json ----------

    def feature_list_path(self) -> Path:
        return self.state_dir / "feature_list.json"

    def write_feature_list(self, sprints: list[Sprint]) -> None:
        data = [s.model_dump(mode="json") for s in sprints]
        self.feature_list_path().write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def read_feature_list(self) -> list[Sprint]:
        if not self.feature_list_path().exists():
            return []
        data = json.loads(self.feature_list_path().read_text())
        return [Sprint(**s) for s in data]

    # ---------- apps.json ----------

    def apps_path(self) -> Path:
        return self.state_dir / "apps.json"

    def write_apps(self, apps: list[AppInfo]) -> None:
        data = [a.model_dump(mode="json") for a in apps]
        self.apps_path().write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def read_apps(self) -> list[AppInfo]:
        if not self.apps_path().exists():
            return []
        data = json.loads(self.apps_path().read_text())
        return [AppInfo(**a) for a in data]

    # ---------- progress.json ----------

    def progress_path(self) -> Path:
        return self.state_dir / "progress.json"

    def write_progress(self, progress: ProgressState) -> None:
        self.progress_path().write_text(
            progress.model_dump_json(indent=2)
        )

    def read_progress(self) -> ProgressState | None:
        if not self.progress_path().exists():
            return None
        return ProgressState.model_validate_json(self.progress_path().read_text())

    # ---------- spec.md ----------

    def spec_path(self) -> Path:
        return self.state_dir / "spec.md"

    def write_spec(self, content: str) -> None:
        self.spec_path().write_text(content)

    def read_spec(self) -> str:
        return self.spec_path().read_text() if self.spec_path().exists() else ""

    # ---------- contracts par sprint ----------

    def contract_path(self, sprint_id: str) -> Path:
        return self.contracts_dir / f"sprint_{sprint_id}.md"

    def write_contract(self, sprint_id: str, content: str) -> None:
        self.contract_path(sprint_id).write_text(content)

    def read_contract(self, sprint_id: str) -> str:
        p = self.contract_path(sprint_id)
        return p.read_text() if p.exists() else ""

    # ---------- critiques par itération ----------

    def critique_path(self, sprint_id: str, iteration: int) -> Path:
        return self.critiques_dir / f"sprint_{sprint_id}_iter_{iteration:03d}.md"

    def write_critique(self, sprint_id: str, iteration: int, content: str) -> None:
        self.critique_path(sprint_id, iteration).write_text(content)

    def list_critiques(self, sprint_id: str) -> list[Path]:
        return sorted(self.critiques_dir.glob(f"sprint_{sprint_id}_iter_*.md"))

    # ---------- test results ----------

    def write_test_results(self, sprint_id: str, results: TestResults) -> None:
        results_dir = self.state_dir / "test_results"
        results_dir.mkdir(exist_ok=True)
        path = results_dir / f"{sprint_id}.json"
        path.write_text(results.model_dump_json(indent=2))

    def read_test_results(self, sprint_id: str) -> TestResults | None:
        path = self.state_dir / "test_results" / f"{sprint_id}.json"
        if not path.exists():
            return None
        return TestResults.model_validate_json(path.read_text())

    # ---------- traces ----------

    def write_trace(self, agent: str, content: str) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        p = self.traces_dir / f"{agent}_{ts}.log"
        p.write_text(content)
        return p

    # ---------- helpers ----------

    def update_sprint(self, progress: ProgressState, sprint_id: str, **fields: Any) -> None:
        """Met à jour un sprint dans progress et persiste."""
        for s in progress.sprints:
            if s.id == sprint_id:
                for k, v in fields.items():
                    setattr(s, k, v)
                break
        self.write_progress(progress)
