name: FPL Data

on:
  workflow_dispatch:        # run manually from the Actions tab (use this first to test)
  schedule:
    - cron: '0 6 * * *'     # daily 06:00 UTC (~4pm AEST) — bump frequency near matchdays later

permissions:
  contents: write

concurrency:
  group: fpl-data
  cancel-in-progress: false

jobs:
  fpl:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Pull FPL data
        run: python EPL/fetch_fpl.py

      - name: Commit data
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add EPL/data/*.json
          if git diff --staged --quiet; then
            echo "No changes."
          else
            git commit -m "FPL data $(date -u +%Y-%m-%dT%H:%MZ)"
            git push
          fi
