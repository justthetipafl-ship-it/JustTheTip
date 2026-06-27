#!/usr/bin/env Rscript
# backfill_2023.R — one-shot 2023 AFL player game-log backfill via fitzRoy.
#
# WHY: game logs only enter the tool through the wheeloratings CSV ingest
# (AFL/csv/ -> ingest_csv.py -> bundle.json -> build_afl_data.py). fitzRoy's
# normal fetch (fetch_afl.R) does fixtures/results/lineups only and never
# touches player logs, so the --seasons 2023 flag had nothing to un-filter.
# This script fetches 2023 player match stats, maps them to the SAME canonical
# CSV schema the ingest expects, and writes AFL/csv/gamelogs_2023.csv. The
# normal ingest then upserts them by (Year, MatchId, Player); H2H history for
# Deja Vu / Bunnies lifts automatically (15 team pairs were stuck at 2 meetings).
#
# COVERAGE: the markets that matter (disposals, kicks, handballs, marks,
# tackles, clearances, goals, behinds, fantasy/DreamTeam) come straight from the
# AFL feed and are reliable. A few wheelo-only advanced columns (SuperCoach,
# xScore, post-clearance splits, Forward50Possessions) are NOT in the AFL feed
# and are left blank — the build's to_num() turns blanks into nulls and the UI
# tolerates them.
#
# SAFETY: the script validates row count + team-name normalisation and exits
# non-zero (writing nothing) if the fitzRoy schema has drifted, so a broken
# fetch can never push garbage into the bundle.

suppressMessages({ library(fitzRoy); library(readr) })

OUT    <- "AFL/csv/gamelogs_2023.csv"
SEASON <- 2023L

# ----- the tool's 18 canonical (short) team names -----
TOOL_TEAMS <- c("Adelaide","Brisbane","Carlton","Collingwood","Essendon","Fremantle",
  "Geelong","Gold Coast","Greater Western Sydney","Hawthorn","Melbourne","North Melbourne",
  "Port Adelaide","Richmond","St Kilda","Sydney","West Coast","Western Bulldogs")

# fitzRoy / AFL-API names (and nicknames) -> tool short names. Keyed by a
# lower-cased alphanumeric squash so "Sydney Swans", "sydney_swans", "Swans" all hit.
TEAM_CANON <- c(
  adelaide="Adelaide", adelaidecrows="Adelaide", crows="Adelaide",
  brisbane="Brisbane", brisbanelions="Brisbane", lions="Brisbane",
  carlton="Carlton", blues="Carlton",
  collingwood="Collingwood", magpies="Collingwood", pies="Collingwood",
  essendon="Essendon", bombers="Essendon",
  fremantle="Fremantle", dockers="Fremantle", freo="Fremantle",
  geelong="Geelong", geelongcats="Geelong", cats="Geelong",
  goldcoast="Gold Coast", goldcoastsuns="Gold Coast", suns="Gold Coast",
  gws="Greater Western Sydney", gwsgiants="Greater Western Sydney",
  greaterwesternsydney="Greater Western Sydney", giants="Greater Western Sydney",
  hawthorn="Hawthorn", hawks="Hawthorn",
  melbourne="Melbourne", demons="Melbourne", dees="Melbourne",
  northmelbourne="North Melbourne", kangaroos="North Melbourne", roos="North Melbourne",
  portadelaide="Port Adelaide", portadelaidepower="Port Adelaide", power="Port Adelaide",
  richmond="Richmond", tigers="Richmond",
  stkilda="St Kilda", saints="St Kilda",
  sydney="Sydney", sydneyswans="Sydney", swans="Sydney",
  westcoast="West Coast", westcoasteagles="West Coast", eagles="West Coast",
  westernbulldogs="Western Bulldogs", bulldogs="Western Bulldogs",
  footscray="Western Bulldogs", dogs="Western Bulldogs"
)
normTeam <- function(x) {
  k <- gsub("[^a-z0-9]", "", tolower(ifelse(is.na(x), "", x)))
  out <- unname(TEAM_CANON[k])
  ifelse(is.na(out), as.character(x), out)   # unknown -> keep raw (surfaces in validation)
}

message("[backfill] fetching 2023 player stats via fitzRoy …")
df <- tryCatch(fetch_player_stats_afl(season = SEASON),
               error = function(e) { message("[backfill] FATAL fetch error: ", conditionMessage(e)); quit(status = 1) })
if (is.null(df) || nrow(df) == 0) { message("[backfill] FATAL: fitzRoy returned no rows"); quit(status = 1) }
message(sprintf("[backfill] fetched %d player-game rows, %d cols", nrow(df), ncol(df)))
cn <- names(df)
message("[backfill] columns:\n  ", paste(cn, collapse = ", "))

# first matching column from a candidate list (else an all-NA vector)
pick <- function(...) { for (c in c(...)) if (c %in% cn) return(df[[c]]); rep(NA, nrow(df)) }
as_chr <- function(x) ifelse(is.na(x), "", as.character(x))

# ---- identity ----
gv <- pick("player.player.player.givenName", "player.givenName", "player.player.givenName", "givenName")
sn <- pick("player.player.player.surname",   "player.surname",   "player.player.surname",   "surname")
full <- pick("player.player.player.playerName", "player.playerName", "playerName")
player <- trimws(paste(as_chr(gv), as_chr(sn)))
player <- ifelse(player == "" & as_chr(full) != "", as_chr(full), player)

