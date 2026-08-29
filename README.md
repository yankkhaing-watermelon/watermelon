# Bursa MusangKing — V5.1 Balanced PWA Recovery

This repository restores the original `BursaMusangKing-app2` phone PWA and
embeds the latest V5.1 Balanced screening engine. It does **not** clone or
depend on the suspended GitHub account.

## What is recovered

- Installable PWA with light/dark mode and offline shell
- All six V5.1 screeners: Trending, Early Uptrend, Reversal, Gaining Momentum,
  Base Breakout, and META Technical Leader
- Full-market Bursa scan, V5.1 priority scores, WATCH status, entry range,
  suggested stop, candlestick details, scan date, and diagnostics
- Weekly signal-review and historical backtest tabs remain compatible with the
  existing Cloudflare KV payloads
- Manual **Run scan now** button and weekday scheduled scan
- Existing Worker/KV API contract: `latest`, `history`, `weekly`, `backtest`

## Architecture

1. GitHub Actions downloads the Bursa universe and daily price history.
2. The embedded `bmk_screener_v3.py` screens with V5.1 Balanced rules.
3. `export_scan.py` publishes `latest.json` and `history.json` to your Worker.
4. The Worker stores the four JSON payloads in your existing `SCANS` KV.
5. The static PWA reads the Worker API.

The scanner fails closed if fewer than 800 Bursa symbols have usable history;
it will not publish a misleading partial-market result.

## Recover using your existing Cloudflare Worker and KV

The supplied `app/config.js` already points to:

`https://bursamusangking-app.yankhaing.workers.dev`

If that Worker still returns `{"ok":true,"keys":[...]}` (or valid `/latest`
data), keep it. Do not create a new KV namespace.

### 1. Upload to your new GitHub account

Create a new empty repository, upload all files from this package, and commit to
the `main` branch.

Do not upload `.pytest_cache` or `__pycache__`; they are temporary test folders.
If `.github` is hidden during manual browser upload, follow
`UPLOAD-TO-GITHUB.txt`. Visible copies of both workflows are provided as
`APP-SCAN-WORKFLOW.yml` and `APP-REVIEW-WORKFLOW.yml`.

### 2. Reconnect the existing Worker to the new repository

In Cloudflare Dashboard, open the existing `bursamusangking-app` Worker:

1. Keep the existing `SCANS` KV binding unchanged.
2. Change `GITHUB_REPO` to `NEW_USERNAME/NEW_REPOSITORY`.
3. Keep `WORKFLOW_FILE` as `app-scan.yml` and `GITHUB_REF` as `main`.
4. Replace `GITHUB_TOKEN` with a fine-grained token from the new GitHub account.
   Give it access only to the new repository, with **Actions: read and write**.
5. Keep the existing `PUBLISH_TOKEN`; you will use the same value in GitHub.
6. `RUN_TOKEN` is optional. Leave it unset for the simplest private-use setup.

If you replace the Worker code from `worker/worker.js`, retain the same KV
binding name: `SCANS`.

### 3. Add GitHub Actions secrets

In the new repository, open **Settings → Secrets and variables → Actions**.

Add these repository secrets:

| Secret | Value |
| --- | --- |
| `WORKER_URL` | `https://bursamusangking-app.yankhaing.workers.dev` |
| `PUBLISH_TOKEN` | The exact existing Worker publish token |

Optional repository variable:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `MIN_UNIVERSE` | `800` | Stops partial scans from replacing valid data |

### 4. Deploy the PWA with Cloudflare Pages

Create or reconnect a Pages project to the new GitHub repository:

- Production branch: `main`
- Framework preset: `None`
- Build command: leave blank
- Build output directory: `app`
- Root directory: `/`

If you want to keep the old Pages address, disconnect its suspended GitHub
repository and connect the new repository. Otherwise, create a new Pages
project and update `ALLOWED_ORIGIN` in the Worker to the new Pages URL.

### 5. Run the first V5.1 scan

Open **GitHub → Actions → App Scan → Run workflow**. A complete run should end
with:

```text
publish latest: 200
publish history: 200
V5.1 exported ... unique candidates from ... stocks
```

Then open the PWA and verify:

- the update time and scan date are current;
- all six screener chips appear;
- a stock can appear under more than one screener;
- tapping a stock opens its chart and V5.1 entry/stop levels.

## Important notes

- The initial PWA can immediately read your existing KV data. The first V5.1
  workflow run replaces only `latest` and `history`; it does not erase stored
  `weekly` or `backtest` payloads.
- Yahoo may not carry every newly listed or unusual Bursa security. The log
  reports the usable count, and the fail-closed threshold protects the PWA from
  a severely incomplete download.
- `RUN_TOKEN` placed in `app/config.js` is visible to visitors. Use it only as a
  casual trigger guard, never as a real secret.
- Candidate ranking is informational and is not financial advice.

## Local checks

```bash
pip install -r requirements.txt
pytest -q tests_screener_v51.py
python -m py_compile export_scan.py data_fetcher.py universe.py signal_log.py
```

Serve the PWA locally with:

```bash
python -m http.server 8899 --directory app
```
