# Weekly update from this PC (needs a Saudi internet connection: turn any VPN off).
# Pulls the repo, fetches new SAMA reports, checks them and pushes the new CSVs.
# GitHub then re-runs the tests on the push and emails if anything fails.
#
# Run by hand:   powershell -ExecutionPolicy Bypass -File scripts\run_weekly.ps1
# Schedule it:   see "Keeping it updated" in README.md

# Native tools (git, python) are checked through $LASTEXITCODE; their stderr is not an error.
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$log = Join-Path $repo "reports\last-run.log"
Start-Transcript -Path $log -Force | Out-Null
try {
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "git pull failed" }

    $py = Join-Path $repo ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        python -m venv .venv
        & $py -m pip install --quiet -r scripts\requirements.txt
    }

    & $py scripts\update.py
    if ($LASTEXITCODE -ne 0) { throw "update.py failed (exit $LASTEXITCODE)" }

    git add data/
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Output "No data changes to commit."
    } else {
        $last = (Get-Content data\cities.csv -Tail 1).Split(",")[1]
        git -c user.name="khaledbinkhunein" -c user.email="343924651+khaledbinkhunein@users.noreply.github.com" commit -m "Data through week ending $last"
        git push
        if ($LASTEXITCODE -ne 0) { throw "git push failed" }
    }
} finally {
    Stop-Transcript | Out-Null
}
