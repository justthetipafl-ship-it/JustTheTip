# ============================================================
# Just The Tip — NRL probe v2 (corrected for nrlR 0.1.2)
#
# Fixes from v1: real function name fetch_cd_comps() (no args).
# Tests BOTH log sources (championdata + rugbyproject) and dumps the
# actual stat-column inventory — the thing the v3 report didn't show.
#
# Usage:
#   Rscript probe_nrl_v2.R           # current season, round 1
#   Rscript probe_nrl_v2.R 2026 5    # season + round
# ============================================================

need <- c("nrlR", "jsonlite", "dplyr")
for (p in need) if (!requireNamespace(p, quietly = TRUE)) install.packages(p)
suppressPackageStartupMessages({ library(nrlR); library(jsonlite); library(dplyr) })

args   <- commandArgs(trailingOnly = TRUE)
SEASON <- if (length(args) >= 1) as.integer(args[1]) else as.integer(format(Sys.Date(), "%Y"))
ROUND  <- if (length(args) >= 2) as.integer(args[2]) else 1L

schema <- function(df, label) {
  cat(sprintf("\n--- SCHEMA: %s ---\n", label))
  if (is.null(df) || !is.data.frame(df) || nrow(df) == 0) { cat("  (empty / NULL)\n"); return(invisible()) }
  cat(sprintf("  %d rows x %d cols\n", nrow(df), ncol(df)))
  for (nm in names(df)) {
    v <- df[[nm]]; ex <- suppressWarnings(head(v[!is.na(v)], 1))
    ex <- if (length(ex)) as.character(ex)[1] else "NA"
    if (nchar(ex) > 40) ex <- paste0(substr(ex, 1, 40), "...")
    cat(sprintf("    %-26s %-10s e.g. %s\n", nm, class(v)[1], ex))
  }
}

cat("============================================================\n")
cat(sprintf("JTT NRL probe v2 — nrlR %s — season %d round %d\n",
            as.character(utils::packageVersion("nrlR")), SEASON, ROUND))
cat("============================================================\n")

# ---- 0. what actually exists in the package ----
cat("\n0. nrlR exports (so we stop guessing fetch_results / fetch_ladder):\n")
print(ls("package:nrlR"))

# ---- 1. THE BLOCKER: fetch_cd_comps() bare ----
cat("\n1. fetch_cd_comps()  [no args]\n")
comps <- tryCatch(fetch_cd_comps(), error = function(e){ cat("  ERROR:", conditionMessage(e), "\n"); NULL })
schema(comps, "competitions")
NRL_COMP_ID <- NA
if (!is.null(comps) && nrow(comps) > 0) {
  nm <- intersect(c("name","competitionName","competition"), names(comps))[1]
  idc <- intersect(c("id","competitionId"), names(comps))[1]
  if (!is.na(nm) && !is.na(idc)) {
    hit <- comps[grepl("premiership|telstra|nrl", comps[[nm]], ignore.case = TRUE), ]
    if (nrow(hit)) { NRL_COMP_ID <- hit[[idc]][1]; cat(sprintf("  -> NRL comp '%s' id=%s\n", hit[[nm]][1], NRL_COMP_ID)) }
    cat("  (all comp names for reference:)\n"); print(comps[[nm]])
  }
} else {
  cat("  *** CD comps empty — championdata log path will fail. rugbyproject is the fallback. ***\n")
}

# ---- 2a. CHAMPION DATA logs (only if comps resolved) ----
cat("\n2a. fetch_player_stats(source='championdata')\n")
cd_logs <- NULL
if (!is.na(NRL_COMP_ID)) {
  cd_logs <- tryCatch(fetch_player_stats(comp = NRL_COMP_ID, round = ROUND, source = "championdata"),
                      error = function(e){ cat("  ERROR:", conditionMessage(e), "\n"); NULL })
} else cat("  skipped — no comp id\n")
schema(cd_logs, "championdata player logs")

# ---- 2b. RUGBY LEAGUE PROJECT logs (the non-CD fallback) ----
cat("\n2b. fetch_player_stats(source='rugbyproject')\n")
rlp_logs <- tryCatch(fetch_player_stats(season = SEASON, league = "nrl", round = ROUND, source = "rugbyproject"),
                     error = function(e){ cat("  ERROR:", conditionMessage(e), "\n"); NULL })
schema(rlp_logs, "rugbyproject player logs")

# ---- 3. write whichever logs returned, for inspection ----
cat("\n3. writing sample JSON (whichever source returned rows)\n")
chosen <- if (!is.null(cd_logs) && nrow(cd_logs) > 0) cd_logs else rlp_logs
src    <- if (!is.null(cd_logs) && nrow(cd_logs) > 0) "championdata" else "rugbyproject"
if (!is.null(chosen) && nrow(chosen) > 0) {
  write_json(chosen, "nrl_player_logs_sample.json", dataframe = "rows", auto_unbox = TRUE, na = "null")
  cat(sprintf("  wrote nrl_player_logs_sample.json from '%s' (%d rows)\n", src, nrow(chosen)))
} else cat("  NEITHER source returned rows — paste the errors above.\n")

cat("\nDONE. Paste sections 0, 1, 2a, 2b back.\n")
cat("Section 2a vs 2b decides the log source; the column lists decide the signal/DVP build.\n")
