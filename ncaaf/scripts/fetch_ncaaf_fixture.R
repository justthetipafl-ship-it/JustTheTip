#!/usr/bin/env Rscript
# fetch_ncaaf_fixture.R — NCAAF fixture from the cfbfastR data (keyless, covers the
# current season), mapped to the ESPN team ids the tool uses (teams.json / logo CDN).
# Writes ncaaf/data/fixture.json in the shape build_ncaaf_data.py produces.
#
# WEEK 0 SAFE: it does NOT trust a "current week" — it takes the earliest UPCOMING week
# by kickoff DATE (games not yet played, today or later), so a week-0 season still ships
# Week 0/Week 1. Non-destructive: only writes on a good load.
#
# Dumps the loaded schedule's column names to ncaaf/_schedule_columns.txt on each run so
# we can correct any field mapping the same way we did the fitzRoy feed.
suppressMessages({ library(cfbfastR); library(jsonlite) })

SEASON <- suppressWarnings(as.integer(Sys.getenv("NCAAF_SEASON", "")))
if (is.na(SEASON) || SEASON < 2000) SEASON <- as.integer(format(Sys.Date(), "%Y"))
OUT   <- Sys.getenv("NCAAF_FIXTURE", "ncaaf/data/fixture.json")
TEAMS <- Sys.getenv("NCAAF_TEAMS",   "ncaaf/data/teams.json")
if (!dir.exists("ncaaf")) dir.create("ncaaf")

# ---- load the season schedule (try the keyless data loader; fall back to CFBD if keyed) ----
sched <- NULL
for (fn in list(
  function() load_cfb_schedules(seasons = SEASON),
  function() cfbd_game_info(year = SEASON, season_type = "both")
)) {
  sched <- tryCatch(fn(), error = function(e) { message("  (loader failed: ", conditionMessage(e), ")"); NULL })
  if (!is.null(sched) && nrow(sched)) break
}
if (is.null(sched) || !nrow(sched)) { message("[ncaaf-fixture] no schedule for ", SEASON, " — leaving fixture untouched"); quit(status = 0) }
writeLines(sort(names(sched)), "ncaaf/_schedule_columns.txt")

# ---- complete id -> name map (this season + 2 prior) so results/H2H opponents resolve, not just the gamelog teams ----
tm_extra <- new.env(parent = emptyenv())
.add_tm <- function(sch) {
  if (is.null(sch) || !nrow(sch)) return(invisible())
  hi <- as.character(pick(sch, c("home_id", "home_team_id"))); hn <- as.character(pick(sch, c("home_team", "home")))
  ai <- as.character(pick(sch, c("away_id", "away_team_id"))); an <- as.character(pick(sch, c("away_team", "away")))
  for (i in seq_along(hi)) {
    if (!is.na(hi[i]) && nzchar(hi[i]) && hi[i] != "NA" && !exists(hi[i], envir = tm_extra, inherits = FALSE)) assign(hi[i], hn[i], envir = tm_extra)
    if (!is.na(ai[i]) && nzchar(ai[i]) && ai[i] != "NA" && !exists(ai[i], envir = tm_extra, inherits = FALSE)) assign(ai[i], an[i], envir = tm_extra)
  }
}
.add_tm(sched)
.add_tm(tryCatch(load_cfb_schedules(seasons = c(SEASON - 2, SEASON - 1)), error = function(e) NULL))
extra <- list()
for (id in ls(tm_extra)) { nm <- get(id, envir = tm_extra); if (!is.na(nm) && nzchar(nm)) extra[[id]] <- list(name = nm, abbr = toupper(substr(gsub("[^A-Za-z0-9]", "", nm), 1, 4))) }
if (length(extra)) { write_json(extra, "ncaaf/data/teams_extra.json", auto_unbox = TRUE); message(sprintf("[ncaaf-fixture] wrote teams_extra.json (%d teams)", length(extra))) }
message(sprintf("[ncaaf-fixture] %d games loaded for %d", nrow(sched), SEASON))

