# Vice SysAdmin

Replication package for:

> A. Parres-Peredo, "Cross-Provider Auditing Outperforms Same-Provider
> Auditing in LLM-Driven Linux System Administration: An Empirical
> Study," *IEEE Access*, 2026 (submitted).

This repository contains the experimental harness, the 18-task
catalog, the prompts used by the proposer and auditor models, and the
aggregated results and figures reported in the paper. It supports an
empirical study of whether adding a second LLM as an independent
**auditor** improves the safety and correctness of an LLM agent that
performs real Linux system-administration tasks, and whether an
auditor drawn from a different model provider than the proposer
(heterogeneous) behaves differently from one drawn from the same
provider (homogeneous).

## What is in this repository

| Path | Contents |
|---|---|
| `vice_sysadmin/` | The harness: proposer/policy/auditor/orchestrator loop, schema validation, structured-output repair retry, cost tracking, and the JSONL event logger. |
| `tasks/` | The 18 task definitions used in the study, across 6 categories (service management, user permissions, fault diagnosis, log analysis, ambiguous instructions, network/firewall). |
| `prompts/` | The proposer, auditor (blind/informed), and repair-retry prompt templates. |
| `configs/` | Run configurations, including `lote1-18tasks-verify-nudge-syntax-v3-b2x.yaml`, the exact configuration used for the final 810-repetition run reported in the paper (3 conditions x 18 tasks x 15 repetitions). |
| `fixtures/` | Mock model responses used by the offline test suite (no live API calls). |
| `tests/` | The unit and integration test suite the harness was developed against. |
| `results/` | Aggregated results behind the paper's tables, exported as CSV, plus the original workbook (`lote1-resultados-finales.xlsx`). |
| `figures/` | The script that generates the paper's result figures from the CSVs, the rendered figures, and the architecture diagram source. |
| `docs/` | Design notes and provenance records from the harness's iterative development. |

Raw per-step JSONL event logs from the 810 repetitions (full command
transcripts, model responses, and token/cost accounting for every
step) are not included here due to their size; the `results/` folder
contains the data these logs were aggregated into, which is what the
paper's tables and figures are computed from.

## Reproducing the aggregated results and figures

```bash
pip install -r requirements.lock.txt
cd figures
python make_results_figures.py
```

This regenerates `fig_success_by_condition.pdf` and
`fig_blocking_by_condition.pdf` directly from the numbers in
`results/table1_overall_by_condition.csv` and
`results/table3_highrisk_proxy.csv`.

## Running the harness

The offline test suite exercises the harness against mock model
responses and does not require any API key or live container runtime:

```bash
pip install -r requirements.lock.txt
pytest -q
```

Running the harness live, as it was run for the paper, additionally
requires:

- An LXD-capable host (the harness provisions a disposable container
  per repetition and does not touch the host system otherwise).
- API keys for the providers named in a given config
  (`OPENAI_API_KEY`, `GEMINI_API_KEY`), set as environment variables.
- The live dependency overlay: `pip install -r requirements-live.lock.txt`.

```bash
python -m vice_sysadmin.run_experiment --config configs/lote1-18tasks-verify-nudge-syntax-v3-b2x.yaml
```

Running the full configuration against live models and real
containers incurs real API and compute cost; `config.yaml` and the
smaller configs under `configs/` are a cheaper starting point for
checking that a setup works before running the full study.

## Citation

A BibTeX entry will be added here once the paper is formally
published; in the meantime, please cite it as the preprint/submission
named above.

## License

Released under the MIT License (see `LICENSE`). The task catalog,
prompts, and harness code are original work produced for this study.
