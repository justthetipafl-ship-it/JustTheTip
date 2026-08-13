#!/usr/bin/env Rscript
# ============================================================
# fetch_aflw.R - AFLW fixtures + results + lineups + player gamelogs (fitzRoy)
# ============================================================
# Unlike the men's tool (which sources game logs from the wheelo CSV pipeline),
# the AFLW tool is FULLY fitzRoy-based: the official AFL website / Champion Data
# source carries AFLW player stats, so we fetch everything here:
#   * fixture   - upcoming round (home, away, venue, date, time)
#   * results   - OFFICIAL final scores across the 3 displayed seasons
#   * lineups   - selected sides for the upcoming round (Role Watch / Absorbers)
#   * gamelogs  - per-player, per-game Champion Data stat lines (the core data)
#
# PHASE 1 goal: land real data + dump the true column names so the Python
# aggregator can be built against them. Column names -> scripts/_aflw_*_columns.txt
# and a 3-row sample -> scripts/_aflw_playerstats_sample.json for easy inspection.
# Atomic temp->rename on every output. Non-destructive: preserves prior bundle
# sections we don't refresh here.
# ============================================================

suppressMessages({ library(fitzRoy); library(jsonlite); library(dplyr) })

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a
num  <- function(x) suppressWarnings(as.numeric(x))
pick <- function(df, candidates) { for (c in candidates) if (c %in% names(df)) return(df[[c]]); rep(NA, nrow(df)) }

SEASON   <- as.integer(Sys.getenv("AFLW_SEASON", format(Sys.Date(), "%Y")))
BUNDLE   <- Sys.getenv("AFLW_BUNDLE", "aflw/bundle.json")
GLOUT    <- Sys.getenv("AFLW_GAMELOGS", "aflw/raw_gamelogs.json")
SEASONS  <- trimws(strsplit(Sys.getenv("AFLW_SEASONS", "2024,2025,2026"), ",")[[1]])
if (!dir.exists("scripts")) dir.create("scripts")
if (!dir.exists("aflw"))    dir.create("aflw")
message(sprintf("[fetch_aflw] season=%d  seasons=%s  bundle=%s", SEASON, paste(SEASONS, collapse="/"), BUNDLE))

# AFLW is the same 18 clubs as the men's comp -> reuse the men's name normaliser
# (so team keys match the shared AFL logos + config).
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
fx <- tryCatch(fetch_fixture(season = SEASON, comp = "AFLW", source = "AFL"),
               error = function(e) { message("[fetch_aflw] fixture error: ", conditionMessage(e)); NULL })
if (!is.null(fx) && nrow(fx)) {
  writeLines(sort(names(fx)), "scripts/_aflw_fixture_columns.txt")
  fh <- norm_team(pick(fx, c("home.team.name","home.name")))
  fa <- norm_team(pick(fx, c("away.team.name","away.name")))
  fv <- as.character(pick(fx, c("venue.name","venue")))
  fdt<- as.character(pick(fx, c("utcStartTime","compSeason.startDate","date")))
  frn<- suppressWarnings(as.integer(pick(fx, c("round.roundNumber","roundNumber"))))
  ts <- suppressWarnings(as.POSIXct(substr(fdt, 1, 19), format = "%Y-%m-%dT%H:%M:%S", tz = "UTC"))
  finished <- !is.na(ts) & ts < (Sys.time() - 3 * 3600)
  rounds <- sort(unique(frn[!is.na(frn)]))
  done <- vapply(rounds, function(r) { ix <- which(frn == r); length(ix) > 0 && all(finished[ix]) }, logical(1))
  not_done <- rounds[!done]
  next_round <- if (length(not_done)) min(not_done) else suppressWarnings(max(rounds))
  keep <- which(frn == next_round)
  # AFLW runs Aug-Nov; eastern states have no DST until Oct, then +11. Australia/Sydney
  # tz handles the switch automatically, so per-venue tz still resolves local time correctly.
  venue_tz <- function(v){
    v <- tolower(if (is.na(v)) "" else v)
    if (grepl("optus|perth|hbf|subiaco|fremantle oval", v))  "Australia/Perth"
    else if (grepl("adelaide|barossa|norwood|unley", v))     "Australia/Adelaide"
    else if (grepl("traeger|marrara|darwin|tio", v))         "Australia/Darwin"
    else                                                      "Australia/Sydney"
  }
  fixture <- lapply(keep, function(i) {
    tz <- venue_tz(fv[i])
    tt <- suppressWarnings(as.POSIXct(substr(fdt[i], 1, 19), format = "%Y-%m-%dT%H:%M:%S", tz = "UTC"))
    if (is.na(tt)) { d <- substr(fdt[i], 1, 10); tm <- "" }
    else {
      d  <- format(tt, tz = tz, format = "%Y-%m-%d")
      tm <- sub("^0", "", format(tt, tz = tz, format = "%I:%M %p"))
    }
    list(home = fh[i], away = fa[i],
         venue = if (is.na(fv[i])) "" else fv[i],
         date = d, time = tm, utc = substr(fdt[i], 1, 19))
  })
}
message(sprintf("[fetch_aflw] next round=%s, %d fixtures", as.character(next_round), length(fixture)))

