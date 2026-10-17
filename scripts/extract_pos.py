"""Extract SAMA weekly Points of Sale reports into tidy CSVs.

Usage:  python scripts/extract_pos.py            (from the project folder)

Reads every PDF in reports/ (named POS_YYYY-MM-DD.pdf after the last week it
covers), pulls Table 1 (activities) and Tables 2.1-2.2 (cities), and writes
data/activities.csv and data/cities.csv with one row per name per week.
Each report repeats the previous four weeks; when weeks overlap, the newest
report's figures win (SAMA sometimes revises).
"""
import csv
import datetime as dt
import re
import sys
from pathlib import Path

import pdfplumber

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


def main():
    pdfs = sorted(REPORTS.glob("POS_*.pdf"), reverse=True)  # newest first
    if not pdfs:
        sys.exit(f"No reports found in {REPORTS}")
    acts, cities = {}, {}
    for pdf in pdfs:
        with pdfplumber.open(pdf) as doc:
            pages = [p.extract_text() for p in doc.pages]
        weeks = week_endings(pages[0])
        a = parse_activities(pages[0], weeks)
        c = parse_cities(pages[1], weeks) + parse_cities(pages[2], weeks)
        n_act, n_city = len(a) // 4, len(c) // 4
        print(f"{pdf.name}: weeks {weeks[0]}..{weeks[-1]}, {n_act} activity rows, {n_city} city rows")
        if n_act != 31 or n_city != 61:
            print(f"  WARNING: expected 31 activity rows and 61 city rows")
        for r in a:
            acts.setdefault((r[0], r[1]), r)
        for r in c:
            cities.setdefault((r[0], r[1]), r)

    DATA.mkdir(exist_ok=True)
    with open(DATA / "activities.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Activity", "Category", "Level", "WeekEnding", "Transactions", "ValueSAR"])
        for (name, wk), (_, _, n, v) in sorted(acts.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            level = "Total" if name == "Total" else "Subtotal" if name in SUBTOTALS else "Line"
            w.writerow([name, CATEGORY.get(name, name), level, wk.isoformat(), n, v])
    with open(DATA / "cities.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["City", "WeekEnding", "Transactions", "ValueSAR"])
        for (name, wk), (_, _, n, v) in sorted(cities.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            w.writerow([name, wk.isoformat(), n, v])
    print(f"Wrote {len(acts)} activity rows and {len(cities)} city rows to {DATA}")


if __name__ == "__main__":
    main()
