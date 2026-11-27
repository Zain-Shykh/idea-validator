"""``tune.py`` (spec Z10, A4 #6). The "human" verdicts here are invented to test the code."""

import json
from pathlib import Path
from typing import Any

import pytest
from verdict_factories import CONFIG_PATH, body, duplicate

import tune
from common.contracts.core import VerdictLabel, VerdictRequest
from verdict.config import load_config
from verdict.engine import decide, label_only

BASE = load_config(CONFIG_PATH)
GO, PIVOT, NO_GO = VerdictLabel.GO, VerdictLabel.PIVOT, VerdictLabel.NO_GO


def request(score: float, **gates: Any) -> VerdictRequest:
    """All six dimensions at ``score``: the weighted score is 20 x score."""
    return VerdictRequest.model_validate(body(scores=(score,) * 6, **gates))


def strict_people(count: int = 20) -> tuple[dict[str, VerdictRequest], dict[str, VerdictLabel]]:
    """Invented labellers who say GO only from 76: half the ideas score 72, which the current
    rules (T_go 70) call GO."""
    requests, human = {}, {}
    for n in range(count):
        score, label = ((3.6, PIVOT), (4.2, GO))[n % 2]
        requests[f"i{n}"], human[f"i{n}"] = request(score), label
    return requests, human


def test_label_only_is_the_verdict_of_decide() -> None:
    cases = [request(s) for s in (1.0, 2.4, 2.6, 3.4, 3.6, 4.4)]
    cases += [request(4.0, n_sources=1), request(4.0, duplicate=duplicate())]
    for case in cases:
        assert label_only(case, BASE) is decide(case, BASE).verdict


def test_the_grid_starts_with_the_current_rules_and_holds_only_valid_ones() -> None:
    rules = tune.candidates(BASE)
    assert rules[0].config == BASE and rules[0].changes == {}
    assert len(rules) > 1000
    assert len(
        {json.dumps(c.config.model_dump(mode="json"), sort_keys=True) for c in rules}
    ) == len(rules)
    for c in rules:
        assert c.config.thresholds.T_no <= c.config.thresholds.T_go
        for preset in c.config.weights.values():
            assert sum(preset.model_dump().values()) == 100


def test_folds_are_stratified_disjoint_and_fixed() -> None:
    human = {f"i{n}": (GO, NO_GO)[n % 2] for n in range(20)}
    found = tune.folds(human, 5)
    assert sorted(i for fold in found for i in fold) == sorted(human)
    assert all(sum(human[i] is GO for i in fold) == 2 for fold in found)
    assert found == tune.folds(dict(reversed(list(human.items()))), 5)
    with pytest.raises(tune.TuneError):
        tune.folds(dict(list(human.items())[:9]), 5)


def test_stricter_people_move_t_go_and_cross_validation_agrees() -> None:
    requests, human = strict_people()
    rules = tune.candidates(BASE, weight_step=0)
    result = tune.tune(requests, human, rules)
    assert result["before"]["agreement"] == 0.5 and result["after"]["agreement"] == 1
    # the nearest change that gets everything right
    assert result["best_on_all"]["changes"] == {"T_go": 75.0}
    check = result["cross_validation"]
    assert check["tuned"]["held_out_agreement"] == 1 and check["tuned"]["fold_stdev"] == 0
    assert check["current"]["held_out_agreement"] == 0.5 and check["train_test_gap"] == 0
    assert result["recommendation"] == "adopt"


def test_agreement_with_the_current_rules_keeps_them() -> None:
    requests, _ = strict_people()
    human = {i: label_only(r, BASE) for i, r in requests.items()}
    result = tune.tune(requests, human, tune.candidates(BASE))
    assert result["best_on_all"]["changes"] == {} and result["recommendation"] == "keep"
    assert all(f["chosen"] == {} for f in result["cross_validation"]["folds"])


def test_a_false_go_outweighs_agreement() -> None:
    # nine ideas people call GO and one they call NO-GO, all with the same scores: agreement
    # alone would keep GO, the false GO must win
    requests = {f"i{n}": request(3.6) for n in range(10)}
    human = {f"i{n}": GO if n else NO_GO for n in range(10)}
    result = tune.tune(requests, human, tune.candidates(BASE, weight_step=0))
    assert result["before"]["false_go_on_no_go"] == 1
    assert result["after"]["false_go_on_no_go"] == 0
    # but on held-out ideas it agrees less, so it is not recommended
    assert result["recommendation"] == "keep"


