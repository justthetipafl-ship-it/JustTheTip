name: Fetch WC Squads

# This workflow is manual / on-demand. Run it once to populate
# worldcup_squads_2026.json. Re-run with the `force` input set to true
# to refresh all teams (e.g. after coaches finalise their 26-man lists).
on:
  workflow_dispatch:
    inputs:
      force:
        description: 'Force refresh ALL teams (not just missing ones)'
        required: false
        default: 'false'
        type: choice
        options:
          - 'false'
          - 'true'

jobs:
  fetch-squads:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi

      - name: Fetch all WC squads
        env:
          API_FOOTBALL_KEY: ${{ secrets.API_FOOTBALL_KEY }}
        run: |
          if [ "${{ github.event.inputs.force }}" = "true" ]; then
            python fetch_wc_squads.py --force
          else
            python fetch_wc_squads.py
          fi

      - name: Commit squads
        run: |
          git config user.email "actions@github.com"
          git config user.name  "github-actions[bot]"
          git add wc/data/worldcup_squads_2026.json
          if git diff --staged --quiet; then
            echo "No changes."
          else
            git commit -m "Refresh WC 2026 squads $(date -u +'%Y-%m-%d %H:%M UTC')"
            git push
          fi
