# ============================================================
# Just The Tip — NRL data fetcher (Phase 1)
# Mirrors the AFL jtt_export.R pattern.
#
# Usage:
#   Rscript fetch_nrl_data.R            # current season
#   Rscript fetch_nrl_data.R 2026       # specific season
#   Rscript fetch_nrl_data.R 2026 1     # season + single round (faster probe)
#
# PRIORITY: this run is diagnostic-first. Before it writes anything it
# proves whether Champion Data is returning rows (the fetch_cd_competitions
# zero-rows blocker). It prints a full schema report at the end — paste
# that back so we can confirm columns before building the HTML.
# ============================================================

# ---- deps ----
need <- c("nrlR", "jsonlite", "dplyr")
for (p in need) {
  if (!requireNamespace(p, quietly = TRUE)) {
    cat(sprintf("Installing %s...\n", p)); install.packages(p)
  }
}
suppressPackageStartupMessages({
  library(nrlR); library(jsonlite); library(dplyr)
})

args   <- commandArgs(trailingOnly = TRUE)
SEASON <- if (length(args) >= 1) as.integer(args[1]) else as.integer(format(Sys.Date(), "%Y"))
ROUND  <- if (length(args) >= 2) as.integer(args[2]) else NA_integer_

cat("============================================================\n")
cat(sprintf("JTT NRL fetcher — season %d%s\n", SEASON,
            if (!is.na(ROUND)) sprintf(", round %d", ROUND) else ""))
cat("============================================================\n\n")

# small helper: write JSON only if we actually have rows
write_json_safe <- function(df, path, label) {
  n <- if (is.data.frame(df)) nrow(df) else length(df)
  if (is.null(df) || n == 0) {
    cat(sprintf("  [SKIP] %s — 0 rows, not written\n", label)); return(invisible(FALSE))
  }
  write_json(df, path, dataframe = "rows", auto_unbox = TRUE, na = "null")
  cat(sprintf("  [OK]   %s -> %s (%d rows)\n", label, path, n)); invisible(TRUE)
}

schema_report <- function(df, label) {
  cat(sprintf("\n--- SCHEMA: %s ---\n", label))
  if (is.null(df) || !is.data.frame(df) || nrow(df) == 0) {
    cat("  (empty / NULL)\n"); return(invisible())
  }
  cat(sprintf("  %d rows x %d cols\n", nrow(df), ncol(df)))
  for (nm in names(df)) {
    v <- df[[nm]]
    ex <- suppressWarnings(head(v[!is.na(v)], 1))
    ex <- if (length(ex)) as.character(ex)[1] else "NA"
    if (nchar(ex) > 40) ex <- paste0(substr(ex, 1, 40), "...")
    cat(sprintf("    %-26s %-10s e.g. %s\n", nm, class(v)[1], ex))
  }
}

# ============================================================
# STEP 1 — THE BLOCKER: prove Champion Data competition discovery
# ============================================================
cat("STEP 1 — fetch_cd_competitions()\n")
comps <- tryCatch(fetch_cd_competitions(),
                  error = function(e) { cat("  ERROR:", conditionMessage(e), "\n"); NULL })

if (is.null(comps) || nrow(comps) == 0) {
  cat("\n  *** BLOCKER CONFIRMED: fetch_cd_competitions() returned 0 rows. ***\n")
  cat("  Champion Data player-stats path is unavailable for this run.\n")
  cat("  Diagnostics to paste back:\n")
  cat("    - nrlR version: ", as.character(utils::packageVersion("nrlR")), "\n")
  cat("    - Available nrlR exports:\n")
  print(ls("package:nrlR"))
  cat("\n  Next: we pivot logs to the beauhobba scraper or RLP + nrl.com.\n")
  cat("  Continuing to fixtures/results/ladder (non-CD) so we still get SOMETHING.\n\n")
  CD_OK <- FALSE
} else {
  cat(sprintf("  CD competitions returned %d rows.\n", nrow(comps)))
  schema_report(comps, "competitions")
  CD_OK <- TRUE
}

