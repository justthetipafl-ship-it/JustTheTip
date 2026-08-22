#!/usr/bin/env Rscript
# fetch_afl_ladder_stats.R — per-game AFL player stats for the Ladder's grading,
# straight from the AFL Match Centre via fitzRoy. These land the morning after each
# game, so the Ladder can result a rung next-day instead of waiting for wheelo's
# weekly per-round file. Writes AFL/data/ladder_gamelogs.json in the game-log shape
# build_ladder.py already reads: Player / Year / RoundName + the gradeable stats.
# Non-destructive: only overwrites on a successful fetch (atomic temp -> rename).
suppressMessages({ library(fitzRoy); library(jsonlite); library(dplyr) })

num  <- function(x) suppressWarnings(as.numeric(x))
pick <- function(df, candidates) { for (c in candidates) if (c %in% names(df)) return(df[[c]]); rep(NA, nrow(df)) }

SEASON <- as.integer(Sys.getenv("AFL_SEASON", format(Sys.Date(), "%Y")))
OUT    <- Sys.getenv("LADDER_STATS", "AFL/data/ladder_gamelogs.json")
if (!dir.exists("scripts")) dir.create("scripts")

# Rounds to pull: the current round from meta.json plus the one before it, so a
# late-posting game from the prior round still lands. Falls back to the last 2.
cur <- tryCatch(as.integer(jsonlite::fromJSON("AFL/data/meta.json")$round),
                error = function(e) NA_integer_)
rounds <- if (!is.na(cur)) unique(c(cur - 1L, cur)) else integer(0)
rounds <- rounds[rounds >= 1L]
if (!length(rounds)) { message("[ladder_stats] no round in meta.json — nothing to do"); quit(status = 0) }
message(sprintf("[ladder_stats] season=%d rounds=%s", SEASON, paste(rounds, collapse = ",")))

grab <- function(rnd) {
  df <- tryCatch(fetch_player_stats_afl(season = SEASON, round = rnd),
                 error = function(e) { message(sprintf("[ladder_stats] round %s: %s", rnd, conditionMessage(e))); NULL })
  if (is.null(df) || !nrow(df)) { message(sprintf("[ladder_stats] round %s: no games yet", rnd)); return(NULL) }
  writeLines(sort(names(df)), "scripts/_afl_playerstats_columns.txt")   # diagnosis: the real column names
  given <- pick(df, c("player.player.player.givenName", "player.givenName", "player.playerName.givenName", "givenName"))
  surn  <- pick(df, c("player.player.player.surname", "player.surname", "player.playerName.surname", "surname"))
  nm <- trimws(paste(given, surn))
  rn <- suppressWarnings(as.integer(pick(df, c("round.roundNumber", "roundNumber"))))
  rn[is.na(rn)] <- rnd
  data.frame(
    Player     = nm,
    Year       = SEASON,
    RoundName  = paste0("Round ", rn),
    disposals  = num(pick(df, c("disposals"))),
    kicks      = num(pick(df, c("kicks"))),
    handballs  = num(pick(df, c("handballs"))),
    marks      = num(pick(df, c("marks"))),
    tackles    = num(pick(df, c("tackles"))),
    goals      = num(pick(df, c("goals"))),
    behinds    = num(pick(df, c("behinds"))),
    clearances = num(pick(df, c("clearances.totalClearances", "clearances", "totalClearances"))),
    hitouts    = num(pick(df, c("hitouts"))),
    dreamteam  = num(pick(df, c("dreamTeamPoints", "dreamteamPoints", "dreamTeam"))),
    stringsAsFactors = FALSE
  )
}

rows <- do.call(rbind, lapply(rounds, grab))
if (is.null(rows) || !nrow(rows)) {
  message("[ladder_stats] no player stats fetched — leaving existing file untouched"); quit(status = 0)
}
rows <- rows[nzchar(rows$Player), , drop = FALSE]

tmp <- paste0(OUT, ".tmp")
write_json(rows, tmp, auto_unbox = TRUE, na = "null", digits = 4)
file.rename(tmp, OUT)
message(sprintf("[ladder_stats] wrote %s — %d player-games (rounds %s)", OUT, nrow(rows), paste(rounds, collapse = ",")))
