"""Trading holiday collection, normalization, and optional Google publishing."""
import argparse
from pathlib import Path

from src.pipeline import collect_live, export, reference_csv, validate_dataset

ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--mode", choices=["reference", "live"], default="reference")
    parser.add_argument("--publish", action="store_true", help="Upload CSV and native Sheet to Google Drive")
    parser.add_argument(
        "--allow-cme-reference",
        action="store_true",
        help="If CME is inaccessible, use the reviewed 2026 CME records and mark them as a fallback",
    )
    parser.add_argument(
        "--allow-opra-reference",
        action="store_true",
        help=(
            "If the OCC memo is inaccessible, use the reviewed 2026 options "
            "holiday reference as an explicitly labeled OPRA proxy"
        ),
    )
    parser.add_argument("--oauth-json", type=Path, default=ROOT / "credentials.json")
    parser.add_argument("--token-json", type=Path, default=ROOT / "token.json")
    args = parser.parse_args()

    baseline = reference_csv(ROOT / "reference" / "holidays_2026.csv")
    if args.mode == "live":
        records = collect_live(
            args.year,
            baseline,
            allow_cme_reference=args.allow_cme_reference,
            allow_opra_reference=args.allow_opra_reference,
        )
    else:
        if args.allow_cme_reference or args.allow_opra_reference:
            parser.error("Reference fallback flags only apply to --mode live")
        if args.year != 2026:
            parser.error("2026 is the only verified reference dataset; use --mode live for other years")
        records = validate_dataset(baseline, args.year)

    csv_file, summary = export(records, ROOT / "output", args.year)
    print(f"Validated {len(records)} records; CSV: {csv_file}; summary: {summary}")

    if args.publish:
        from src.publish import publish
        urls = publish(csv_file, args.year, args.oauth_json, args.token_json)
        print(f"Drive CSV: {urls['csv_url']}")
        print(f"Google Sheet: {urls['sheet_url']}")


if __name__ == "__main__":
    main()
