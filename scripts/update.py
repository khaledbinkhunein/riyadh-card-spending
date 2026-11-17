"""Weekly update: fetch new SAMA Points of Sale reports, extract them, check, write CSVs.

Usage (from the project folder):
    python scripts/update.py                 look on SAMA's page for reports newer than data/
    python scripts/update.py --pdf FILE_OR_URL [...]   use these reports instead of the page

Steps:
 1. Read the newest week already in data/cities.csv.
 2. Collect the PDF links on SAMA's POS page, download them one by one and keep
    every weekly POS report that covers a week after that date. Reports are
    recognised by their contents (the four week-ending dates in the table
    header), not by file name, so a change in SAMA's naming does not break this.
 3. Save each as reports/POS_<last week>.pdf, add them on top of the current
    CSVs (newest report wins on overlapping weeks) and run scripts/checks.py.
 4. Write data/ only if every check passes, including "no missing weeks".

Exit codes: 0 = updated or nothing new yet, 1 = checks failed or SAMA unreachable
(with --stale-after DAYS, an unreachable SAMA only fails once the data is that old).
Note: sama.gov.sa refuses connections from outside Saudi Arabia, so run this
from a Saudi internet connection (or a runner that has one).
"""
import argparse
import csv
import datetime as dt
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_pos as ex  # noqa: E402
from checks import check  # noqa: E402

PAGES = [
    "https://www.sama.gov.sa/en-US/Statistics/Indices/Pages/POS.aspx",
    "https://www.sama.gov.sa/ar-sa/Statistics/Indices/Pages/POS.aspx",
]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) riyadh-card-spending weekly update"
MAX_DOWNLOADS = 25        # never fetch more than this many PDFs in one run
TIMEOUT = 60


def get(url, retries=3):
    req = urllib.request.Request(quote(url, safe=":/?&=%#+,;~@!$'()*"), headers={"User-Agent": UA})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == retries:
                raise
            print(f"  {url}: {e}; retrying")
            time.sleep(10 * attempt)


class SamaUnreachable(Exception):
    pass


def pdf_links(html, base):
    """Every distinct .pdf link in the page, in page order, as absolute URLs."""
    found = re.findall(r"""(?:href|src)\s*=\s*["']([^"']+?\.pdf(?:\?[^"']*)?)["']""", html, re.I)
    # SAMA's SharePoint page lists the reports in a JSON blob ("FileRef": "/en-US/...pdf")
    html = html.replace("\\u002f", "/").replace("\\u002F", "/")
    found += re.findall(r"""["'](\\?/[^"'<>]+?\.pdf)["']""", html, re.I)
    out = []
    for link in found:
        url = urljoin(base, unescape(link).replace("\\/", "/"))
        if url not in out:
            out.append(url)
    return out


def last_week_in_data():
    path = ex.DATA / "cities.csv"
    if not path.exists():
        return None
    return max(dt.date.fromisoformat(r["WeekEnding"]) for r in csv.DictReader(open(path, encoding="utf-8")))


def report_weeks(pdf_bytes):
    """The four week-ending dates of a SAMA POS report, or None if it isn't one."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp = f.name
    try:
        with ex.pdfplumber.open(tmp) as doc:
            text = doc.pages[0].extract_text() or ""
        return ex.week_endings(text)
    except Exception:
        return None
    finally:
        Path(tmp).unlink(missing_ok=True)


def save(pdf_bytes, weeks):
    ex.REPORTS.mkdir(exist_ok=True)
    path = ex.REPORTS / f"POS_{weeks[-1].isoformat()}.pdf"
    path.write_bytes(pdf_bytes)
    print(f"  saved {path.name} (weeks {weeks[0]}..{weeks[-1]})")
    return path


def find_new_reports(after):
    """Download POS reports from SAMA's page that cover weeks after `after`."""
    html, base = None, None
    for page in PAGES:
        try:
            html, base = get(page).decode("utf-8", "replace"), page
            break
        except Exception as e:
            print(f"Could not open {page}: {e}")
    if html is None:
        raise SamaUnreachable("SAMA's POS page could not be reached (sama.gov.sa only answers Saudi connections)")
    links = pdf_links(html, base)
    print(f"{len(links)} PDF link(s) on {base}")
    new, seen, downloads = {}, [], 0
    while links and downloads < MAX_DOWNLOADS:
        url = links.pop(0)
        downloads += 1
        try:
            data = get(url)
        except Exception as e:
            print(f"  skipped {url}: {e}")
            continue
        weeks = report_weeks(data)
        if not weeks:
            continue  # some other PDF on the page
        seen.append(weeks[-1])
        if after is None or weeks[-1] > after:
            new.setdefault(weeks[-1], (data, weeks))
        if len(seen) == 2 and seen[1] > seen[0]:
            links.reverse()  # the page lists oldest first: continue from the newest end
            continue
        covered = new and min(w[0] for _, w in new.values()) <= after + dt.timedelta(days=7)
        if len(seen) >= 2 and weeks[-1] <= after and (covered or not new):
            break  # reached reports we already have
    return [save(d, w) for d, w in new.values()]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", nargs="+", help="local paths or URLs of SAMA POS reports to add")
    ap.add_argument("--stale-after", type=int, metavar="DAYS",
                    help="if SAMA can't be reached, succeed anyway while the newest week is at most "
                         "DAYS old (another machine keeps it updated); fail once it is older")
    args = ap.parse_args()

    after = last_week_in_data()
    print(f"Data currently runs to week ending {after}")
    if args.pdf:
        paths = []
        for src in args.pdf:
            data = get(src) if re.match(r"https?://", src) else Path(src).read_bytes()
            weeks = report_weeks(data)
            if not weeks:
                sys.exit(f"{src} is not a SAMA weekly POS report")
            paths.append(save(data, weeks))
    else:
        try:
            paths = find_new_reports(after)
        except SamaUnreachable as e:
            age = (dt.date.today() - after).days if after else None
            if args.stale_after and age is not None and age <= args.stale_after:
                print(f"{e}.\nThe newest week is {age} days old (limit {args.stale_after}), so the data is up to date.")
                return
            sys.exit(f"{e}, and the newest week is {age} days old. Run the update from a Saudi connection.")
    paths = [p for p in paths if after is None or dt.date.fromisoformat(p.stem[4:]) > after]
    if not paths:
        print("No new SAMA report yet; nothing to do.")
        return

    act_rows, city_rows = ex.build(paths, keep_existing=True)
    problems = check(act_rows, city_rows)
    if problems:
        print("Checks failed; data/ was not changed:", *problems, sep="\n  ")
        sys.exit(1)
    ex.write(act_rows, city_rows)
    print(f"Data now runs to week ending {max(r['WeekEnding'] for r in city_rows)}")


if __name__ == "__main__":
    main()