pick <- function(df, cands) { for (c in cands) if (c %in% names(df)) return(df[[c]]); rep(NA, nrow(df)) }
norm <- function(s) { s <- tolower(ifelse(is.na(s), "", as.character(s)))
  s <- gsub("[^a-z0-9 ]", " ", s); s <- gsub("\\b(university|univ|the)\\b", " ", s); trimws(gsub("\\s+", " ", s)) }

# ---- ESPN id map from teams.json (school / displayName / abbr) ----
tj <- fromJSON(TEAMS, simplifyDataFrame = TRUE)
name2id <- new.env(parent = emptyenv())
if (is.data.frame(tj)) {
  for (i in seq_len(nrow(tj))) {
    id <- as.character(tj$team[i])
    for (f in c("school", "displayName", "abbr")) if (f %in% names(tj)) {
      k <- norm(tj[[f]][i]); if (nzchar(k) && is.null(name2id[[k]])) assign(k, id, envir = name2id)
    }
  }
}
resolve <- function(nm) {
  k <- norm(nm); v <- mget(k, envir = name2id, ifnotfound = list(NA_character_))[[1]]
  if (!is.na(v)) return(v)
  parts <- strsplit(k, " ")[[1]]
  if (length(parts) > 1) for (c in (length(parts) - 1):1) {
    cand <- paste(parts[1:c], collapse = " ")
    vv <- mget(cand, envir = name2id, ifnotfound = list(NA_character_))[[1]]
    if (!is.na(vv)) return(vv)
  }
  NA_character_
}

home_nm <- pick(sched, c("home_team", "home", "home_team_name", "homeTeam"))
away_nm <- pick(sched, c("away_team", "away", "away_team_name", "awayTeam"))
wk      <- suppressWarnings(as.integer(pick(sched, c("week", "game_week"))))
sd      <- as.character(pick(sched, c("start_date", "start_time", "startDate", "date", "game_date")))
done    <- pick(sched, c("completed", "status_completed"))
neu     <- pick(sched, c("neutral_site", "neutral", "neutralSite"))
conf    <- pick(sched, c("conference_game", "conferenceGame"))
ven     <- pick(sched, c("venue", "venue_name"))
hconf   <- as.character(pick(sched, c("home_conference", "home_conf")))
aconf   <- as.character(pick(sched, c("away_conference", "away_conf")))

# the cfbfastR schedule is ESPN-keyed, so home_id/away_id ARE ESPN team ids -> use directly
# (resolves every team, incl. the ~25 FBS teams missing from teams.json); name-map only as fallback
hid <- as.character(pick(sched, c("home_id", "home_team_id", "homeId")))
aid <- as.character(pick(sched, c("away_id", "away_team_id", "awayId")))
mh <- is.na(hid) | hid == "" | hid == "NA"; if (any(mh)) hid[mh] <- vapply(home_nm[mh], resolve, character(1))
ma <- is.na(aid) | aid == "" | aid == "NA"; if (any(ma)) aid[ma] <- vapply(away_nm[ma], resolve, character(1))
iso <- ifelse(is.na(sd) | sd == "", "", ifelse(grepl("T", sd), sd, paste0(substr(sd, 1, 10), "T00:00:00Z")))
day <- substr(iso, 1, 10)
is_done <- !is.na(done) & (done %in% c(TRUE, "true", "TRUE", 1, "1"))

ok <- !is.na(hid) & !is.na(aid) & nzchar(hid) & nzchar(aid)
unmatched <- unique(c(home_nm[is.na(hid)], away_nm[is.na(aid)]))
unmatched <- unmatched[!is.na(unmatched) & nzchar(unmatched)]
if (length(unmatched)) { message("[ncaaf-fixture] ", length(unmatched), " teams unmatched to ESPN ids (likely FCS, skipped):")
  for (n in head(sort(unmatched), 40)) message("    - ", n) }

