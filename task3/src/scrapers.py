"""Source-specific collectors for US exchange holiday calendars (2026).

Fetches current public pages; callers must validate before publishing. Does not
silently substitute hardcoded dates when a live source cannot be parsed.
"""
from __future__ import annotations

import calendar
import io
import re
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

from .model import Holiday

NASDAQ_URL = "https://nasdaqtrader.com/Trader.aspx?id=Calendar"
NYSE_URL = "https://www.nyse.com/markets/hours-calendars"
CME_URL = "https://www.cmegroup.com/trading-hours.html"
OCC_URL = "https://infomemo.theocc.com/infomemos?number=57897"

MONTH_PATTERN = r"January|February|March|April|May|June|July|August|September|October|November|December"
HOLIDAY_KEYS = {
    "new year": "New Year's Day",
    "martin luther": "Martin Luther King Jr. Day",
    "dr. martin": "Martin Luther King Jr. Day",
    "mlk": "Martin Luther King Jr. Day",
    "president": "Presidents Day",
    "washington": "Presidents Day",
    "good friday": "Good Friday",
    "memorial": "Memorial Day",
    "juneteenth": "Juneteenth",
    "independence": "Independence Day (observed)",
    "labor": "Labor Day",
    "thanksgiving": "Thanksgiving Day",
    "christmas": "Christmas Day",
}


def normalize_holiday(name: str) -> str:
    cleaned = re.sub(r"(?i)\s*(early close|\(observed\)|\*+|\s+-\s+u\.s\.)", "", name)
    for token, normalized in HOLIDAY_KEYS.items():
        if token in cleaned.lower():
            return normalized
    raise ValueError(f"Unrecognized holiday name: {name!r}")


def html_get(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; HolidayCalendarResearch/1.0)"},
        timeout=25,
    )
    response.raise_for_status()
    return response.text


