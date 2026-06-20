#!/usr/bin/env Rscript
# ============================================================
# fetch_afl.R — Stage 1 of the AFL data pipeline
# ============================================================
# Pulls the current season's player game logs + fixtures from the
# AFL (Champion Data) API via fitzRoy and merges them into
# AFL/bundle.json, which build_afl_data.py then splits + derives.
#
# Design notes:
#  * NON-DESTRUCTIVE: only overwrites bundle.json on a *successful*
#    fetch. Any failure stop()s before writing, leaving the live
#    bundle intact (mirrors the tennis cached-CSV fallback pattern).
#  * PRESERVES history + slow-changing blobs: prior-season game logs,
#    the player position/age blob, FGS and injuries are carried over
#    from the existing bundle (fitzRoy doesn't supply those cleanly).
#  * RESILIENT column mapping: each target column is resolved against
#    a list of candidate fitzRoy names; a missing one becomes NA and
#    the Python build + scoring engine degrade gracefully rather than
#    crashing. The actual columns seen are dumped to
#    scripts/_afl_columns.txt so the mapping can be tightened later.
# ============================================================

suppressMessages({
  library(fitzRoy); library(jsonlite); library(dplyr)
})

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a

SEASON <- as.integer(Sys.getenv("AFL_SEASON", format(Sys.Date(), "%Y")))
BUNDLE <- Sys.getenv("AFL_BUNDLE", "AFL/bundle.json")
message(sprintf("[fetch_afl] season=%d  bundle=%s", SEASON, BUNDLE))

# ---- team-name normaliser: fitzRoy names -> tool canonical names ----
TEAM_MAP <- c(
  "Adelaide Crows"="Adelaide", "Adelaide"="Adelaide",
  "Brisbane Lions"="Brisbane", "Brisbane"="Brisbane", "Brisbane Lion"="Brisbane",
  "Carlton"="Carlton", "Collingwood"="Collingwood", "Essendon"="Essendon",
  "Fremantle"="Fremantle", "Geelong Cats"="Geelong", "Geelong"="Geelong",
  "Gold Coast Suns"="Gold Coast", "Gold Coast"="Gold Coast", "Gold Coast SUNS"="Gold Coast",
  "GWS Giants"="Greater Western Sydney", "Greater Western Sydney"="Greater Western Sydney", "GWS GIANTS"="Greater Western Sydney",
  "Hawthorn"="Hawthorn", "Melbourne"="Melbourne", "North Melbourne"="North Melbourne",
  "Kangaroos"="North Melbourne", "Port Adelaide"="Port Adelaide", "Richmond"="Richmond",
  "St Kilda"="St Kilda", "Sydney Swans"="Sydney", "Sydney"="Sydney",
  "West Coast Eagles"="West Coast", "West Coast"="West Coast", "Western Bulldogs"="Western Bulldogs"
)
norm_team <- function(x) {
  x <- as.character(x); out <- unname(TEAM_MAP[x]); ifelse(is.na(out), x, out)
}

# ---- column resolver: first candidate present wins, else NA ----
pick <- function(df, candidates) {
  for (c in candidates) if (c %in% names(df)) return(df[[c]])
  rep(NA, nrow(df))
}
num <- function(x) suppressWarnings(as.numeric(x))

# ---- fetch player stats ----
ps <- tryCatch(
  fetch_player_stats_afl(season = SEASON),
  error = function(e) { message("[fetch_afl] player stats error: ", conditionMessage(e)); NULL }
)
if (is.null(ps) || nrow(ps) == 0) stop("[fetch_afl] no player stats for season ", SEASON, " — leaving bundle untouched")

# diagnostic dump so the mapping can be finalised against real columns
if (!dir.exists("scripts")) dir.create("scripts")
writeLines(sort(names(ps)), "scripts/_afl_columns.txt")
message(sprintf("[fetch_afl] %d rows, %d cols (dumped to scripts/_afl_columns.txt)", nrow(ps), ncol(ps)))

# ---- identity columns ----
given  <- pick(ps, c("player.player.player.givenName","player.givenName","givenName","player.player.givenName"))
surn   <- pick(ps, c("player.player.player.surname","player.surname","surname","player.player.surname"))
player <- trimws(paste(given, surn))
team   <- norm_team(pick(ps, c("team.name","player.player.team.name","teamName","team")))
home   <- norm_team(pick(ps, c("home.team.name","match.home.team.name","home.name")))
away   <- norm_team(pick(ps, c("away.team.name","match.away.team.name","away.name")))
rnum   <- suppressWarnings(as.integer(pick(ps, c("round.roundNumber","roundNumber","round.round_number"))))
rname  <- ifelse(is.na(rnum), as.character(pick(ps, c("round.name","roundName"))), paste("Round", rnum))
# deterministic match key (consistent across both teams in a game -> build pairs them)
match_id <- paste0(SEASON, "-R", rnum, "-", pmin(home, away), "-v-", pmax(home, away))

