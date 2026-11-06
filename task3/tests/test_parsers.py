import unittest
from src.scrapers import (
    parse_nasdaq, parse_nyse, parse_cme, parse_cme_range, parse_occ_text,
)
from src.pipeline import reference_csv, validate_dataset, collect_live
from pathlib import Path
from unittest.mock import patch

class ParsingTests(unittest.TestCase):
    def test_nasdaq(self):
        html = '''<h1>U.S. Equity and Options Markets Holiday Schedule 2026</h1>
          <table><tr><th>2026</th><th>Holiday</th><th>Status</th></tr>
          <tr><td>January 1, 2026</td><td>New Years Day (Observed)</td><td>Closed</td></tr>
          <tr><td>November 27, 2026</td><td>Early Close* - U.S.</td><td>1:00 p.m.</td></tr></table>'''
        rows = parse_nasdaq(html)
        self.assertEqual([r.status for r in rows], ['CLOSED', 'EARLY_CLOSE'])
        self.assertEqual(rows[1].holiday, 'Day after Thanksgiving')

    def test_nyse(self):
        html = '''<h1>Holidays & Trading Hours</h1><table>
          <tr><th>Holiday</th><th>2025</th><th>2026</th><th>2027</th></tr>
          <tr><td>Thanksgiving Day</td><td>Thursday, November 27**</td><td>Thursday, November 26**</td><td>Thursday, November 25**</td></tr>
          <tr><td>Christmas Day</td><td>Thursday, December 25***</td><td>Friday, December 25***</td><td>Friday, December 24</td></tr>
          </table>'''
        rows = parse_nyse(html)
        self.assertEqual(len(rows), 4)
        self.assertEqual({r.date for r in rows}, {'2026-11-26','2026-11-27','2026-12-24','2026-12-25'})

    def test_cme_windows(self):
        self.assertEqual(parse_cme_range('31 December 2025 – 2 January 2026')[0].isoformat(), '2025-12-31')
        self.assertEqual(parse_cme_range('18 - 20 January 2026')[0].isoformat(), '2026-01-18')
        html = '''<h2>2026 CME Globex Trading Schedule</h2><table><tr><th>U.S. HOLIDAY</th><th>INCLUDES THE FOLLOWING DATES:</th></tr>
        <tr><td>Dr. Martin Luther King, Jr.</td><td>18 - 20 January 2026</td></tr>
        <tr><td>New Year’s</td><td>31 December 2026 - 1 January 2027</td></tr></table>'''
        rows = parse_cme(html, 2026, {'Martin Luther King Jr. Day':'2026-01-19',"New Year's Day":'2026-01-01'})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].date, '2026-01-19')
        self.assertEqual(rows[0].status, 'HOLIDAY_HOURS')

    def test_occ_proxy(self):
        text = '''OCC Holidays\nDate Holiday\nJanuary 1 New Year’s Day\nJanuary 19 Martin Luther King, Jr. Day\nU.S. Banking Holidays\nOctober 12 Columbus Day'''
        rows = parse_occ_text(text)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r.source_basis.startswith('OCC_PROXY') for r in rows))


    def test_occ_html_response(self):
        """OCC info memo #57897 is published as HTML, not just as PDF."""
        from unittest.mock import Mock, patch
        from src.scrapers import scrape_opra_proxy

        holidays = [
            ("January 1", "New Year's Day"),
            ("January 19", "Martin Luther King, Jr. Day"),
            ("February 16", "Presidents Day"),
            ("April 3", "Good Friday"),
            ("May 25", "Memorial Day"),
            ("June 19", "Juneteenth National Independence Day"),
            ("July 3", "Independence Day"),
            ("September 7", "Labor Day"),
            ("November 26", "Thanksgiving"),
            ("December 25", "Christmas Day"),
        ]
        rows = "".join(f"<tr><td>{day}</td><td>{name}</td></tr>" for day, name in holidays)
        html = (
            "<html><body><h2>OCC Holidays</h2><table>" + rows + "</table>"
            "<h2>U.S. Banking Holidays</h2><table>"
            "<tr><td>October 12</td><td>Columbus Day</td></tr></table></body></html>"
        )

        response = Mock()
        response.content = html.encode("utf-8")
        response.text = html
        response.raise_for_status.return_value = None
        with patch("src.scrapers.requests.get", return_value=response):
            result = scrape_opra_proxy(2026)

        self.assertEqual(len(result), 10)
        self.assertTrue(all(row.source_basis == "OCC_PROXY_NOT_OPRA_OFFICIAL" for row in result))

    def test_baseline(self):
        path = Path(__file__).resolve().parents[1] / 'reference/holidays_2026.csv'
        rows = validate_dataset(reference_csv(path), 2026)
        self.assertEqual(len(rows),44)
        self.assertEqual(sum(r.status=='EARLY_CLOSE' for r in rows),4)
        self.assertEqual(sum(r.status=='HOLIDAY_HOURS' for r in rows),10)


    def test_cme_reference_fallback_is_opt_in(self):
        baseline_path = Path(__file__).resolve().parents[1] / "reference/holidays_2026.csv"
        baseline = reference_csv(baseline_path)
        original_nasdaq = [row for row in baseline if row.exchange == "NASDAQ"]
        original_nyse = [row for row in baseline if row.exchange == "NYSE"]
        original_opra = [row for row in baseline if row.exchange == "OPRA"]

        with (
            patch("src.pipeline.scrape_nasdaq", return_value=original_nasdaq),
            patch("src.pipeline.scrape_nyse", return_value=original_nyse),
            patch("src.pipeline.scrape_cme", side_effect=RuntimeError("HTTP2 failure")),
            patch("src.pipeline.scrape_opra_proxy", return_value=original_opra),
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP2 failure"):
                collect_live(2026, baseline)

            rows = collect_live(2026, baseline, allow_cme_reference=True)

        cme_rows = [row for row in rows if row.exchange == "CME"]
        self.assertEqual(len(rows), 44)
        self.assertEqual(len(cme_rows), 10)
        self.assertTrue(all(row.source_basis == "OFFICIAL_REFERENCE_FALLBACK" for row in cme_rows))

    def test_occ_reference_fallback_is_opt_in(self):
        """An OCC HTTP 403 must not be mistaken for a successful live scrape."""
        from requests.exceptions import HTTPError

        baseline_path = Path(__file__).resolve().parents[1] / "reference/holidays_2026.csv"
        baseline = reference_csv(baseline_path)

        with (
            patch("src.pipeline.scrape_nasdaq", return_value=[r for r in baseline if r.exchange == "NASDAQ"]),
            patch("src.pipeline.scrape_nyse", return_value=[r for r in baseline if r.exchange == "NYSE"]),
            patch("src.pipeline.scrape_cme", return_value=[r for r in baseline if r.exchange == "CME"]),
            patch("src.pipeline.scrape_opra_proxy", side_effect=HTTPError("403 Forbidden")),
        ):
            with self.assertRaisesRegex(RuntimeError, "allow-opra-reference"):
                collect_live(2026, baseline)

            rows = collect_live(2026, baseline, allow_opra_reference=True)

        opra_rows = [r for r in rows if r.exchange == "OPRA"]
        self.assertEqual(len(rows), 44)
        self.assertEqual(len(opra_rows), 10)
        self.assertTrue(all(r.source_basis == "OCC_PROXY_REFERENCE_FALLBACK" for r in opra_rows))

    def test_both_website_access_failures_can_use_explicit_fallbacks(self):
        """Only the unreachable sources fall back; known live records are retained."""
        from requests.exceptions import HTTPError
        from tempfile import TemporaryDirectory
        from src.pipeline import export
        import json

        baseline_path = Path(__file__).resolve().parents[1] / "reference/holidays_2026.csv"
        baseline = reference_csv(baseline_path)

        with (
            patch("src.pipeline.scrape_nasdaq", return_value=[r for r in baseline if r.exchange == "NASDAQ"]),
            patch("src.pipeline.scrape_nyse", return_value=[r for r in baseline if r.exchange == "NYSE"]),
            patch("src.pipeline.scrape_cme", side_effect=RuntimeError("CME HTTP2 failure")),
            patch("src.pipeline.scrape_opra_proxy", side_effect=HTTPError("OCC 403")),
        ):
            rows = collect_live(
                2026,
                baseline,
                allow_cme_reference=True,
                allow_opra_reference=True,
            )

        self.assertEqual(len(rows), 44)
        with TemporaryDirectory() as folder:
            _, summary_path = export(rows, Path(folder), 2026)
            summary = json.loads(summary_path.read_text())

        self.assertEqual(summary["opra_proxy_events"], 10)
        self.assertEqual(summary["reference_fallback_events"], 20)
        self.assertEqual(summary["source_basis_counts"]["OFFICIAL_REFERENCE_FALLBACK"], 10)
        self.assertEqual(summary["source_basis_counts"]["OCC_PROXY_REFERENCE_FALLBACK"], 10)

if __name__ == '__main__':
    unittest.main()