# ---------- RESULTS: official final scores across displayed seasons ----------
results <- list()
for (yr in SEASONS) {
  rs <- tryCatch(fetch_results(season = as.integer(yr), comp = "AFLW"),
                 error = function(e) { message(sprintf("[fetch_aflw] results %s error: %s", yr, conditionMessage(e))); NULL })
  if (is.null(rs) || !nrow(rs)) next
  writeLines(sort(names(rs)), "scripts/_aflw_results_columns.txt")
  rh <- norm_team(pick(rs, c("home.team.name","match.homeTeam.name","homeTeamName","home.name")))
  ra <- norm_team(pick(rs, c("away.team.name","match.awayTeam.name","awayTeamName","away.name")))
  hs <- num(pick(rs, c("homeTeamScore.matchScore.totalScore","homeTeamScore.totalScore","home.score","homeScore","homeTeamScoreFull")))
  as_<- num(pick(rs, c("awayTeamScore.matchScore.totalScore","awayTeamScore.totalScore","away.score","awayScore","awayTeamScoreFull")))
  rn <- suppressWarnings(as.integer(pick(rs, c("round.roundNumber","roundNumber"))))
  dt <- as.character(pick(rs, c("match.utcStartTime","utcStartTime","match.date","date")))
  vn <- as.character(pick(rs, c("venue.name","match.venue.name","venue")))
  for (i in seq_len(nrow(rs))) {
    if (is.na(hs[i]) || is.na(as_[i])) next
    results[[length(results)+1]] <- list(
      year = as.integer(yr), round = rn[i], home = rh[i], away = ra[i],
      homeScore = hs[i], awayScore = as_[i], margin = hs[i] - as_[i],
      winner = if (hs[i] > as_[i]) rh[i] else if (as_[i] > hs[i]) ra[i] else "Draw",
      date = substr(dt[i], 1, 10), venue = if (is.na(vn[i])) "" else vn[i],
      key  = mkey(yr, rn[i], rh[i], ra[i])
    )
  }
}
message(sprintf("[fetch_aflw] %d official results across %s", length(results), paste(SEASONS, collapse="/")))

# ---------- LINEUPS: selected sides for the upcoming round ----------
lineups <- list()
if (!is.na(next_round)) {
  lu <- tryCatch(fetch_lineup(season = SEASON, round_number = next_round, comp = "AFLW"),
                 error = function(e) { message("[fetch_aflw] lineup error (likely teams not named yet): ", conditionMessage(e)); NULL })
  if (!is.null(lu) && nrow(lu)) {
    writeLines(sort(names(lu)), "scripts/_aflw_lineup_columns.txt")
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
message(sprintf("[fetch_aflw] %d lineup entries for round %s", length(lineups), as.character(next_round)))

# ---------- GAMELOGS: per-player, per-game Champion Data lines (fitzRoy AFL source) ----------
# This is the piece the men's tool gets from wheelo CSVs; for AFLW it comes straight
# from fitzRoy. We tag each row with jttSeason, bind all seasons, and dump the true
# column names + a 3-row sample so the aggregator can be mapped exactly.
gl <- list()
for (yr in SEASONS) {
  ps <- tryCatch(fetch_player_stats(season = as.integer(yr), comp = "AFLW", source = "AFL"),
                 error = function(e) { message(sprintf("[fetch_aflw] player stats %s error: %s", yr, conditionMessage(e))); NULL })
  if (is.null(ps) || !nrow(ps)) { message(sprintf("[fetch_aflw]   (no player stats for %s)", yr)); next }
  writeLines(sort(names(ps)), "scripts/_aflw_playerstats_columns.txt")
  ps$jttSeason <- as.integer(yr)
  gl[[length(gl)+1]] <- ps
  message(sprintf("[fetch_aflw]   player stats %s: %d rows, %d cols", yr, nrow(ps), ncol(ps)))
}
if (length(gl)) {
  allgl <- dplyr::bind_rows(gl)
  message(sprintf("[fetch_aflw] %d total player-game rows, %d cols", nrow(allgl), ncol(allgl)))
  # 3-row sample for quick inspection of value shapes
  samp <- utils::head(allgl, 3)
  write_json(samp, "scripts/_aflw_playerstats_sample.json", auto_unbox = TRUE, na = "null", digits = 4, pretty = TRUE)
  tmp <- paste0(GLOUT, ".tmp")
  write_json(allgl, tmp, auto_unbox = TRUE, na = "null", digits = 4)
  file.rename(tmp, GLOUT)
  message(sprintf("[fetch_aflw] wrote %s", GLOUT))
} else {
  message("[fetch_aflw] WARNING: no gamelogs fetched - check comp/source support in the log above")
}

# ---------- write bundle (fixtures / results / lineups) ----------
existing <- if (file.exists(BUNDLE)) fromJSON(BUNDLE, simplifyVector = FALSE) else list()
bundle <- existing
bundle$fixture <- if (length(fixture)) fixture else (existing$fixture %||% list())
bundle$results <- if (length(results)) results else (existing$results %||% list())
bundle$lineups <- if (length(lineups)) lineups else (existing$lineups %||% list())
if (!is.na(next_round)) bundle$round <- next_round
bundle$fixtureUpdated <- format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
bundle$fixtureSource  <- "fitzRoy (AFLW)"

tmp <- paste0(BUNDLE, ".tmp")
write_json(bundle, tmp, auto_unbox = TRUE, na = "null", digits = 4)
file.rename(tmp, BUNDLE)
message(sprintf("[fetch_aflw] wrote %s - %d fixtures, %d results, %d lineup rows",
                BUNDLE, length(bundle$fixture), length(bundle$results), length(bundle$lineups)))
