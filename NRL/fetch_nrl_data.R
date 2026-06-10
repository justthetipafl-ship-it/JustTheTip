# ============================================================
# Just The Tip — NRL production fetcher
# Source: Champion Data via nrlR. Blocker resolved (fetch_cd_comps works).
#
# Usage: Rscript fetch_nrl_data.R 2026
#
# Writes to working dir:
#   nrl_player_logs.json   (workhorse — per player per game, 63 stat cols)
#   nrl_team_stats.json    (team aggregates — tier / style / DVP)
#   nrl_fixtures.json      (match picker)            [best-effort]
#   nrl_ladder.json        (ladder)                  [best-effort]
#   nrl_injuries.json      (availability/rep-duty)   [best-effort]
#   version.txt
# ============================================================

suppressPackageStartupMessages({ library(nrlR); library(jsonlite); library(dplyr) })

args   <- commandArgs(trailingOnly = TRUE)
SEASON <- if (length(args) >= 1) as.integer(args[1]) else as.integer(format(Sys.Date(), "%Y"))
TIMEOUT <- 180

ts <- function(msg) { cat(sprintf("[%s] %s\n", format(Sys.time(), "%H:%M:%S"), msg)); flush(stdout()) }
timed <- function(label, expr, secs = TIMEOUT) {
  ts(sprintf("START  %s", label)); t0 <- Sys.time()
  on.exit(setTimeLimit(elapsed = Inf), add = TRUE)
  setTimeLimit(elapsed = secs, transient = TRUE)
  res <- tryCatch(force(expr), error = function(e) { ts(sprintf("  !! %s FAILED: %s", label, conditionMessage(e))); NULL })
  ts(sprintf("DONE   %s  (%.1fs, %s rows)", label,
             as.numeric(difftime(Sys.time(), t0, units = "secs")),
             if (is.data.frame(res)) nrow(res) else "n/a"))
  res
}
wrote <- function(df, path) {
  if (is.null(df) || !is.data.frame(df) || nrow(df) == 0) { ts(sprintf("  [skip] %s — empty", path)); return(invisible(FALSE)) }
  write_json(df, path, dataframe = "rows", auto_unbox = TRUE, na = "null")
  ts(sprintf("  [ok]   %s (%d rows)", path, nrow(df))); invisible(TRUE)
}

ts(sprintf("JTT NRL fetcher | nrlR %s | season %d", as.character(utils::packageVersion("nrlR")), SEASON))

# ---- 1. resolve the CURRENT-SEASON NRL Premiership comp id ----
comps <- timed("fetch_cd_comps()", fetch_cd_comps())
if (is.null(comps) || nrow(comps) == 0) { ts("FATAL: no competitions returned."); quit(status = 1) }

target <- paste0(SEASON, " Telstra NRL Premiership")
row <- comps[comps$name == target, , drop = FALSE]
if (nrow(row) == 0) {
  # fallback: starts with season, contains NRL Premiership, not Finals/NRLW/Womens/Origin
  row <- comps[grepl(paste0("^", SEASON, ".*NRL Premiership$"), comps$name) &
               !grepl("Finals|NRLW|Women|Origin", comps$name, ignore.case = TRUE), , drop = FALSE]
}
if (nrow(row) == 0) { ts(sprintf("FATAL: could not resolve '%s'. Check comp names.", target)); quit(status = 1) }
NRL_COMP_ID <- row$id[1]
ts(sprintf("Resolved comp: '%s' -> id %s", row$name[1], NRL_COMP_ID))

# ---- 2. player game logs (the workhorse) — full season, all completed rounds ----
logs <- timed("player logs", fetch_player_stats(comp = NRL_COMP_ID, source = "championdata"))
if (!is.null(logs) && nrow(logs) > 0) {
  if (all(c("firstname","surname") %in% names(logs)))
    logs$player <- trimws(paste(logs$firstname, logs$surname))
}
wrote(logs, "nrl_player_logs.json")

# ---- 3. team stats (tier / style / DVP) ----
team <- timed("team stats", fetch_team_stats_championdata(comp = NRL_COMP_ID))
wrote(team, "nrl_team_stats.json")

# ---- 4. fixture (match picker) — best-effort, signature unknown so try forms ----
fixture <- timed("fixture", {
  out <- tryCatch(fetch_fixture_nrl(season = SEASON), error = function(e) NULL)
  if (is.null(out)) out <- tryCatch(fetch_fixture(season = SEASON, league = "nrl"), error = function(e) NULL)
  out
})
wrote(fixture, "nrl_fixtures.json")

# ---- 5. ladder — best-effort ----
ladder <- timed("ladder", {
  out <- tryCatch(fetch_ladder_nrl(season = SEASON), error = function(e) NULL)
  if (is.null(out)) out <- tryCatch(fetch_ladder(season = SEASON, league = "nrl"), error = function(e) NULL)
  out
})
wrote(ladder, "nrl_ladder.json")

# ---- 6. injuries / suspensions — availability + rep-duty signal ----
inj <- timed("injuries/suspensions", tryCatch(fetch_injuries_suspensions(), error = function(e) NULL))
wrote(inj, "nrl_injuries.json")

writeLines(format(Sys.time(), "%Y-%m-%d %H:%M"), "version.txt")
ts("FETCH COMPLETE.")
