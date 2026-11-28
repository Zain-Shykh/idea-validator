from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from verdict_factories import CONFIG_PATH

from common.contracts.core import Dimension, Goal
from verdict.config import VerdictConfig


def rules() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(CONFIG_PATH.read_text())
    return data


def test_shipped_rules_are_the_ones_of_the_requirements(config: VerdictConfig) -> None:
    """VE-6, VE-10, architecture §13."""
    presets = {goal: [getattr(w, d.value) for d in Dimension] for goal, w in config.weights.items()}
    assert presets == {
        Goal.STARTUP: [25, 20, 20, 15, 15, 5],
        Goal.PORTFOLIO: [15, 15, 15, 25, 10, 20],
        Goal.COURSE_PROJECT: [10, 10, 15, 35, 10, 20],
    }
    assert all(sum(p) == 100 for p in presets.values())
    assert config.thresholds.model_dump() == {"T_go": 70, "T_no": 50, "floor": 1.5}
    assert config.floor_dimensions == list(Dimension)[:5]
    assert (config.confidence.min, config.sensitivity.weight_delta) == (0.5, 0.2)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("weights", "portfolio", "learning"), 20.001),
        (("weights", "portfolio", "learning"), -1),
        (("weights",), {"portfolio": rules()["weights"]["portfolio"]}),
        (
            ("weights", "startup"),
            dict(zip((d.value for d in Dimension), (90, 2, 2, 2, 2, 2), strict=True)),
        ),
        (("thresholds", "T_no"), 80),
        (("thresholds", "floor"), 6),
        (("floor_dimensions",), ["demand"]),
        (("confidence", "w_sources"), 0.4),
        (("confidence", "sources_norm"), 0),
        (("gates", "feasibility", "effort_margin"), 0),
        (("gates", "evidence", "min_sources"), -1),
        (("challenge", "score_penalty"), -0.5),
        (("sensitivity", "weight_delta"), 1.0),
        (("whatif", "step"), 0),
        (("whatif", "max_delta"), 0.25),
        (("version",), "1"),
        (("weights_version",), "x"),
        (("thresholds", "T_g0"), 70),  # a typo must not be ignored
        (("thresholds", "T_go"), float("nan")),
    ],
)
def test_invalid_rules_are_rejected(path: tuple[str, ...], value: Any) -> None:
    data = rules()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        VerdictConfig.model_validate(data)


@pytest.mark.parametrize("key", list(rules()))
def test_every_section_is_required(key: str) -> None:
    data = rules()
    del data[key]
    with pytest.raises(ValidationError):
        VerdictConfig.model_validate(data)