hdiv <- tolower(as.character(pick(sched, c("home_division", "home_classification", "home_div"))))
adiv <- tolower(as.character(pick(sched, c("away_division", "away_classification", "away_div"))))
is_fbs <- (hdiv %in% c("fbs")) | (adiv %in% c("fbs"))   # keep games with >=1 FBS team (drops pure FCS/D2/D3)
if (!any(is_fbs)) is_fbs <- rep(TRUE, length(hid))       # no division field -> keep all rather than drop everything
today <- format(Sys.Date(), "%Y-%m-%d")
cand <- ok & is_fbs & !is_done & !is.na(day) & day >= today & !is.na(wk)
if (!any(cand)) { message("[ncaaf-fixture] no upcoming unplayed games — leaving fixture untouched"); quit(status = 0) }
nxt <- min(wk[cand], na.rm = TRUE)                      # earliest UPCOMING week -> week-0 safe
sel <- which(cand & wk == nxt)

# ---- AP / CFP rankings via CFBD (needs CFBD_API_KEY; schedule still ships without it) ----
rank_by_id <- new.env(parent = emptyenv())
ck <- Sys.getenv("CFBD_API_KEY", "")
if (nzchar(ck)) {
  if (exists("cfbd_key")) try(cfbd_key(ck), silent = TRUE)
  rk <- tryCatch(cfbd_rankings(year = SEASON), error = function(e) { message("  (cfbd_rankings failed: ", conditionMessage(e), ")"); NULL })
  if (!is.null(rk) && nrow(rk)) {
    wc <- if ("week" %in% names(rk)) suppressWarnings(as.integer(rk$week)) else rep(1L, nrow(rk))
    latest <- suppressWarnings(max(wc, na.rm = TRUE)); if (!is.finite(latest)) latest <- NA
    if (!is.na(latest)) rk <- rk[!is.na(wc) & wc == latest, , drop = FALSE]
    polls <- if ("poll" %in% names(rk)) as.character(rk$poll) else rep("", nrow(rk))
    pref <- c("Playoff Committee Rankings", "AP Top 25", "Coaches Poll")
    chosen <- pref[pref %in% unique(polls)]; chosen <- if (length(chosen)) chosen[1] else unique(polls)[1]
    if (!is.na(chosen)) rk <- rk[polls == chosen, , drop = FALSE]
    schools <- as.character(pick(rk, c("school", "team")))
    ranks   <- suppressWarnings(as.integer(pick(rk, c("rank", "ranking"))))
    n <- 0L
    for (i in seq_along(schools)) { id <- resolve(schools[i]); if (!is.na(id) && !is.na(ranks[i])) { assign(id, ranks[i], envir = rank_by_id); n <- n + 1L } }
    message(sprintf("[ncaaf-fixture] rankings: %s week %s (%d teams mapped)", chosen, latest, n))
  }
} else message("[ncaaf-fixture] no CFBD_API_KEY -> rankings skipped")
rkOf <- function(id) { r <- mget(as.character(id), envir = rank_by_id, ifnotfound = list(NA_integer_))[[1]]; if (is.na(r)) NULL else as.integer(r) }

rows <- lapply(sel, function(i) list(
  home = hid[i], away = aid[i], homeName = as.character(home_nm[i]), awayName = as.character(away_nm[i]),
  week = wk[i], season = SEASON, utc = iso[i], date = day[i],
  venue = if (is.na(ven[i])) "TBC" else as.character(ven[i]),
  homeConf = if (is.na(hconf[i])) NULL else hconf[i], awayConf = if (is.na(aconf[i])) NULL else aconf[i],
  neutral = if (isTRUE(neu[i]) || (!is.na(neu[i]) && neu[i] %in% c("true","1",1,TRUE))) 1L else 0L,
  confGame = if (isTRUE(conf[i]) || (!is.na(conf[i]) && conf[i] %in% c("true","1",1,TRUE))) 1L else 0L,
  spread = NULL, total = NULL, hasLine = 0L, homeRank = rkOf(hid[i]), awayRank = rkOf(aid[i]),
  p4 = 0L, fbsVfbs = 1L, realistic = 1L))
rows <- rows[order(vapply(rows, function(r) r$utc, character(1)))]

tmp <- paste0(OUT, ".tmp")
write_json(rows, tmp, auto_unbox = TRUE, null = "null")
file.rename(tmp, OUT)
message(sprintf("[ncaaf-fixture] wrote %s — %d games (week %d)", OUT, length(rows), nxt))
