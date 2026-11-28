import pytest
from pydantic import ValidationError

from common.http import SCORER_TIMEOUT, VALIDATOR_TIMEOUT
from validators.main import build_engine
from validators.settings import ValidatorSettings


def test_default_timeouts_are_consistent() -> None:
    settings = ValidatorSettings()
    assert settings.scorer_concurrency == 1
    assert settings.scorer_timeout_s == SCORER_TIMEOUT.read
    # six serialised calls fit in one request, and the request fits in the caller's wait
    assert 6 * settings.scorer_timeout_s <= settings.deadline_s < (VALIDATOR_TIMEOUT.read or 0)


def test_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALIDATORS_SCORER_CONCURRENCY", "4")
    monkeypatch.setenv("KB_URL", "http://kb:8000")
    settings = ValidatorSettings()
    assert (settings.scorer_concurrency, settings.kb_url) == (4, "http://kb:8000")


@pytest.mark.parametrize(
    "bad",
    [
        {"scorer_concurrency": 0},
        {"scorer_timeout_s": 91},
        {"deadline_s": 600},
        {"max_evidence": 13},
        {"max_quote_chars": 501},
    ],
)
def test_settings_outside_the_bounds_are_rejected(bad: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        ValidatorSettings.model_validate(bad)


def test_engine_needs_urls_and_valid_registry(tmp_path: object) -> None:
    urls = {"KB_URL": "http://kb:8000", "SCORER_URL": "http://scorer:8000"}
    assert build_engine(ValidatorSettings()) is None
    built = build_engine(ValidatorSettings.model_validate(urls))
    # no TOOLS_URL for the default gateway backend: still usable, with band midpoints
    assert built is not None and built.analyst is None and built.effort.llm is None
    broken = ValidatorSettings.model_validate(urls | {"prompts_dir": str(tmp_path)})
    assert build_engine(broken) is None
