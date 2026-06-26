#!/usr/bin/env Rscript
# ============================================================
# fetch_afl.R — fixtures + official results + lineups (fitzRoy)
# ============================================================
# Game logs / player / team stats now come from the wheelo CSV pipeline
# (ingest_csv.py). fitzRoy's job is the match-level + selection data wheelo
# CSVs don't carry:
#   * fixtures  — upcoming round (home, away, venue, date, time)
#   * results   — OFFICIAL final scores across all seasons we display, so
#                 margins/scorelines are exact (not reconstructed from player
#                 goals+behinds, which misses rushed behinds)
#   * lineups   — the selected sides for the upcoming round (feeds Role Watch
#                 / Absorbers and "who's actually playing")
#
# NON-DESTRUCTIVE: this NEVER touches dvp / player / teamform / teamdef / fgs /
# injury — those belong to the CSV pipeline. It only refreshes fixture/results/
# lineups, and only overwrites them on a successful fetch. Atomic temp->rename.
# Self-diagnosing: real column names are dumped to scripts/_afl_*_columns.txt
# so the candidate lists can be tightened after the first Actions run.
# ============================================================

suppressMessages({ library(fitzRoy); library(jsonlite); library(dplyr) })

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a
num  <- function(x) suppressWarnings(as.numeric(x))
pick <- function(df, candidates) { for (c in candidates) if (c %in% names(df)) return(df[[c]]); rep(NA, nrow(df)) }

SEASON  <- as.integer(Sys.getenv("AFL_SEASON", format(Sys.Date(), "%Y")))
BUNDLE  <- Sys.getenv("AFL_BUNDLE", "AFL/bundle.json")
RES_SEASONS <- trimws(strsplit(Sys.getenv("RESULTS_SEASONS", "2024,2025,2026"), ",")[[1]])
if (!dir.exists("scripts")) dir.create("scripts")
message(sprintf("[fetch_afl] season=%d  results=%s  bundle=%s", SEASON, paste(RES_SEASONS, collapse="/"), BUNDLE))

TEAM_MAP <- c(
  "Adelaide Crows"="Adelaide","Adelaide"="Adelaide","Brisbane Lions"="Brisbane","Brisbane"="Brisbane","Brisbane Lion"="Brisbane",
  "Carlton"="Carlton","Collingwood"="Collingwood","Essendon"="Essendon","Fremantle"="Fremantle",
  "Geelong Cats"="Geelong","Geelong"="Geelong","Gold Coast Suns"="Gold Coast","Gold Coast"="Gold Coast","Gold Coast SUNS"="Gold Coast",
  "GWS Giants"="Greater Western Sydney","Greater Western Sydney"="Greater Western Sydney","GWS GIANTS"="Greater Western Sydney","GWS"="Greater Western Sydney",
  "Hawthorn"="Hawthorn","Melbourne"="Melbourne","North Melbourne"="North Melbourne","Kangaroos"="North Melbourne",
  "Port Adelaide"="Port Adelaide","Richmond"="Richmond","St Kilda"="St Kilda","Sydney Swans"="Sydney","Sydney"="Sydney",
  "West Coast Eagles"="West Coast","West Coast"="West Coast","Western Bulldogs"="Western Bulldogs","Footscray"="Western Bulldogs"
)
norm_team <- function(x) { x <- as.character(x); out <- unname(TEAM_MAP[x]); ifelse(is.na(out), x, out) }
mkey <- function(yr, rn, a, b) paste0(yr, "-R", rn, "-", pmin(a, b), "-v-", pmax(a, b))

# ---------- FIXTURES: upcoming round ----------
fixture <- list(); next_round <- NA_integer_
fx <- tryCatch(fetch_fixture_afl(season = SEASON), error = function(e) { message("[fetch_afl] fixture error: ", conditionMessage(e)); NULL })
if (!is.null(fx) && nrow(fx)) {
  writeLines(sort(names(fx)), "scripts/_afl_fixture_columns.txt")
  fh <- norm_team(pick(fx, c("home.team.name","home.name")))
  fa <- norm_team(pick(fx, c("away.team.name","away.name")))
  fv <- as.character(pick(fx, c("venue.name","venue")))
  fdt<- as.character(pick(fx, c("utcStartTime","compSeason.startDate","date")))
  frn<- suppressWarnings(as.integer(pick(fx, c("round.roundNumber","roundNumber"))))
  # A game is "finished" only if it has a known start time more than 3h in the past.
  # A round is the upcoming one if it is the lowest round NOT fully finished — this
  # rolls past a completed round even when future rounds have no scheduled time yet
  # (their NA times count as not-finished, so they stay eligible).
  ts <- suppressWarnings(as.POSIXct(substr(fdt, 1, 19), format = "%Y-%m-%dT%H:%M:%S", tz = "UTC"))
  finished <- !is.na(ts) & ts < (Sys.time() - 3 * 3600)
  rounds <- sort(unique(frn[!is.na(frn)]))
  done <- vapply(rounds, function(r) { ix <- which(frn == r); length(ix) > 0 && all(finished[ix]) }, logical(1))
  not_done <- rounds[!done]
  next_round <- if (length(not_done)) min(not_done) else suppressWarnings(max(rounds))
  keep <- which(frn == next_round)
  # utcStartTime is UTC; convert to the venue's local time (AFL displays local).
  # Winter season = no DST for eastern states, so Australia/Sydney == +10 == AEST.
  venue_tz <- function(v){
    v <- tolower(if (is.na(v)) "" else v)
    if (grepl("optus|perth|hbf|subiaco", v))            "Australia/Perth"
    else if (grepl("adelaide|barossa|norwood", v))      "Australia/Adelaide"
    else if (grepl("traeger|marrara|darwin|tio", v))    "Australia/Darwin"
    else                                                "Australia/Sydney"
  }
  fixture <- lapply(keep, function(i) {
    tz <- venue_tz(fv[i])
    tt <- suppressWarnings(as.POSIXct(substr(fdt[i], 1, 19), format = "%Y-%m-%dT%H:%M:%S", tz = "UTC"))
    if (is.na(tt)) { d <- substr(fdt[i], 1, 10); tm <- "" }
    else {
      d  <- format(tt, tz = tz, format = "%Y-%m-%d")
      tm <- sub("^0", "", format(tt, tz = tz, format = "%I:%M %p"))   # "7:30 PM"
    }
    list(home = fh[i], away = fa[i],
         venue = if (is.na(fv[i])) "" else fv[i],
         date = d, time = tm, utc = substr(fdt[i], 1, 19))
  })
}
message(sprintf("[fetch_afl] next round=%s, %d fixtures", as.character(next_round), length(fixture)))

