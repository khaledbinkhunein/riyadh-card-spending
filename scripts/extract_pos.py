"""Extract SAMA weekly Points of Sale reports into tidy CSVs.

Usage (from the project folder):
    python scripts/extract_pos.py                  rebuild data/ from every PDF in reports/
    python scripts/extract_pos.py --keep-existing  add the PDFs in reports/ on top of the current CSVs

Reads PDFs named POS_YYYY-MM-DD.pdf (after the last week each covers), pulls
Table 1 (activities) and Tables 2.1-2.2 (cities), and writes
data/activities.csv and data/cities.csv with one row per name per week.
Each report repeats the previous four weeks; when weeks overlap, the newest
report's figures win (SAMA sometimes revises). Nothing is written unless every
week passes the checks in scripts/checks.py.
"""
import argparse
import csv
import datetime as dt
import re
import sys
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checks import check  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DATA = ROOT / "data"

NUM = r"[\d,]+"
PCT = r"-?[\d.]+|-|—"
ACT_ROW = re.compile(rf"^((?:{NUM} ){{7}}{NUM}) ({PCT}) ({PCT})$")
CITY_ROW = re.compile(rf"^(.*?)\s*((?:{NUM} ){{7}}{NUM}) ({PCT}) ({PCT})$")
WEEK = re.compile(r"(\d{1,2}) ([A-Za-z]{3}),(\d{2})")
ASCII_NAME = re.compile(r"^[A-Za-z][A-Za-z'\-\. &]*")

# Rows on page 1 that are subtotals of the rows beneath them, or the grand total.
SUBTOTALS = {"Transportation", "Health", "Recreation & Culture"}
CATEGORY = {
    "Airlines": "Transportation",
    "Auto & Equipment Rentals": "Transportation",
    "Trade of Vehicles & Spare Parts": "Transportation",
    "Maintenance & Repair of Vehicles": "Transportation",
    "Freight Transport & Postal & Courier Services": "Transportation",
    "Transport - Others": "Transportation",
    "Medical Services": "Health",
    "Pharmacies & Medical Supplies": "Health",
    "Personal Care": "Health",
    "Books & Stationery": "Recreation & Culture",
    "Recreation": "Recreation & Culture",
}


def to_int(s):
    return int(s.replace(",", ""))


def week_endings(text):
    """The four week-ending dates in the table header, oldest first."""
    header = next(l for l in text.splitlines() if len(WEEK.findall(l)) == 8)
    ends = WEEK.findall(header)[1::2]
    return [dt.datetime.strptime(" ".join(e), "%d %b %y").date() for e in ends]


def rows_from_numbers(name, nums, weeks):
    vals = [to_int(n) for n in nums.split()]
    for i, wk in enumerate(weeks):
        # Report figures are in thousands
        yield name, wk, vals[2 * i] * 1000, vals[2 * i + 1] * 1000


def parse_activities(text, weeks):
    lines = [l.strip() for l in text.splitlines()]
    out, seen_transport_others = [], False
    for i, line in enumerate(lines):
        m = ACT_ROW.match(line)
        if not m:
            continue
        label = lines[i + 1].strip()
        if label == "Others" and not seen_transport_others:
            label, seen_transport_others = "Transport - Others", True
        out.extend(rows_from_numbers(label, m.group(1), weeks))
    return out


def parse_cities(text, weeks):
    lines = [l.strip() for l in text.splitlines()]
    out = []
    for i, line in enumerate(lines):
        m = CITY_ROW.match(line)
        if not m:
            continue
        name = ASCII_NAME.match(m.group(1) or "")
        name = name.group(0).strip() if name else ""
        if not name or name[0].islower():
            # Name wrapped: the start of it sits at the end of the previous line
            prev = re.findall(r"[A-Za-z][A-Za-z'\-\.]*$", lines[i - 1])
            name = (prev[0] if prev else "") + name
        out.extend(rows_from_numbers(name, m.group(2), weeks))
    return out


