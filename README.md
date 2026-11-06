## Tasks

| Task | Description | Documentation |
| --- | --- | --- |
| **1 — Market Data Validation** | Historical candle consistency, edge cases, bounded REST benchmarks, and delayed SSE observations. | [README](task1/README.md) · [Technical report](task1/technical_report.md) |
| **2 — Incident Investigation** | Investigation and escalation plan for intermittent API 502 errors, upstream timeouts, and resource pressure. | [Incident plan](task2/task2.md) |
| **3 — Trading Holidays** | Collect and normalize 2026 NASDAQ, NYSE, CME, and OPRA-related calendar dates; publish a CSV and native Google Sheet. | [README](task3/README.md) · [Technical report](task3/technical_report.md) |

## Reproduce the offline checks

Use Python 3.11+ and install the requirements in each task directory.

```bash
cd task1
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python code/benchmark_rest.py --dry-run
python code/benchmark_multi.py --dry-run
python code/verify_partial_and_live.py --analyze-existing data/final_checks

cd ../task3
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py --mode reference
```

`task1/data/` contains saved validation and benchmark evidence. `task3/output/` includes the 2026 CSV and validation summary; running `--mode reference` rewrites those generated outputs, so retain the committed live-run versions for review. See the task-specific READMEs for live collection and Google OAuth publishing instructions.

## Published Task 3 outputs

- [Google Drive CSV](https://drive.google.com/file/d/1ep8SBMlPaQTo6koUOdl5D27gQRlrLAi8/view)
- [Native Google Sheet](https://docs.google.com/spreadsheets/d/1cPXo5rnFn0mngogdZAz2wESUNscn7CppteWWO9waojU/edit)

**Limitations:** Task 1 uses a public delayed demo feed, and finalized SSE-to-REST equality was not established. In Task 3's verified run, NASDAQ and NYSE were retrieved live; CME and the OCC-based OPRA proxy required explicitly labeled 2026 reference fallbacks. Access to the Google files depends on their sharing permissions.
