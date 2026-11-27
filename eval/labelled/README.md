# Labelled set (EVAL-1)

> **`ideas.jsonl` holds 40 candidates drafted by AI (claude-sonnet-5-5, 2026-11-28). The owner has
> not reviewed them (`owner_review: "pending"`). There are no human labels yet**, so there is no
> confusion matrix, baseline or tuned value.

The set is used only to measure and tune, **never to train** the scoring model: `jobs/finetune/split.py`
refuses any idea that is in it or close to one.

| File | Written by | Content |
|------|-----------|---------|
| `ideas.jsonl` | AI draft, then the owner | `idea_id`, `title`, `text`, `profile`, `drafted_by`, `owner_review` (`pending` / `accepted` / `edited`) |
| `case_types.csv` | AI draft | what each idea was drafted to cover (10 each: clear GO, clear NO-GO, borderline, thin evidence). **Do not open it before you have labelled.** It is not a verdict and nothing reads it as one |
| `labels_zain.csv`, `labels_ahmad.csv` | each person alone | `idea_id,verdict,note` |
| `resolved.csv` | both together | `idea_id,verdict,labelled_by,resolution` |

## How to label

1. Review `ideas.jsonl` first: edit or replace weak ideas and set `owner_review`. After labelling
   starts, the file must not change (a baseline records its hash).
2. Each person labels **alone**, without seeing the other's file, the case types or any system output.
   Use the idea **and its profile** (hours, skills, goal). Verdicts, spelled exactly:
   `GO`, `PIVOT`, `NO-GO`, `INSUFFICIENT_EVIDENCE`.
3. `python jobs/evaluation/labelled.py` validates the files and lists the disagreements and
   inter-rater κ.
4. Resolve every disagreement together in `resolved.csv`. `labelled_by` names who labelled the idea
   (for example `zain+ahmad`); `resolution` says why, and is required for a disagreement or a verdict
   neither person gave.
5. Run `labelled.py` again until it reports nothing open (exit 0), then commit.