def read_date(value: str, year: int) -> date:
    """Parse source labels such as 'Thursday, November 26***'."""
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"[*†‡]", "", value).strip().rstrip(",")
    value = re.sub(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+", "", value)
    value = re.sub(r"\s+", " ", value)
    if not re.search(r"\b20\d{2}\b", value):
        value = f"{value}, {year}"
    for fmt in ("%B %d, %Y", "%B %d %Y", "%d %B %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse calendar date: {value!r}")


def tables_after_heading(soup: BeautifulSoup, phrase: str):
    """Find the first table after an exact year/section heading."""
    heading = soup.find(lambda x: getattr(x, "name", "") in {"h1", "h2", "h3", "h4"}
                        and phrase.lower() in x.get_text(" ", strip=True).lower())
    if not heading:
        raise ValueError(f"Heading {phrase!r} not found")
    table = heading.find_next("table")
    if not table:
        raise ValueError(f"Table after {phrase!r} missing")
    return table


def parse_nasdaq(html: str, year: int = 2026) -> list[Holiday]:
    soup = BeautifulSoup(html, "html.parser")
    table = tables_after_heading(soup, f"Holiday Schedule {year}")
    result = []
    for tr in table.select("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 3:
            continue
        try:
            d = read_date(cells[0], year)
        except ValueError:
            continue
        if d.year != year:
            continue
        raw_status = cells[2].lower()
        if "closed" in raw_status:
            status, close = "CLOSED", ""
        elif "1:00" in raw_status or "early close" in cells[1].lower():
            status, close = "EARLY_CLOSE", "13:00"
        else:
            raise ValueError(f"Unknown Nasdaq trading status: {cells!r}")
        if status == "EARLY_CLOSE":
            label = "Day after Thanksgiving" if d.month == 11 else "Christmas Eve"
        else:
            label = normalize_holiday(cells[1])
        result.append(Holiday(
            "NASDAQ", d.isoformat(), label, status,
            close_time_local=close, timezone="America/New_York", source_url=NASDAQ_URL,
        ))
    return result


def parse_nyse(html: str, year: int = 2026) -> list[Holiday]:
    soup = BeautifulSoup(html, "html.parser")
    table = tables_after_heading(soup, "Holidays & Trading Hours")
    # The heading may be h1 and the first table has holiday rows, with 2025/26/27 columns.
    rows = table.select("tr")
    header = [c.get_text(" ", strip=True) for c in rows[0].find_all(["td", "th"])]
    if str(year) not in header:
        raise ValueError(f"NYSE calendar does not include year {year}: {header!r}")
    date_column = header.index(str(year))
    result = []
    for tr in rows[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) <= date_column or not cells[date_column].strip(" -*—"):
            continue
        d = read_date(cells[date_column], year)
        if d.year == year:
            result.append(Holiday(
                "NYSE", d.isoformat(), normalize_holiday(cells[0]), "CLOSED",
                timezone="America/New_York", source_url=NYSE_URL,
            ))
    # Official NYSE page publishes these early-close rules in footnotes, not table rows.
    # Apply dates based on parsed Thanksgiving/Christmas records, not fixed dates.
    for holiday in result[:]:
        d = date.fromisoformat(holiday.date)
        if holiday.holiday in {"Thanksgiving Day", "Christmas Day"}:
            if holiday.holiday == "Thanksgiving Day":
                early = d.replace(day=d.day + 1)
                label = "Day after Thanksgiving"
            else:
                from datetime import timedelta
                early = d - timedelta(days=1)
                label = "Christmas Eve"
            if early.year == year and early.weekday() < 5:
                result.append(Holiday(
                    "NYSE", early.isoformat(), label, "EARLY_CLOSE", "13:00",
                    "America/New_York", source_url=NYSE_URL,
                    notes="NYSE equity sessions; eligible options may close at 13:15 ET",
                ))
    return result


def parse_cme_range(raw: str) -> tuple[date, date]:
    """Convert '18 - 20 January 2026' or '31 December 2025 – 2 January 2026'."""
    text = re.sub(r"\s+", " ", raw.replace("–", "-").replace("—", "-")).strip()
    if "-" not in text:
        d = read_date(text, 2026)
        return d, d
    left, right = [part.strip() for part in text.split("-", 1)]
    end = read_date(right, 2026)
    if re.fullmatch(r"\d{1,2}", left):
        start = date(end.year, end.month, int(left))
    elif re.fullmatch(r"\d{1,2}\s+[A-Za-z]+", left):
        start = read_date(f"{left} {end.year}", end.year)
        if start > end:
            start = start.replace(year=start.year - 1)
    else:
        start = read_date(left, end.year)
    if start > end:
        raise ValueError(f"Reversed CME range: {raw!r}")
    return start, end


def parse_cme(html: str, year: int = 2026, anchors: dict[str, str] | None = None) -> list[Holiday]:
    soup = BeautifulSoup(html, "html.parser")
    table = tables_after_heading(soup, f"{year} CME Globex Trading Schedule")
    result = []
    for tr in table.select("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 2 or "HOLIDAY" in cells[0].upper():
            continue
        start, end = parse_cme_range(cells[1])
        holiday = normalize_holiday(cells[0])
        if anchors is None or holiday not in anchors:
            raise ValueError(f"No verified anchor date for CME {holiday!r}, year {year}")
        canonical = date.fromisoformat(anchors[holiday])
        if canonical.year != year or not start <= canonical <= end:
            # Example: the last row might be New Year's 2027; skip that row.
            if not (canonical < start and start.year == year and end.year == year + 1):
                raise ValueError(f"CME range {start}/{end} does not cover {holiday} {canonical}")
            continue
        if (start.year, canonical.year) != (year, year) and canonical.year != year:
            continue
        # CME frequently has two New Year rows; drop the NEXT year rather than duplicate.
        if start.year == year and end.year > year and canonical <= start:
            continue
        result.append(Holiday(
            "CME", canonical.isoformat(), holiday, "HOLIDAY_HOURS",
            timezone="America/Chicago", window_start=start.isoformat(),
            window_end=end.isoformat(), source_url=CME_URL,
            notes="Globex holiday-hours window; product-specific hours, not a universal closure",
        ))
    # Protect against future year crossover duplicated in same page.
    return list({r.date: r for r in result}.values())


def parse_occ_text(text: str, year: int = 2026) -> list[Holiday]:
    """Extract OCC closed dates for an explicitly labeled OPRA proxy calendar."""
    block = re.split(r"OCC Holidays", text, flags=re.IGNORECASE, maxsplit=1)
    if len(block) < 2:
        raise ValueError("OCC Holidays section not found")
    content = re.split(r"U\.S\. Banking Holidays", block[1], flags=re.IGNORECASE, maxsplit=1)[0]
    # PDF extraction may combine or separate date and name across lines.
    flat = re.sub(r"\s+", " ", content)
    starts = list(re.finditer(rf"\b({MONTH_PATTERN})\s+(\d{{1,2}})\b", flat, re.IGNORECASE))
    results = []
    for i, match in enumerate(starts):
        following = flat[match.end(): starts[i + 1].start() if i + 1 < len(starts) else len(flat)]
        name = re.sub(r"^(?:Date\s+Holiday\s+)?", "", following).strip(" -:|")
        if not name:
            continue
        d = read_date(f"{match.group(1)} {match.group(2)} {year}", year)
        results.append(Holiday(
            "OPRA", d.isoformat(), normalize_holiday(name), "CLOSED",
            timezone="America/New_York", source_url=OCC_URL,
            source_basis="OCC_PROXY_NOT_OPRA_OFFICIAL",
            notes="OCC options-clearing holiday proxy; OPRA-specific 2026 schedule not confirmed",
        ))
    return results


def scrape_nasdaq(year: int) -> list[Holiday]:
    return parse_nasdaq(html_get(NASDAQ_URL), year)


def scrape_nyse(year: int) -> list[Holiday]:
    return parse_nyse(html_get(NYSE_URL), year)


def scrape_cme(year: int, anchors: dict[str, str]) -> list[Holiday]:
    """Try the official CME page over HTTP, then with a browser.

    Some networks reject both methods. In that case raise a helpful error;
    the pipeline decides whether an explicitly requested reference fallback
    is acceptable. This function itself never silently substitutes data.
    """
    try:
        return parse_cme(html_get(CME_URL), year, anchors)
    except (requests.RequestException, ValueError) as error:
        # Exception variables are deleted when an except block ends;
        # preserve the diagnostic for the browser fallback path.
        http_error_message = str(error)

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "CME could not be loaded through HTTP, and Playwright is not installed. "
            "Install the optional browser dependencies or use the explicit "
            "--allow-cme-reference option for the reviewed 2026 schedule. "
            f"HTTP error: {http_error_message}"
        ) from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(
                    CME_URL,
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                heading = f"{year} CME Globex Trading Schedule"
                page.get_by_text(heading).first.wait_for(timeout=10_000)
                return parse_cme(page.content(), year, anchors)
            finally:
                browser.close()
    except (PlaywrightError, ValueError, OSError) as browser_error:
        raise RuntimeError(
            "Unable to retrieve the official CME Globex schedule. "
            f"HTTP attempt failed: {http_error_message}. "
            f"Browser attempt failed: {browser_error}. "
            "This is an access failure, not evidence that CME has no holidays. "
            "For the verified 2026 schedule, rerun with --allow-cme-reference "
            "to use a clearly labeled reference snapshot."
        ) from browser_error


def scrape_opra_proxy(year: int) -> list[Holiday]:
    """Read the OCC 2026 holiday memo as an explicitly labeled OPRA proxy.

    OCC publishes this memo as an HTML page. We also support PDF responses
    in case the publisher changes its format in a subsequent deployment.
    """
    if year != 2026:
        raise ValueError("The published OCC memo used here covers 2026 only")

    response = requests.get(
        OCC_URL,
        timeout=25,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    if response.content.lstrip().startswith(b"%PDF"):
        from pypdf import PdfReader

        pdf = PdfReader(io.BytesIO(response.content))
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    else:
        soup = BeautifulSoup(response.text, "html.parser")
        for element in soup(["script", "style"]):
            element.decompose()
        text = soup.get_text(" ", strip=True)

    holidays = parse_occ_text(text, year)
    if len(holidays) != 10:
        raise ValueError(
            f"Expected 10 OCC holiday records for 2026, got {len(holidays)}"
        )
    return holidays
