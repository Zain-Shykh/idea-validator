"""train.ipynb runs top to bottom (spec Z9 A4 7). Every ML library is a STUB: this proves the
plumbing (files, settings, log), nothing about training. The notebook has not run on a GPU."""

import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml

from dataset import ROOT

NOTEBOOK = ROOT / "jobs/finetune/train.ipynb"
CONFIG = ROOT / "jobs/finetune/train_config.yaml"


def code_cells() -> list[str]:
    cells = json.loads(NOTEBOOK.read_text())["cells"]
    return ["".join(c["source"]) for c in cells if c["cell_type"] == "code"]


class _Tokenizer:
    def apply_chat_template(self, messages: list[dict[str, str]], **_: Any) -> str:
        return "".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in messages)

    def __call__(self, *, text: str, **_: Any) -> dict[str, list[str]]:
        return {"input_ids": text.split()}

    def save_pretrained(self, path: str) -> None:
        pass


def stubs(calls: dict[str, Any], *, gpu: bool = True) -> dict[str, ModuleType]:
    class Model:
        def save_pretrained(self, path: str) -> None:
            calls["adapter"] = path

        def save_pretrained_merged(self, path: str, tokenizer: Any, **kw: Any) -> None:
            calls["merged"] = (path, kw)

    class FastLanguageModel:
        @staticmethod
        def from_pretrained(**kw: Any) -> tuple[Model, _Tokenizer]:
            calls["from_pretrained"] = kw
            return Model(), _Tokenizer()

        @staticmethod
        def get_peft_model(model: Model, **kw: Any) -> Model:
            calls["peft"] = kw
            return model

    class Trainer:
        def __init__(self, **kw: Any) -> None:
            calls["trainer"] = kw
            self.state = SimpleNamespace(log_history=[])

        def train(self) -> SimpleNamespace:
            self.state.log_history.append({"step": 1, "loss": 0.0})  # FAKE
            return SimpleNamespace(metrics={"stub": True})

        def evaluate(self) -> None:
            self.state.log_history.append({"step": 1, "eval_loss": 0.0})  # FAKE

    def only_responses(trainer: Trainer, **kw: Any) -> Trainer:
        calls["markers"] = kw
        return trainer

    def module(name: str, **attributes: Any) -> ModuleType:
        made = ModuleType(name)
        made.__dict__.update(attributes)
        return made

    cuda = SimpleNamespace(is_available=lambda: gpu, get_device_name=lambda _: "STUB GPU")
    return {
        "unsloth": module("unsloth", FastLanguageModel=FastLanguageModel),
        "unsloth.chat_templates": module(
            "unsloth.chat_templates", train_on_responses_only=only_responses
        ),
        "torch": module("torch", cuda=cuda),
        "datasets": module("datasets", Dataset=SimpleNamespace(from_list=list)),
        "trl": module("trl", SFTTrainer=Trainer, SFTConfig=lambda **kw: kw),
    }


def workspace(folder: Path, *, long_rows: int = 0, rows: int = 3) -> None:
    (folder / "data").mkdir()
    (folder / "train_config.yaml").write_text(CONFIG.read_text())
    hashes = {}
    for name in ("train", "val"):
        texts = [f"FAKE {name} {n}" for n in range(rows)] + ["word " * 3000] * long_rows
        lines = [
            {"messages": [{"role": r, "content": t} for r in ("system", "user", "assistant")]}
            for t in texts
        ]
        raw = "".join(json.dumps(line) + "\n" for line in lines).encode()
        (folder / f"data/{name}.jsonl").write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    report = {"sha256": hashes, "fake": True}
    (folder / "data/split-report.json").write_text(json.dumps(report))


def run(folder: Path, monkeypatch: pytest.MonkeyPatch, *, gpu: bool = True) -> dict[str, Any]:
    calls: dict[str, Any] = {}
    for name, made in stubs(calls, gpu=gpu).items():
        monkeypatch.setitem(sys.modules, name, made)
    monkeypatch.setenv("FT_DIR", str(folder))
    monkeypatch.delenv("FT_LOG_PATH", raising=False)
    namespace: dict[str, Any] = {"__name__": "__notebook__"}
    for number, source in enumerate(code_cells()):
        # IPython's %magic lines are not Python: the install is not run
        plain = "\n".join(x for x in source.split("\n") if not x.startswith(("%", "!")))
        exec(compile(plain, f"train.ipynb[{number}]", "exec"), namespace)
    return calls


def test_runs_top_to_bottom_and_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace(tmp_path, long_rows=1)
    calls = run(tmp_path, monkeypatch)
    config = yaml.safe_load(CONFIG.read_text())
    log = json.loads((tmp_path / "outputs/finetune-train-log.json").read_text())
    assert log["settings"] == config and log["fake_data"] is True and log["gpu"] == "STUB GPU"
    assert log["data"]["train"] == {
        "rows_in_file": 4,
        "rows_used": 3,
        "too_long": 1,
        "sha256": hashlib.sha256((tmp_path / "data/train.jsonl").read_bytes()).hexdigest(),
    }
    assert {"loss", "eval_loss"} <= {k for entry in log["log_history"] for k in entry}
    # the settings reach the libraries
    assert calls["peft"]["r"] == config["lora"]["r"] == 16
    assert (calls["peft"]["lora_alpha"], calls["peft"]["lora_dropout"]) == (32, 0.05)
    args = calls["trainer"]["args"]
    assert (args["learning_rate"], args["num_train_epochs"], args["max_length"]) == (2e-4, 3, 2048)
    assert args["per_device_train_batch_size"] * args["gradient_accumulation_steps"] == 8
    assert calls["markers"]["response_part"] == "<|im_start|>assistant\n"
    assert calls["merged"][1] == {"save_method": "merged_16bit"}


def test_changed_data_file_stops_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace(tmp_path)
    with (tmp_path / "data/train.jsonl").open("a") as handle:
        handle.write('{"messages": []}\n')
    with pytest.raises(SystemExit, match=r"not the file split\.py wrote"):
        run(tmp_path, monkeypatch)
    assert not (tmp_path / "outputs").exists()


def test_no_gpu_and_no_fitting_row_stop_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace(tmp_path)
    with pytest.raises(SystemExit, match="no GPU"):
        run(tmp_path, monkeypatch, gpu=False)
    other = tmp_path / "long"
    other.mkdir()
    workspace(other, rows=0, long_rows=2)
    with pytest.raises(SystemExit, match="no row fits"):
        run(other, monkeypatch)


def test_settings_record_everything_ft6_names_and_pin_versions() -> None:
    config = yaml.safe_load(CONFIG.read_text())
    assert {"base_model", "max_seq_length"} <= set(config)
    assert {"r", "alpha", "dropout"} <= set(config["lora"])
    assert {
        "learning_rate", "epochs", "per_device_batch_size", "gradient_accumulation_steps", "seed",
    } <= set(config["training"])  # fmt: skip
    assert all("==" in package for package in config["packages"])


def test_notebook_is_committed_without_outputs_and_never_reads_the_test_set() -> None:
    cells = json.loads(NOTEBOOK.read_text())["cells"]
    assert all(not c.get("outputs") and not c.get("execution_count") for c in cells)
    assert not any("test.jsonl" in source for source in code_cells())
