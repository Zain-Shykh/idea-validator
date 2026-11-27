"""The versioned files M3 needs: budgets, rubric versions and the prompt versions that go on
every verdict and into its cache key (AO-3, AVOID-2)."""

from dataclasses import dataclass
from pathlib import Path

from common.contracts.core import Budget, Dimension, Workflow
from common.registry import RegistryError, load_budgets, load_prompts, load_rubrics

# the prompts whose text can change a Validate card
VALIDATE_PROMPTS = ("intake", "scorer", "effort", "skeptic", "critic_check", "concretizer")


@dataclass(frozen=True)
class Rules:
    rubric_set: str
    rubric_ids: dict[Dimension, str]
    prompt_versions: dict[str, str]
    budgets: dict[Workflow, Budget]


def load_rules(rubrics_dir: Path, prompts_dir: Path, budgets_path: Path) -> Rules:
    """Raises ``RegistryError``."""
    rubric_set, rubrics = load_rubrics(rubrics_dir)
    prompts = load_prompts(prompts_dir)
    missing = [name for name in VALIDATE_PROMPTS if name not in prompts]
    if missing:
        raise RegistryError(f"missing prompts: {', '.join(missing)}")
    return Rules(
        rubric_set=rubric_set,
        rubric_ids={d: f"{d.value}@v{rubrics[d].version}" for d in Dimension},
        prompt_versions={name: prompts[name].id for name in VALIDATE_PROMPTS},
        budgets=load_budgets(budgets_path).workflows,
    )
