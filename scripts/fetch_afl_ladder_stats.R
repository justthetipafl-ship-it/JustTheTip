#!/usr/bin/env Rscript
# fetch_afl_ladder_stats.R — per-game AFL player stats for the Ladder's grading,
# from the AFL Match Centre (fitzRoy), with a fryzigg fallback. These land the
# morning after each game, so the Ladder can result a rung next-day instead of
# waiting for wheelo's weekly per-round file. Writes AFL/data/ladder_gamelogs.json
# in the game-log shape build_ladder.py reads: Player / Year / RoundName + stats.
# Non-destructive: only overwrites on a successful fetch (atomic temp -> rename).
suppressMessages({ library(fitzRoy); library(jsonlite); library(dplyr) })

num  <- function(x) suppressWarnings(as.numeric(x))
pick <- function(df, candidates) { for (c in candidates) if (c %in% names(df)) return(df[[c]]); rep(NA, nrow(df)) }

SEASON <- as.integer(Sys.getenv("AFL_SEASON", format(Sys.Date(), "%Y")))
OUT    <- Sys.getenv("LADDER_STATS", "AFL/data/ladder_gamelogs.json")
if (!dir.exists("scripts")) dir.create("scripts")

cur <- tryCatch(as.integer(jsonlite::fromJSON("AFL/data/meta.json")$round), error = function(e) NA_integer_)
rounds <- if (!is.na(cur)) unique(c(cur - 1L, cur)) else integer(0)
rounds <- rounds[rounds >= 1L]
if (!length(rounds)) { message("[ladder_stats] no round in meta.json — nothing to do"); quit(status = 0) }
message(sprintf("[ladder_stats] season=%d rounds=%s", SEASON, paste(rounds, collapse = ",")))

build_rows <- function(df, default_round, keep_rounds = NULL) {
  if (is.null(df) || !nrow(df)) return(NULL)
  writeLines(sort(names(df)), "scripts/_afl_playerstats_columns.txt")   # diagnosis: real column names
  rn <- suppressWarnings(as.integer(pick(df, c("round.roundNumber", "roundNumber", "round", "match_round"))))
  rn[is.na(rn)] <- default_round
  if (!is.null(keep_rounds)) { k <- rn %in% keep_rounds; df <- df[k, , drop = FALSE]; rn <- rn[k] }
  if (!nrow(df)) return(NULL)
  given <- pick(df, c("player.player.player.givenName","player.givenName","player.playerName.givenName","givenName","player_first_name"))
  surn  <- pick(df, c("player.player.player.surname","player.surname","player.playerName.surname","surname","player_last_name"))
  data.frame(
    Player     = trimws(paste(given, surn)),
    Year       = SEASON,
    RoundName  = paste0("Round ", rn),
    disposals  = num(pick(df, c("disposals"))),
    kicks      = num(pick(df, c("kicks"))),
    handballs  = num(pick(df, c("handballs"))),
    marks      = num(pick(df, c("marks"))),
    tackles    = num(pick(df, c("tackles"))),
    goals      = num(pick(df, c("goals"))),
    behinds    = num(pick(df, c("behinds"))),
    clearances = num(pick(df, c("clearances.totalClearances","clearances","totalClearances"))),
    hitouts    = num(pick(df, c("hitouts"))),
    dreamteam  = num(pick(df, c("dreamTeamPoints","dreamteamPoints","afl_fantasy_score"))),
    stringsAsFactors = FALSE
  )
}

rows <- NULL
# 1) AFL Match Centre, per round
for (r in rounds) {
  d <- tryCatch(fetch_player_stats_afl(season = SEASON, round = r),
                error = function(e) { message(sprintf("[ladder_stats] afl round %s: %s", r, conditionMessage(e))); NULL })
  rr <- tryCatch(build_rows(d, r), error = function(e) NULL)
  if (!is.null(rr) && nrow(rr)) rows <- rbind(rows, rr)
}
# 2) fallback: fryzigg whole season, filtered to our rounds
if (is.null(rows) || !nrow(rows)) {
  message("[ladder_stats] AFL source empty — trying fryzigg")
  d <- tryCatch(fetch_player_stats_fryzigg(season = SEASON),
                error = function(e) { message(sprintf("[ladder_stats] fryzigg: %s", conditionMessage(e))); NULL })
  rows <- tryCatch(build_rows(d, max(rounds), keep_rounds = rounds), error = function(e) NULL)
}

if (is.null(rows) || !nrow(rows)) {
  message("[ladder_stats] no player stats from any source — leaving existing file untouched")
  quit(status = 0)
}
rows <- rows[nzchar(rows$Player), , drop = FALSE]

tmp <- paste0(OUT, ".tmp")
write_json(rows, tmp, auto_unbox = TRUE, na = "null", digits = 4)
file.rename(tmp, OUT)
message(sprintf("[ladder_stats] wrote %s — %d player-games (rounds %s)", OUT, nrow(rows), paste(rounds, collapse = ",")))