# Try to resolve the NRL comp id (Telstra Premiership). Name field unknown
# until we see the schema, so probe the likeliest columns.
NRL_COMP_ID <- NA
if (CD_OK) {
  name_col <- intersect(c("name","competition","compName","competitionName","comp"), names(comps))[1]
  id_col   <- intersect(c("id","compId","competitionId","cdId"), names(comps))[1]
  if (!is.na(name_col) && !is.na(id_col)) {
    hit <- comps[grepl("premiership|telstra|nrl", comps[[name_col]], ignore.case = TRUE), ]
    if (nrow(hit) > 0) {
      NRL_COMP_ID <- hit[[id_col]][1]
      cat(sprintf("\n  Resolved NRL comp: '%s' -> id %s\n",
                  hit[[name_col]][1], as.character(NRL_COMP_ID)))
    } else {
      cat("\n  Could not auto-match NRL by name — inspect the schema above and set NRL_COMP_ID manually.\n")
    }
  } else {
    cat("\n  comp name/id columns not recognised — inspect schema, set columns manually.\n")
  }
}

# ============================================================
# STEP 2 — PLAYER GAME LOGS (the workhorse)
# ============================================================
cat("\nSTEP 2 — player game logs\n")
player_logs <- NULL
if (CD_OK && !is.na(NRL_COMP_ID)) {
  player_logs <- tryCatch({
    if (!is.na(ROUND)) fetch_player_stats_championdata(comp = NRL_COMP_ID, round = ROUND)
    else               fetch_player_stats_championdata(comp = NRL_COMP_ID)
  }, error = function(e) { cat("  ERROR:", conditionMessage(e), "\n"); NULL })
} else {
  cat("  Skipped — no CD comp id. (Resolve blocker before logs are possible.)\n")
}
schema_report(player_logs, "player_logs")

# ============================================================
# STEP 3 — TEAM STATS (tier / style / DVP)
# ============================================================
cat("\nSTEP 3 — team stats\n")
team_stats <- NULL
if (CD_OK && !is.na(NRL_COMP_ID)) {
  team_stats <- tryCatch(fetch_team_stats_championdata(comp = NRL_COMP_ID),
                         error = function(e) { cat("  ERROR:", conditionMessage(e), "\n"); NULL })
}
schema_report(team_stats, "team_stats")

# ============================================================
# STEP 4 — RESULTS (H2H / "last time they met") — non-CD, should work regardless
# ============================================================
cat("\nSTEP 4 — results (Rugby League Project)\n")
results <- tryCatch(fetch_results(seasons = (SEASON-6):SEASON, league = "nrl"),
                    error = function(e) { cat("  ERROR:", conditionMessage(e), "\n"); NULL })
schema_report(results, "results")

# ============================================================
# STEP 5 — LADDER
# ============================================================
cat("\nSTEP 5 — ladder\n")
ladder <- tryCatch(fetch_ladder(season = SEASON, league = "nrl"),
                   error = function(e) {
                     # arg names may differ; retry bare
                     tryCatch(fetch_ladder(), error = function(e2) {
                       cat("  ERROR:", conditionMessage(e2), "\n"); NULL })
                   })
schema_report(ladder, "ladder")

# ============================================================
# WRITE OUTPUTS (only what has rows) — JTT Phase 1 schema
# ============================================================
cat("\n============================================================\n")
cat("WRITING JSON\n")
cat("============================================================\n")
write_json_safe(player_logs, "nrl_player_logs.json",   "player logs")
write_json_safe(team_stats,  "nrl_team_stats.json",    "team stats")
write_json_safe(results,     "nrl_results.json",       "results / H2H")
write_json_safe(ladder,      "nrl_ladder.json",        "ladder")

# version stamp (matches WC/AFL cron pattern)
writeLines(format(Sys.time(), "%Y-%m-%d %H:%M"), "version.txt")

cat("\nDONE.\n")
cat("Paste the full SCHEMA blocks above back into chat — especially player_logs.\n")
cat("That confirms the column inventory (tackle busts vs run metres, kick metres,\n")
cat("1H/2H splits) before any HTML gets built.\n")
