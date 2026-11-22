import copy
import csv
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import checks  # noqa: E402
import update  # noqa: E402


def load(name):
    with open(ROOT / "data" / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def data():
    return load("activities.csv"), load("cities.csv")


# --- the committed data -------------------------------------------------------

def test_committed_data_passes_every_check(data):
    assert checks.check(*data) == []


def test_data_starts_on_first_week_and_has_every_week_since(data):
    weeks = sorted({r["WeekEnding"] for r in data[1]})
    assert weeks[0] == "2025-09-20"
    assert len(weeks) >= 56


# --- the checks catch broken data --------------------------------------------

def without_week(rows, week):
    return [r for r in rows if r["WeekEnding"] != week]


def test_missing_week_is_caught(data):
    acts, cities = data
    problems = checks.check(without_week(acts, "2026-01-10"), without_week(cities, "2026-01-10"))
    assert any("Gap" in p for p in problems)


def test_total_mismatch_is_caught(data):
    acts = copy.deepcopy(data[0])
    row = next(r for r in acts if r["Activity"] == "Hotels" and r["WeekEnding"] == "2026-03-14")
    row["ValueSAR"] = str(int(row["ValueSAR"]) + 50_000)  # one misread digit
    problems = checks.check(acts, data[1])
    assert any("2026-03-14: activity ValueSAR" in p for p in problems)


def test_rounding_limits_fit_the_number_of_parts():
    # Small sums: the true worst case (500 per figure, the total included)
    assert checks.rounding_tolerance(2) == 1_500
    assert checks.rounding_tolerance(3) == 2_000
    # Long sums: well above what rounding has produced so far (6,000 for 61 cities),
    # well below what a misread figure would cause
    assert 9_000 < checks.rounding_tolerance(61) < 15_000
    assert 6_000 < checks.rounding_tolerance(27) < 10_000


def test_missing_city_is_caught(data):
    cities = [r for r in data[1] if not (r["City"] == "Riyadh" and r["WeekEnding"] == "2026-05-02")]
    problems = checks.check(data[0], cities)
    assert any("2026-05-02: expected the same 61 cities" in p for p in problems)


def test_subtotal_mismatch_is_caught(data):
    acts = copy.deepcopy(data[0])
    row = next(r for r in acts if r["Activity"] == "Health" and r["WeekEnding"] == "2025-12-06")
    row["ValueSAR"] = str(int(row["ValueSAR"]) + 5_000)
    assert any("Health ValueSAR parts" in p for p in checks.check(acts, data[1]))


# --- finding new reports on SAMA's page ----------------------------------------

PAGE = """
<a href="/en-US/Indices/POS/Weekly%20POS%2014-11-2026.pdf">14 Nov</a>
<a href='/en-US/Indices/POS/Weekly POS 07-11-2026.pdf'>7 Nov</a>
<a href="https://www.sama.gov.sa/en-US/Indices/POS/Weekly POS 07-11-2026.pdf">dup</a>
<a href="/en-US/Documents/Annual-report.pdf">annual</a>
<script>var x = {"u":"\\/en-US\\/Indices\\/POS\\/Weekly POS 31-10-2026.pdf"};</script>
"""


def test_pdf_links_are_absolute_and_unique():
    links = update.pdf_links(PAGE, "https://www.sama.gov.sa/en-US/Statistics/Indices/Pages/POS.aspx")
    assert links[0] == "https://www.sama.gov.sa/en-US/Indices/POS/Weekly%20POS%2014-11-2026.pdf"
    assert len(links) == 4
    assert all(l.startswith("https://www.sama.gov.sa/") for l in links)


def fake_reports(ends):
    """URL -> four week-ending dates for a fake page listing one report per end date."""
    out = {}
    for end in ends:
        end = dt.date.fromisoformat(end)
        out[f"https://x/{end}.pdf"] = [end - dt.timedelta(days=7 * i) for i in (3, 2, 1, 0)]
    return out


@pytest.mark.parametrize("newest_first", [True, False])
def test_new_reports_found_in_either_page_order(monkeypatch, tmp_path, newest_first):
    ends = ["2026-11-14", "2026-11-07", "2026-10-31", "2026-10-24", "2026-10-17", "2026-10-10", "2026-10-03"]
    reports = fake_reports(ends if newest_first else ends[::-1])
    html = "".join(f'<a href="{u}">r</a>' for u in reports)
    fetched = []

    def get(url, retries=3):
        if url.startswith("https://www.sama.gov.sa"):
            return html.encode()
        fetched.append(url)
        return url.encode()

    monkeypatch.setattr(update, "get", get)
    monkeypatch.setattr(update, "report_weeks", lambda b: reports.get(b.decode()))
    monkeypatch.setattr(update.ex, "REPORTS", tmp_path)
    saved = update.find_new_reports(dt.date(2026, 10, 10))
    names = sorted(p.name for p in saved)
    # every report after 10 Oct is kept; together they cover 17 Oct onwards with no gap
    assert names == [f"POS_{e}.pdf" for e in sorted(ends[:5])]
    assert len(fetched) < len(reports) + 1


def test_nothing_new(monkeypatch, tmp_path):
    reports = fake_reports(["2026-10-10", "2026-10-03", "2026-09-26"])
    html = "".join(f'<a href="{u}">r</a>' for u in reports)
    monkeypatch.setattr(update, "get", lambda url, retries=3: html.encode() if "sama" in url else url.encode())
    monkeypatch.setattr(update, "report_weeks", lambda b: reports.get(b.decode()))
    monkeypatch.setattr(update.ex, "REPORTS", tmp_path)
    assert update.find_new_reports(dt.date(2026, 10, 10)) == []


def test_unreachable_sama_is_fine_while_data_is_fresh(monkeypatch, capsys):
    def refuse(url, retries=3):
        raise ConnectionRefusedError("refused")
    monkeypatch.setattr(update, "get", refuse)
    newest = update.last_week_in_data()
    monkeypatch.setattr(sys, "argv", ["update.py", "--stale-after", "21"])

    class Today(dt.date):
        @classmethod
        def today(cls):
            return newest + dt.timedelta(days=10)
    monkeypatch.setattr(update.dt, "date", Today)
    update.main()  # no exit: data is 10 days old
    assert "up to date" in capsys.readouterr().out

    Today.today = classmethod(lambda cls: newest + dt.timedelta(days=30))
    with pytest.raises(SystemExit) as e:
        update.main()
    assert "30 days old" in str(e.value)


def test_links_in_samas_sharepoint_json():
    # The live page (Nov 2026) lists reports in a JSON blob, with "/" written as /
    page = r'''"FileRef": "/en-US/Statistics/Indices/POS_EN/Weekly_Points_of_Sale_Transactions_As_of_14Nov2026.pdf",
"FileLeafRef": "Weekly_Points_of_Sale_Transactions_As_of_14Nov2026.pdf",
"FileRef": "/en-US/Statistics/Indices/POS_EN/Weekly_Points_of_Sale_Transactions_Report_07_Nov_2026.pdf",'''
    links = update.pdf_links(page, update.PAGES[0])
    assert links == [
        "https://www.sama.gov.sa/en-US/Statistics/Indices/POS_EN/Weekly_Points_of_Sale_Transactions_As_of_14Nov2026.pdf",
        "https://www.sama.gov.sa/en-US/Statistics/Indices/POS_EN/Weekly_Points_of_Sale_Transactions_Report_07_Nov_2026.pdf",
    ]
