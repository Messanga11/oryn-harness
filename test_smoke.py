"""Tests basiques de smoke. Ne testent PAS l'intégration avec claude CLI."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from oryn_harness.config import HarnessConfig
from oryn_harness.state import (
    ProgressState,
    RubricScore,
    Sprint,
    SprintStatus,
    StateManager,
)
from datetime import datetime


def test_config_defaults():
    cfg = HarnessConfig(user_prompt="test", workdir=Path("/tmp/x"))
    assert cfg.budget_usd == 50.0
    assert sum(cfg.rubric_weights.values()) == pytest.approx(1.0)


def test_config_invalid_weights():
    with pytest.raises(ValueError):
        HarnessConfig(
            user_prompt="t",
            workdir=Path("/tmp/x"),
            rubric_weights={"design": 0.5, "originality": 0.5, "craft": 0.5, "functionality": 0.5},
        )


def test_state_init_and_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        state = StateManager(Path(tmp))
        state.init()

        assert state.state_dir.exists()
        assert state.contracts_dir.exists()
        assert state.traces_dir.exists()

        sprints = [
            Sprint(id="00", title="Scaffold", description="..."),
            Sprint(id="01", title="Auth", description="..."),
        ]
        state.write_feature_list(sprints)
        read_back = state.read_feature_list()
        assert len(read_back) == 2
        assert read_back[0].id == "00"


def test_progress_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        state = StateManager(Path(tmp))
        state.init()

        progress = ProgressState(
            started_at=datetime.now(),
            user_prompt="test",
            sprints=[Sprint(id="00", title="t", description="d")],
            total_cost_usd=1.23,
        )
        state.write_progress(progress)
        read_back = state.read_progress()
        assert read_back is not None
        assert read_back.total_cost_usd == 1.23
        assert read_back.sprints[0].id == "00"


def test_rubric_score_weighted():
    weights = {"design": 0.3, "originality": 0.25, "craft": 0.2, "functionality": 0.25}
    score = RubricScore.from_dict(
        {"design": 0.8, "originality": 0.6, "craft": 0.7, "functionality": 0.9, "feedback": "ok"},
        weights,
    )
    expected = 0.8 * 0.3 + 0.6 * 0.25 + 0.7 * 0.2 + 0.9 * 0.25
    assert score.weighted_total == pytest.approx(expected)


def test_sprint_update():
    with tempfile.TemporaryDirectory() as tmp:
        state = StateManager(Path(tmp))
        state.init()
        progress = ProgressState(
            started_at=datetime.now(),
            user_prompt="t",
            sprints=[Sprint(id="00", title="t", description="d")],
        )
        state.update_sprint(progress, "00", status=SprintStatus.PASSED, last_score=0.85)

        read_back = state.read_progress()
        assert read_back.sprints[0].status == SprintStatus.PASSED
        assert read_back.sprints[0].last_score == 0.85