ACT_COLS = ["Activity", "Category", "Level", "WeekEnding", "Transactions", "ValueSAR"]
CITY_COLS = ["City", "WeekEnding", "Transactions", "ValueSAR"]


def parse_report(pdf):
    """Return (weeks, activity rows, city rows) for one SAMA PDF; raise if it looks wrong."""
    with pdfplumber.open(pdf) as doc:
        pages = [p.extract_text() or "" for p in doc.pages]
    weeks = week_endings(pages[0])
    a = parse_activities(pages[0], weeks)
    c = parse_cities(pages[1], weeks) + parse_cities(pages[2], weeks)
    n_act, n_city = len(a) // 4, len(c) // 4
    print(f"{Path(pdf).name}: weeks {weeks[0]}..{weeks[-1]}, {n_act} activity rows, {n_city} city rows")
    if n_act != 31 or n_city != 61:
        raise ValueError(f"{Path(pdf).name}: expected 31 activity rows and 61 city rows")
    return weeks, a, c


def to_dicts(acts, cities):
    act_rows = []
    for (name, wk), (_, _, n, v) in sorted(acts.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        level = "Total" if name == "Total" else "Subtotal" if name in SUBTOTALS else "Line"
        act_rows.append(dict(zip(ACT_COLS, [name, CATEGORY.get(name, name), level, wk.isoformat(), n, v])))
    city_rows = [dict(zip(CITY_COLS, [name, wk.isoformat(), n, v]))
                 for (name, wk), (_, _, n, v) in sorted(cities.items(), key=lambda kv: (kv[0][1], kv[0][0]))]
    return act_rows, city_rows


def read_existing():
    """The current CSVs as {(name, week): row tuple}, in the same shape the parser yields."""
    acts, cities = {}, {}
    if (DATA / "activities.csv").exists():
        for r in csv.DictReader(open(DATA / "activities.csv", encoding="utf-8")):
            wk = dt.date.fromisoformat(r["WeekEnding"])
            acts[(r["Activity"], wk)] = (r["Activity"], wk, int(r["Transactions"]), int(r["ValueSAR"]))
        for r in csv.DictReader(open(DATA / "cities.csv", encoding="utf-8")):
            wk = dt.date.fromisoformat(r["WeekEnding"])
            cities[(r["City"], wk)] = (r["City"], wk, int(r["Transactions"]), int(r["ValueSAR"]))
    return acts, cities


def build(pdfs, keep_existing=False):
    """Combine reports (newest wins) and, optionally, the current CSVs underneath them."""
    acts, cities = {}, {}
    for pdf in sorted(pdfs, key=lambda p: Path(p).name, reverse=True):  # newest first
        _, a, c = parse_report(pdf)
        for r in a:
            acts.setdefault((r[0], r[1]), r)
        for r in c:
            cities.setdefault((r[0], r[1]), r)
    if keep_existing:
        old_acts, old_cities = read_existing()
        for k, r in old_acts.items():
            acts.setdefault(k, r)
        for k, r in old_cities.items():
            cities.setdefault(k, r)
    return to_dicts(acts, cities)


def write(act_rows, city_rows):
    DATA.mkdir(exist_ok=True)
    for name, cols, rows in (("activities.csv", ACT_COLS, act_rows), ("cities.csv", CITY_COLS, city_rows)):
        with open(DATA / name, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows([r[c] for c in cols] for r in rows)
    print(f"Wrote {len(act_rows)} activity rows and {len(city_rows)} city rows to {DATA}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep-existing", action="store_true",
                    help="keep weeks already in data/ that the PDFs don't cover")
    args = ap.parse_args()
    pdfs = sorted(REPORTS.glob("POS_*.pdf"))
    if not pdfs:
        sys.exit(f"No reports found in {REPORTS}")
    act_rows, city_rows = build(pdfs, keep_existing=args.keep_existing)
    problems = check(act_rows, city_rows)
    if problems:
        print("Checks failed; data/ was not changed:", *problems, sep="\n  ")
        sys.exit(1)
    write(act_rows, city_rows)


if __name__ == "__main__":
    main()
