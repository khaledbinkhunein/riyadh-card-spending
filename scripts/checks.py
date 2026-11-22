"""Data checks shared by the update script and the tests.

check(activities, cities) returns a list of problems (empty when the data is
good). Both arguments are lists of dicts with the same columns as the CSVs.
"""
import datetime as dt
import math
from collections import defaultdict

ACTIVITY_ROWS = 31           # 27 lines + 3 subtotals + Total
CITY_ROWS = 61               # including "Others"
# A week-on-week swing outside this range almost certainly means a parsing error
# (the first 56 weeks ranged from -18% to +38%, Eid weeks included).
MAX_DROP, MAX_RISE = -0.50, 1.00


def rounding_tolerance(n_parts):
    """The largest gap that rounding alone can plausibly cause when n_parts figures
    are added up and compared with a printed total.

    SAMA prints every figure rounded to the nearest thousand, so each one (the
    total included) is off by up to 500. The gap can never exceed 500 per figure;
    for long sums, such a worst case is practically impossible, so the limit is
    five times the typical spread of the gap instead (a chance of under one in a
    million per check). A misread number is normally off by far more than this.
    """
    figures = n_parts + 1
    worst_case = 500 * figures
    five_spreads = 5 * (1000 / math.sqrt(12)) * math.sqrt(figures)
    return min(worst_case, five_spreads)


def _by_week(rows):
    weeks = defaultdict(list)
    for r in rows:
        weeks[r["WeekEnding"]].append(r)
    return dict(sorted(weeks.items()))


def check(activities, cities):
    problems = []
    acts, cits = _by_week(activities), _by_week(cities)
    if not acts:
        return ["No activity rows"]
    if list(acts) != list(cits):
        problems.append("Activities and cities cover different weeks")

    dates = [dt.date.fromisoformat(w) for w in acts]
    for a, b in zip(dates, dates[1:]):
        if (b - a).days != 7:
            problems.append(f"Gap or uneven step between weeks {a} and {b}")
    if any(d.weekday() != 5 for d in dates):
        problems.append("Some week-ending dates are not Saturdays")

    first_acts = {r["Activity"] for r in next(iter(acts.values()))}
    first_cities = {r["City"] for r in next(iter(cits.values()))}
    prev_total = None
    for week, a in acts.items():
        c = cits.get(week, [])
        if len(a) != ACTIVITY_ROWS or {r["Activity"] for r in a} != first_acts:
            problems.append(f"{week}: expected the same {ACTIVITY_ROWS} activities as week 1, got {len(a)}")
        if len(c) != CITY_ROWS or {r["City"] for r in c} != first_cities:
            problems.append(f"{week}: expected the same {CITY_ROWS} cities as week 1, got {len(c)}")
        totals = [r for r in a if r["Level"] == "Total"]
        if len(totals) != 1:
            problems.append(f"{week}: expected one Total row")
            continue
        total = totals[0]
        for field in ("ValueSAR", "Transactions"):
            t = int(total[field])
            if t <= 0:
                problems.append(f"{week}: {field} total is not positive")
            line_rows = [r for r in a if r["Level"] == "Line"]
            lines = sum(int(r[field]) for r in line_rows)
            if abs(lines - t) > rounding_tolerance(len(line_rows)):
                problems.append(f"{week}: activity {field} sums to {lines:,}, SAMA total is {t:,}")
            city_sum = sum(int(r[field]) for r in c)
            if c and abs(city_sum - t) > rounding_tolerance(len(c)):
                problems.append(f"{week}: city {field} sums to {city_sum:,}, SAMA total is {t:,}")
            for sub in (r for r in a if r["Level"] == "Subtotal"):
                part_rows = [r for r in line_rows if r["Category"] == sub["Activity"]]
                parts = sum(int(r[field]) for r in part_rows)
                if abs(parts - int(sub[field])) > rounding_tolerance(len(part_rows)):
                    problems.append(f"{week}: {sub['Activity']} {field} parts sum to {parts:,}, subtotal is {int(sub[field]):,}")
        if any(int(r[f]) < 0 for r in a + c for f in ("ValueSAR", "Transactions")):
            problems.append(f"{week}: negative figures")
        value = int(total["ValueSAR"])
        if prev_total:
            change = value / prev_total - 1
            if not MAX_DROP <= change <= MAX_RISE:
                problems.append(f"{week}: total spending changed {change:+.0%} on the week before")
        prev_total = value
    return problems