def write_inputs(folder: Path, fake: bool = True) -> tuple[Path, Path]:
    requests, human = strict_people()
    evaluation = {
        "kind": "eval",
        "fake": fake,
        "git_sha": "x",
        "ideas": [
            {
                "idea_id": i,
                "verdict": label_only(r, BASE).value,
                "request": r.model_dump(mode="json"),
            }
            for i, r in requests.items()
        ]
        + [{"idea_id": "failed", "verdict": None, "request": None}],
    }
    results = folder / "eval.json"
    results.write_text(json.dumps(evaluation))
    labels = folder / "resolved.csv"
    labels.write_text(
        "idea_id,verdict,labelled_by,resolution\n"
        + "".join(f"{i},{v},invented,\n" for i, v in human.items())
    )
    return results, labels


def test_the_command_writes_matrices_and_leaves_the_rules_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grid = tune.candidates
    monkeypatch.setattr(tune, "candidates", lambda base: grid(base, weight_step=0))
    results, labels = write_inputs(tmp_path)
    before = CONFIG_PATH.read_bytes()
    args = ["--results", str(results), "--labels", str(labels), "--out", str(tmp_path / "out")]
    assert tune.main(args) == 0
    assert CONFIG_PATH.read_bytes() == before
    (report,) = (tmp_path / "out").glob("FAKE-tune-*.json")
    data = json.loads(report.read_text())
    assert data["fake"] and "failed" in data["warnings"][0]
    text = report.with_suffix(".md").read_text()
    assert "FAKE input" in text and text.count("| human \\ system |") == 2
    assert "GO on a human NO-GO" in text and "fold stdev" in text


def test_the_command_refuses_unsound_input(tmp_path: Path) -> None:
    results, labels = write_inputs(tmp_path)
    args = ["--results", str(results), "--labels", str(labels)]
    # fake input never reaches eval/results (the default output)
    assert tune.main(args) == 2
    out = ["--out", str(tmp_path / "out")]
    labels.write_text("\n".join(labels.read_text().split("\n")[:-3]) + "\n")
    assert tune.main([*args, *out]) == 2  # a verdict is missing
    assert tune.main(["--results", str(results), "--labels", str(tmp_path / "no.csv"), *out]) == 2
    results.write_text(json.dumps({"kind": "drift"}))
    assert tune.main([*args, *out]) == 2
    assert not (tmp_path / "out").exists()


# --- edge cases: tune (all input here is FAKE) ---


@pytest.mark.parametrize(
    "ideas",
    [None, [1], ["x"], [{}], [{"idea_id": "a", "request": 5}], []],
)
def test_tune_refuses_a_hostile_report_with_exit_2(tmp_path: Path, ideas: Any) -> None:
    results = tmp_path / "FAKE_eval.json"
    results.write_text(json.dumps({"kind": "eval", "fake": True, "ideas": ideas}))
    labels = tmp_path / "FAKE_resolved.csv"
    labels.write_text("idea_id,verdict,labelled_by,resolution\n")
    out = tmp_path / "out"
    args = ["--results", str(results), "--labels", str(labels), "--out", str(out)]
    assert tune.main(args) == 2
    assert not out.exists()


@pytest.mark.parametrize("config", ["thresholds: [", "{}", "- 1"])
def test_tune_with_a_broken_rules_file_gives_exit_2(tmp_path: Path, config: str) -> None:
    results = tmp_path / "FAKE_eval.json"
    results.write_text(json.dumps({"kind": "eval", "fake": True, "ideas": []}))
    labels = tmp_path / "FAKE_resolved.csv"
    labels.write_text("idea_id,verdict,labelled_by,resolution\n")
    rules = tmp_path / "verdict.yaml"
    rules.write_text(config)
    out = tmp_path / "out"
    args = ["--results", str(results), "--labels", str(labels), "--out", str(out)]
    assert tune.main([*args, "--config", str(rules)]) == 2
    assert not out.exists()