# ---- map stats to the PascalCase names build_afl_data.py expects ----
# (target = c(candidate fitzRoy column names…))
dvp <- data.frame(
  Year = SEASON, RoundName = rname, MatchId = match_id, Player = player, Team = team,
  Disposals        = num(pick(ps, c("disposals"))),
  Kicks            = num(pick(ps, c("kicks"))),
  Handballs        = num(pick(ps, c("handballs"))),
  Marks            = num(pick(ps, c("marks"))),
  ContestedMarks   = num(pick(ps, c("contestedMarks"))),
  InterceptMarks   = num(pick(ps, c("extendedStats.interceptMarks","interceptMarks"))),
  Tackles          = num(pick(ps, c("tackles"))),
  PressureActs     = num(pick(ps, c("extendedStats.pressureActs","pressureActs"))),
  Goals            = num(pick(ps, c("goals"))),
  Behinds          = num(pick(ps, c("behinds"))),
  ShotsAtGoal      = num(pick(ps, c("shotsAtGoal","extendedStats.shotsAtGoal"))),
  GoalAssists      = num(pick(ps, c("goalAssists"))),
  ScoreInvolvements= num(pick(ps, c("scoreInvolvements"))),
  TotalClearances  = num(pick(ps, c("clearances.totalClearances","totalClearances"))),
  Hitouts          = num(pick(ps, c("hitouts"))),
  Inside50s        = num(pick(ps, c("inside50s"))),
  ContestedPossessions = num(pick(ps, c("contestedPossessions"))),
  GroundBallGets   = num(pick(ps, c("extendedStats.groundBallGets","groundBallGets"))),
  Intercepts       = num(pick(ps, c("extendedStats.intercepts","intercepts"))),
  xScore           = num(pick(ps, c("extendedStats.expectedScore","expectedScore","xScore"))),
  PostClearanceGroundBallGets        = num(pick(ps, c("extendedStats.postClearanceGroundBallGets"))),
  PostClearanceContestedPossessions  = num(pick(ps, c("extendedStats.postClearanceContestedPossessions"))),
  HandballReceives = num(pick(ps, c("extendedStats.handballReceives","handballReceives"))),
  MetresGained     = num(pick(ps, c("metresGained"))),
  CentreBounceAttendancePercentage = num(pick(ps, c("extendedStats.centreBounceAttendancePercentage","extendedStats.centreBounceAttendances","centreBounceAttendances"))),
  TimeOnGround     = num(pick(ps, c("timeOnGroundPercentage","extendedStats.timeOnGroundPercentage"))),
  DreamTeamPoints  = num(pick(ps, c("dreamTeamPoints"))),
  Supercoach       = num(pick(ps, c("superCoachPoints","supercoach"))),
  RatingPoints     = num(pick(ps, c("ratingPoints"))),
  DisposalEfficiency = num(pick(ps, c("disposalEfficiency","extendedStats.disposalEfficiency"))),
  stringsAsFactors = FALSE, check.names = FALSE
)
dvp <- dvp[!is.na(dvp$Player) & dvp$Player != "" & !is.na(dvp$Team), ]
if (nrow(dvp) == 0) stop("[fetch_afl] resolved 0 valid rows — check scripts/_afl_columns.txt for the real names")
message(sprintf("[fetch_afl] mapped %d current-season player-game rows", nrow(dvp)))

# report which targets came back entirely empty (so gaps are visible in the log)
empty <- names(dvp)[sapply(dvp, function(c) all(is.na(c)))]
if (length(empty)) message("[fetch_afl] NOTE — no data for: ", paste(empty, collapse = ", "))

# ---- fixtures: keep just the upcoming round ----
fixture <- list()
fx <- tryCatch(fetch_fixture_afl(season = SEASON), error = function(e) NULL)
next_round <- max(rnum, na.rm = TRUE) + 1
if (!is.null(fx) && nrow(fx)) {
  fh <- norm_team(pick(fx, c("home.team.name","home.name")))
  fa <- norm_team(pick(fx, c("away.team.name","away.name")))
  fv <- as.character(pick(fx, c("venue.name","venue")))
  fdt <- as.character(pick(fx, c("utcStartTime","compSeason.startDate","date")))
  frn <- suppressWarnings(as.integer(pick(fx, c("round.roundNumber","roundNumber"))))
  # upcoming round = smallest round with a start date today-or-later; fall back to max+1
  d <- suppressWarnings(as.Date(substr(fdt, 1, 10)))
  upcoming <- frn[!is.na(d) & d >= (Sys.Date() - 1)]
  if (length(upcoming)) next_round <- min(upcoming, na.rm = TRUE)
  keep <- which(frn == next_round)
  fixture <- lapply(keep, function(i) list(
    home = fh[i], away = fa[i],
    venue = if (is.na(fv[i])) "" else fv[i],
    date  = substr(fdt[i], 1, 10),
    time  = if (nchar(fdt[i]) >= 16) substr(fdt[i], 12, 16) else ""
  ))
}
message(sprintf("[fetch_afl] next round=%d, %d fixtures", next_round, length(fixture)))

# ---- merge into existing bundle (preserve history + blobs) ----
existing <- if (file.exists(BUNDLE)) fromJSON(BUNDLE, simplifyVector = FALSE) else list()
old_dvp  <- existing$dvp %||% list()
hist     <- Filter(function(r) as.character(r$Year) != as.character(SEASON), old_dvp)
new_rows <- fromJSON(toJSON(dvp, dataframe = "rows", na = "null", digits = 4), simplifyVector = FALSE)
message(sprintf("[fetch_afl] preserving %d historical rows + %d fresh rows", length(hist), length(new_rows)))

bundle <- list(
  dvp     = c(hist, new_rows),
  player  = existing$player  %||% list(),   # position/age preserved from prior source
  fixture = if (length(fixture)) fixture else (existing$fixture %||% list()),
  fgs     = existing$fgs     %||% list(),
  injury  = existing$injury  %||% list(),
  round   = next_round,
  version = as.character(as.integer(as.numeric(Sys.time()))),
  created = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
  source  = "fitzRoy"
)

# write atomically: temp then rename, so a half-write can't corrupt the live bundle
tmp <- paste0(BUNDLE, ".tmp")
write_json(bundle, tmp, auto_unbox = TRUE, na = "null", digits = 4)
file.rename(tmp, BUNDLE)
message(sprintf("[fetch_afl] wrote %s (%d total game-log rows)", BUNDLE, length(bundle$dvp)))