homeTeam <- normTeam(pick("home.team.name", "match.homeTeam.name", "homeTeam.name", "home.name"))
awayTeam <- normTeam(pick("away.team.name", "match.awayTeam.name", "awayTeam.name", "away.name"))
teamRaw  <- pick("team.name", "player.team.name", "teamName", "team")
teamName <- normTeam(teamRaw)
status   <- tolower(as_chr(pick("teamStatus", "player.teamStatus", "home.away")))

team <- ifelse(as_chr(teamName) != "", teamName,
          ifelse(status == "home", homeTeam,
            ifelse(status == "away", awayTeam, "")))
opp  <- ifelse(team == homeTeam, awayTeam, ifelse(team == awayTeam, homeTeam, ""))

matchId  <- as_chr(pick("providerId", "match.matchId", "match.providerId", "matchId"))
roundNum <- pick("round.roundNumber", "round.round", "roundNumber")
roundNm  <- as_chr(pick("round.name", "round.roundName", "roundName"))
roundNm  <- ifelse(roundNm != "", roundNm, ifelse(!is.na(roundNum), paste("Round", roundNum), ""))

# ---- canonical header  <-  fitzRoy candidate(s) ----
out <- data.frame(
  Year        = SEASON,
  RoundName   = roundNm,
  MatchId     = matchId,
  Player      = player,
  Team        = team,
  Opponent    = opp,
  Disposals            = pick("disposals"),
  Kicks                = pick("kicks"),
  Handballs            = pick("handballs"),
  Marks                = pick("marks"),
  ContestedMarks       = pick("contestedMarks"),
  InterceptMarks       = pick("extendedStats.interceptMarks", "interceptMarks"),
  Tackles              = pick("tackles"),
  PressureActs         = pick("extendedStats.pressureActs", "pressureActs"),
  Goals                = pick("goals"),
  Behinds              = pick("behinds"),
  ShotsAtGoal          = pick("shotsAtGoal", "extendedStats.shotsAtGoal"),
  GoalAssists          = pick("goalAssists"),
  ScoreInvolvements    = pick("extendedStats.scoreInvolvements", "scoreInvolvements"),
  TotalClearances      = pick("clearances.totalClearances", "extendedStats.totalClearances", "totalClearances"),
  Hitouts              = pick("hitouts"),
  Inside50s            = pick("inside50s"),
  ContestedPossessions = pick("contestedPossessions"),
  GroundBallGets       = pick("extendedStats.groundBallGets", "groundBallGets"),
  Intercepts           = pick("extendedStats.intercepts", "intercepts"),
  HandballReceives     = pick("extendedStats.handballReceives", "handballReceives"),
  MetresGained         = pick("extendedStats.metresGained", "metresGained"),
  CentreBounceAttendancePercentage = pick("extendedStats.centreBounceAttendancePercentage", "centreBounceAttendancePercentage"),
  TimeOnGround         = pick("timeOnGroundPercentage", "extendedStats.timeOnGroundPercentage"),
  DreamTeamPoints      = pick("dreamTeamPoints"),
  RatingPoints         = pick("ratingPoints"),
  DisposalEfficiency   = pick("disposalEfficiency"),
  MarksOnLead          = pick("extendedStats.marksOnLead", "marksOnLead"),
  MarksInside50        = pick("marksInside50"),
  TacklesInside50      = pick("extendedStats.tacklesInside50", "tacklesInside50"),
  Rebound50s           = pick("rebound50s"),
  stringsAsFactors = FALSE, check.names = FALSE
)

# ---- clean + validate ----
out <- out[as_chr(out$Player) != "" & as_chr(out$Team) != "" & as_chr(out$MatchId) != "", ]
n_disp <- sum(!is.na(out$Disposals))
message(sprintf("[backfill] %d rows after clean; %d carry disposals", nrow(out), n_disp))

if (nrow(out) < 4000 || n_disp < 4000) {
  message("[backfill] FATAL: too few usable rows — fitzRoy schema likely changed.")
  message("           Inspect the column dump above and update the pick() candidates. NOT writing CSV.")
  quit(status = 1)
}

# every team name must have mapped to one of the 18 canonical names
unmapped <- setdiff(unique(c(out$Team, out$Opponent[as_chr(out$Opponent) != ""])), TOOL_TEAMS)
if (length(unmapped) > 0) {
  message("[backfill] FATAL: these team names did not normalise — add them to TEAM_CANON:")
  message("           ", paste(unmapped, collapse = " | "))
  quit(status = 1)
}

# each match should resolve to exactly two teams (so H2H pairs cleanly)
mt  <- tapply(out$Team, out$MatchId, function(t) length(unique(t)))
bad <- sum(mt != 2)
message(sprintf("[backfill] %d matches resolved; %d with != 2 teams (expect 0)", length(mt), bad))

dir.create(dirname(OUT), showWarnings = FALSE, recursive = TRUE)
readr::write_csv(out, OUT, na = "")
message(sprintf("[backfill] wrote %s — %d rows, %d cols", OUT, nrow(out), ncol(out)))