# ---------- RESULTS: official final scores across displayed seasons ----------
results <- list()
for (yr in RES_SEASONS) {
  rs <- tryCatch(fetch_results_afl(season = as.integer(yr)), error = function(e) { message(sprintf("[fetch_afl] results %s error: %s", yr, conditionMessage(e))); NULL })
  if (is.null(rs) || !nrow(rs)) next
  writeLines(sort(names(rs)), "scripts/_afl_results_columns.txt")   # last one wins; for diagnosis
  rh <- norm_team(pick(rs, c("home.team.name","match.homeTeam.name","homeTeamName","home.name")))
  ra <- norm_team(pick(rs, c("away.team.name","match.awayTeam.name","awayTeamName","away.name")))
  hs <- num(pick(rs, c("homeTeamScore.matchScore.totalScore","homeTeamScore.totalScore","home.score","homeScore","homeTeamScoreFull")))
  as_<- num(pick(rs, c("awayTeamScore.matchScore.totalScore","awayTeamScore.totalScore","away.score","awayScore","awayTeamScoreFull")))
  rn <- suppressWarnings(as.integer(pick(rs, c("round.roundNumber","roundNumber"))))
  dt <- as.character(pick(rs, c("match.utcStartTime","utcStartTime","match.date","date")))
  vn <- as.character(pick(rs, c("venue.name","match.venue.name","venue")))
  for (i in seq_len(nrow(rs))) {
    if (is.na(hs[i]) || is.na(as_[i])) next            # not completed -> skip
    results[[length(results)+1]] <- list(
      year = as.integer(yr), round = rn[i], home = rh[i], away = ra[i],
      homeScore = hs[i], awayScore = as_[i], margin = hs[i] - as_[i],
      winner = if (hs[i] > as_[i]) rh[i] else if (as_[i] > hs[i]) ra[i] else "Draw",
      date = substr(dt[i], 1, 10), venue = if (is.na(vn[i])) "" else vn[i],
      key  = mkey(yr, rn[i], rh[i], ra[i])
    )
  }
}
message(sprintf("[fetch_afl] %d official results across %s", length(results), paste(RES_SEASONS, collapse="/")))

# ---------- LINEUPS: selected sides for the upcoming round ----------
lineups <- list()
if (!is.na(next_round)) {
  lu <- tryCatch(fetch_lineup_afl(season = SEASON, round_number = next_round),
                 error = function(e) { message("[fetch_afl] lineup error (likely teams not named yet): ", conditionMessage(e)); NULL })
  if (!is.null(lu) && nrow(lu)) {
    writeLines(sort(names(lu)), "scripts/_afl_lineup_columns.txt")
    lg <- pick(lu, c("player.playerName.givenName","player.givenName","givenName","player.player.givenName"))
    ls <- pick(lu, c("player.playerName.surname","player.surname","surname","player.player.surname"))
    lname <- trimws(paste(ifelse(is.na(lg), "", lg), ifelse(is.na(ls), "", ls)))
    lteam <- norm_team(pick(lu, c("teamName","team.name","player.team.name")))
    lpos  <- as.character(pick(lu, c("position","player.position")))
    lstat <- as.character(pick(lu, c("selectionStatusId","status","playerPosition")))
    for (i in seq_len(nrow(lu))) {
      if (is.na(lname[i]) || lname[i] == "") next
      lineups[[length(lineups)+1]] <- list(round = next_round, team = lteam[i],
        player = lname[i], position = if (is.na(lpos[i])) "" else lpos[i],
        status = if (is.na(lstat[i])) "" else lstat[i])
    }
  }
}
message(sprintf("[fetch_afl] %d lineup entries for round %s", length(lineups), as.character(next_round)))

# ---------- merge: refresh ONLY fixture/results/lineups; preserve the rest ----------
existing <- if (file.exists(BUNDLE)) fromJSON(BUNDLE, simplifyVector = FALSE) else list()
bundle <- existing                                   # keeps dvp/player/teamform/teamdef/fgs/injury/meta intact
bundle$fixture <- if (length(fixture)) fixture else (existing$fixture %||% list())
bundle$results <- if (length(results)) results else (existing$results %||% list())
bundle$lineups <- if (length(lineups)) lineups else (existing$lineups %||% list())
if (!is.na(next_round)) bundle$round <- next_round
bundle$fixtureUpdated <- format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
bundle$fixtureSource  <- "fitzRoy"

tmp <- paste0(BUNDLE, ".tmp")
write_json(bundle, tmp, auto_unbox = TRUE, na = "null", digits = 4)
file.rename(tmp, BUNDLE)
message(sprintf("[fetch_afl] wrote %s — %d fixtures, %d results, %d lineup rows (game logs untouched)",
                BUNDLE, length(bundle$fixture), length(bundle$results), length(bundle$lineups)))
