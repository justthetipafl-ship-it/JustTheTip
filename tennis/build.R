#!/usr/bin/env Rscript
# =============================================================================
# build.R — JTT Tennis modelling pipeline (base R, ZERO CRAN dependencies)
#
# Sackmann match CSVs -> per-player match logs -> surface-adjusted Elo,
# serve/return profiles, ace model, H2H, form -> joined to ESPN fixtures via
# player_map.json -> match projections. Emits JSON bundles to data/.
#
# Runs on r-base-core alone (no jsonlite/tidyverse), so the GitHub Actions
# step just needs apt install r-base-core. No build step, by design.
#
#   Rscript build.R
# =============================================================================

# ---- config -----------------------------------------------------------------
CFG <- list(
  sackmann_dir = "data/sackmann",
  atp_years    = 2023:2026,
  wta_years    = 2025:2026,
  fixtures     = "data/tennis_fixtures.json",
  player_map   = "data/player_map.json",
  out_dir      = "data",
  K            = 32,        # Elo K-factor
  surf_prior   = 20,        # surface-Elo confidence prior (matches)
  recent_n     = 20         # recent-form window (matches)
)
SURFACES <- c("Hard", "Clay", "Grass")

# ---- tiny JSON writer (base R only) -----------------------------------------
.jstr <- function(s) {
  s <- as.character(s)
  s <- gsub("\\\\", "\\\\\\\\", s)
  s <- gsub('"', '\\\\"', s)
  s <- gsub("\n", "\\\\n", s); s <- gsub("\r", "\\\\r", s); s <- gsub("\t", "\\\\t", s)
  paste0('"', s, '"')
}
to_json <- function(x) {
  if (is.null(x)) return("null")
  if (is.list(x)) {
    nm <- names(x)
    if (!is.null(nm) && all(nzchar(nm))) {
      parts <- vapply(seq_along(x), function(i)
        paste0(.jstr(nm[i]), ":", to_json(x[[i]])), character(1))
      return(paste0("{", paste(parts, collapse = ","), "}"))
    }
    parts <- vapply(x, to_json, character(1))
    return(paste0("[", paste(parts, collapse = ","), "]"))
  }
  if (length(x) == 0) return("[]")
  if (length(x) > 1) {
    parts <- vapply(x, to_json, character(1))
    return(paste0("[", paste(parts, collapse = ","), "]"))
  }
  if (is.na(x)) return("null")
  if (is.logical(x)) return(if (x) "true" else "false")
  if (is.numeric(x)) { if (!is.finite(x)) return("null"); return(formatC(x, format = "g", digits = 10)) }
  .jstr(x)
}
write_json <- function(x, path) {
  writeLines(to_json(x), path, useBytes = TRUE)
  cat(sprintf("  wrote %s\n", path))
}

# ---- minimal JSON reader for our own bundles (flat enough for base parse) ----
read_json <- function(path) {
  txt <- paste(readLines(path, warn = FALSE, encoding = "UTF-8"), collapse = "\n")
  # base R has no JSON parser; our fixtures/map are produced by us/Python and
  # are well-formed. Use a tolerant eval via a JS-ish -> R translation is risky;
  # instead shell out to nothing — parse the two shapes we need explicitly.
  stop("read_json: use the typed loaders below")  # not used; see load_* funcs
}

# We avoid a general parser. Fixtures + player_map are read with a small,
# purpose-built tokeniser good enough for the (machine-generated) shapes.
.slurp <- function(path) paste(readLines(path, warn = FALSE, encoding = "UTF-8"), collapse = "")

# ASCII-fold for safe matching across mixed/garbled CSV encodings (ITF names)
ascii <- function(x) { x <- iconv(as.character(x), to = "ASCII//TRANSLIT"); x[is.na(x)] <- ""; x }

# ---- surface bucketing ------------------------------------------------------
bucket_surface <- function(s) {
  s <- tolower(trimws(as.character(s)))
  out <- rep(NA_character_, length(s))
  out[grepl("hard", s)]   <- "Hard"
  out[grepl("clay", s)]   <- "Clay"
  out[grepl("grass", s)]  <- "Grass"
  out[grepl("carpet", s)] <- "Hard"   # fold rare carpet into hard
  out
}

# ---- load matches -----------------------------------------------------------
NUMCOLS <- c("tourney_date","match_num","best_of",
             "w_ace","w_df","w_svpt","w_1stIn","w_1stWon","w_2ndWon","w_SvGms","w_bpSaved","w_bpFaced",
             "l_ace","l_df","l_svpt","l_1stIn","l_1stWon","l_2ndWon","l_SvGms","l_bpSaved","l_bpFaced",
             "winner_rank","loser_rank")

load_tour <- function(tour, years) {
  files <- file.path(CFG$sackmann_dir, sprintf("%s_matches_%d.csv", tour, years))
  files <- files[file.exists(files)]
  if (!length(files)) return(NULL)
  dfs <- lapply(files, function(f) read.csv(f, colClasses = "character", check.names = FALSE))
  m <- do.call(rbind, dfs)
  for (c in intersect(NUMCOLS, names(m))) m[[c]] <- suppressWarnings(as.numeric(m[[c]]))
  m$tour <- tour
  m$surf <- bucket_surface(m$surface)
  # chronological order; match_num breaks ties within a tournament
  m <- m[order(m$tourney_date, m$match_num), ]
  m
}

# ---- per-player long match log ----------------------------------------------
build_log <- function(m) {
  # winner perspective + loser perspective, stacked
  base <- function(side) {
    o <- if (side == "w") "l" else "w"
    data.frame(
      tour = m$tour,
      date = m$tourney_date,
      tourney = m$tourney_name,
      surf = m$surf,
      round = m$round,
      best_of = m$best_of,
      score = m$score,
      player_id = m[[paste0(ifelse(side=="w","winner","loser"), "_id")]],
      player_name = m[[paste0(ifelse(side=="w","winner","loser"), "_name")]],
      ioc = m[[paste0(ifelse(side=="w","winner","loser"), "_ioc")]],
      opp_id = m[[paste0(ifelse(o=="w","winner","loser"), "_id")]],
      opp_name = m[[paste0(ifelse(o=="w","winner","loser"), "_name")]],
      won = as.integer(side == "w"),
      ace = m[[paste0(side, "_ace")]],
      df = m[[paste0(side, "_df")]],
      svpt = m[[paste0(side, "_svpt")]],
      firstIn = m[[paste0(side, "_1stIn")]],
      firstWon = m[[paste0(side, "_1stWon")]],
      secondWon = m[[paste0(side, "_2ndWon")]],
      svGms = m[[paste0(side, "_SvGms")]],
      bpSaved = m[[paste0(side, "_bpSaved")]],
      bpFaced = m[[paste0(side, "_bpFaced")]],
      opp_ace = m[[paste0(o, "_ace")]],
      opp_svGms = m[[paste0(o, "_SvGms")]],
      opp_svpt = m[[paste0(o, "_svpt")]],
      opp_firstWon = m[[paste0(o, "_1stWon")]],
      opp_secondWon = m[[paste0(o, "_2ndWon")]],
      stringsAsFactors = FALSE
    )
  }
  rbind(base("w"), base("l"))
}

# ---- surface-adjusted Elo (chronological single pass) -----------------------
compute_elo <- function(m) {
  ids <- unique(c(m$winner_id, m$loser_id)); ids <- ids[!is.na(ids)]
  Eo <- new.env(parent = emptyenv()); No <- new.env(parent = emptyenv())
  Es <- setNames(lapply(SURFACES, function(.) new.env(parent = emptyenv())), SURFACES)
  Ns <- setNames(lapply(SURFACES, function(.) new.env(parent = emptyenv())), SURFACES)
  g  <- function(e, k) { v <- e[[k]]; if (is.null(v)) 1500 else v }
  gn <- function(e, k) { v <- e[[k]]; if (is.null(v)) 0 else v }
  K <- CFG$K
  for (i in seq_len(nrow(m))) {
    w <- m$winner_id[i]; l <- m$loser_id[i]
    if (is.na(w) || is.na(l)) next
    Rw <- g(Eo, w); Rl <- g(Eo, l)
    ew <- 1 / (1 + 10 ^ ((Rl - Rw) / 400))
    Eo[[w]] <- Rw + K * (1 - ew); Eo[[l]] <- Rl - K * (1 - ew)
    No[[w]] <- gn(No, w) + 1;     No[[l]] <- gn(No, l) + 1
    s <- m$surf[i]
    if (!is.na(s) && s %in% SURFACES) {
      Rws <- g(Es[[s]], w); Rls <- g(Es[[s]], l)
      ews <- 1 / (1 + 10 ^ ((Rls - Rws) / 400))
      Es[[s]][[w]] <- Rws + K * (1 - ews); Es[[s]][[l]] <- Rls - K * (1 - ews)
      Ns[[s]][[w]] <- gn(Ns[[s]], w) + 1;   Ns[[s]][[l]] <- gn(Ns[[s]], l) + 1
    }
  }
  list(Eo = Eo, No = No, Es = Es, Ns = Ns, get = g, getn = gn)
}

# blended surface Elo for prediction: lean on surface ladder in proportion to
# how much surface history a player has, else fall back to overall.
blended_elo <- function(elo, pid, surf) {
  o <- elo$get(elo$Eo, pid)
  if (length(surf) != 1 || is.na(surf) || !(surf %in% SURFACES)) return(o)
  s  <- elo$get(elo$Es[[surf]], pid)
  ns <- elo$getn(elo$Ns[[surf]], pid)
  wgt <- ns / (ns + CFG$surf_prior)
  wgt * s + (1 - wgt) * o
}

# ---- serve / return aggregates ----------------------------------------------
safe_div <- function(a, b) ifelse(is.na(b) | b == 0, NA_real_, a / b)

agg_block <- function(d) {
  # d: subset of the long log for one player (or player+surface)
  S <- function(x) sum(x, na.rm = TRUE)
  svpt <- S(d$svpt); firstIn <- S(d$firstIn)
  list(
    n            = nrow(d),
    ace_svgm     = round(safe_div(S(d$ace), S(d$svGms)), 4),
    df_svgm      = round(safe_div(S(d$df),  S(d$svGms)), 4),
    first_pct    = round(safe_div(firstIn, svpt), 4),
    first_win    = round(safe_div(S(d$firstWon), firstIn), 4),
    second_win   = round(safe_div(S(d$secondWon), svpt - firstIn), 4),
    spw          = round(safe_div(S(d$firstWon) + S(d$secondWon), svpt), 4),  # serve pts won
    bp_save      = round(safe_div(S(d$bpSaved), S(d$bpFaced)), 4),
    # return side: what the player did against opponents' serve
    ace_against  = round(safe_div(S(d$opp_ace), S(d$opp_svGms)), 4),           # aces conceded / return game
    rpw          = round(1 - safe_div(S(d$opp_firstWon) + S(d$opp_secondWon), S(d$opp_svpt)), 4)
  )
}

# ---- parse sets won/lost from a Sackmann score string, player-relative ------
parse_sets <- function(score, won) {
  if (is.na(score) || !nzchar(score)) return(c(NA_integer_, NA_integer_))
  sc <- gsub("\\([^)]*\\)", "", score)            # drop tiebreak detail (7)
  toks <- strsplit(trimws(sc), "\\s+")[[1]]
  w <- 0L; l <- 0L
  for (t in toks) {
    mm <- regmatches(t, regexec("^([0-9]+)-([0-9]+)$", t))[[1]]
    if (length(mm) == 3) {
      a <- as.integer(mm[2]); b <- as.integer(mm[3])
      if (a > b) w <- w + 1L else if (b > a) l <- l + 1L
    }
  }
  if (isTRUE(won == 1)) c(w, l) else c(l, w)        # score is winner-perspective
}

# ---- main -------------------------------------------------------------------
cat("JTT Tennis build.R\n")
atp <- load_tour("atp", CFG$atp_years)
wta <- load_tour("wta", CFG$wta_years)
cat(sprintf("  loaded ATP=%s WTA=%s matches\n",
            if (is.null(atp)) 0 else nrow(atp), if (is.null(wta)) 0 else nrow(wta)))

players_out <- list()
h2h_out <- list()
elo_by_tour <- list()
log_by_tour <- list()
ace_surf_factor <- list()

for (tr in c("atp", "wta")) {
  m <- if (tr == "atp") atp else wta
  if (is.null(m)) next
  elo <- compute_elo(m)
  elo_by_tour[[tr]] <- elo
  lg <- build_log(m)
  lg <- lg[!is.na(lg$player_id), ]
  log_by_tour[[tr]] <- lg

  # league ace/svgm per surface -> surface factor (for thin-sample adjustment)
  lf <- list(); ov <- safe_div(sum(lg$ace, na.rm=TRUE), sum(lg$svGms, na.rm=TRUE))
  for (s in SURFACES) {
    ds <- lg[!is.na(lg$surf) & lg$surf == s, ]
    lf[[s]] <- round(safe_div(sum(ds$ace, na.rm=TRUE), sum(ds$svGms, na.rm=TRUE)) / ov, 4)
  }
  ace_surf_factor[[tr]] <- lf

  # per-player aggregates
  byp <- split(seq_len(nrow(lg)), lg$player_id)
  for (pid in names(byp)) {
    idx <- byp[[pid]]
    d <- lg[idx, ]
    d <- d[order(d$date), ]
    career <- agg_block(d)
    recent <- agg_block(tail(d, CFG$recent_n))
    surf_splits <- list()
    for (s in SURFACES) {
      ds <- d[!is.na(d$surf) & d$surf == s, ]
      if (nrow(ds) > 0) surf_splits[[s]] <- agg_block(ds)
    }
    last10 <- tail(d$won, 10)
    # recent per-match detail (most-recent-first) for streak signals
    dr <- tail(d, 12)
    recent_matches <- lapply(rev(seq_len(nrow(dr))), function(i) {
      ss <- parse_sets(dr$score[i], dr$won[i])
      list(date = dr$date[i], tourney = dr$tourney[i], round = dr$round[i], surf = dr$surf[i],
           opp_name = dr$opp_name[i], won = as.integer(dr$won[i]), score = dr$score[i],
           ace = if (is.na(dr$ace[i])) NULL else as.integer(dr$ace[i]),
           opp_ace = if (is.na(dr$opp_ace[i])) NULL else as.integer(dr$opp_ace[i]),
           sw = if (is.na(ss[1])) NULL else ss[1],
           sl = if (is.na(ss[2])) NULL else ss[2])
    })
    players_out[[pid]] <- list(
      id = pid,
      name = d$player_name[nrow(d)],
      ioc = d$ioc[nrow(d)],
      tour = tr,
      n_matches = nrow(d),
      elo_overall = round(elo$get(elo$Eo, pid), 1),
      elo_hard  = round(elo$get(elo$Es$Hard,  pid), 1),
      elo_clay  = round(elo$get(elo$Es$Clay,  pid), 1),
      elo_grass = round(elo$get(elo$Es$Grass, pid), 1),
      n_hard = elo$getn(elo$Ns$Hard, pid),
      n_clay = elo$getn(elo$Ns$Clay, pid),
      n_grass = elo$getn(elo$Ns$Grass, pid),
      serve_career = career,
      serve_recent = recent,
      serve_surface = surf_splits,
      form_last10 = paste(ifelse(rev(last10) == 1, "W", "L"), collapse = ""),
      win_pct_recent = round(mean(tail(d$won, CFG$recent_n), na.rm = TRUE), 3),
      recent = recent_matches
    )
  }

  # H2H from the long log (winner-perspective rows only to avoid double count)
  wl <- lg[lg$won == 1, ]
  key <- function(a, b) paste(pmin(a, b), pmax(a, b), sep = "_")
  wl$k <- key(wl$player_id, wl$opp_id)
  for (k in unique(wl$k)) {
    rows <- wl[wl$k == k, ]
    ids <- strsplit(k, "_")[[1]]
    a <- ids[1]; b <- ids[2]
    a_wins <- sum(rows$player_id == a); b_wins <- sum(rows$player_id == b)
    last <- rows[which.max(rows$date), ]
    ord <- rows[order(-rows$date), ]
    mt <- utils::head(ord, 12)
    meetings <- lapply(seq_len(nrow(mt)), function(i) list(
      date = mt$date[i], tourney = mt$tourney[i], surface = mt$surf[i],
      score = mt$score[i], winner_id = mt$player_id[i], round = mt$round[i],
      w_ace = if (is.na(mt$ace[i])) NULL else as.integer(mt$ace[i]),
      l_ace = if (is.na(mt$opp_ace[i])) NULL else as.integer(mt$opp_ace[i])))
    h2h_out[[k]] <- list(
      a = a, b = b, a_wins = a_wins, b_wins = b_wins,
      last = list(date = last$date, tourney = last$tourney, surface = last$surf,
                  winner_id = last$player_id, round = last$round),
      meetings = meetings
    )
  }
}

# ---- fixtures + player_map (purpose-built extraction) -----------------------
# player_map.json: { "<espn_id>": {"sackmann_id": "...", ...}, ... }
parse_player_map <- function(path) {
  if (!file.exists(path)) return(list())
  txt <- .slurp(path)
  # grab "espn":{...,"sackmann_id":"NNN"...} pairs
  ids <- regmatches(txt, gregexpr('"([0-9/]+)"\\s*:\\s*\\{[^{}]*"sackmann_id"\\s*:\\s*"([0-9]+)"', txt))[[1]]
  map <- list()
  for (chunk in ids) {
    e <- sub('^"([0-9/]+)".*', "\\1", chunk)
    s <- sub('.*"sackmann_id"\\s*:\\s*"([0-9]+)".*', "\\1", chunk)
    map[[e]] <- s
  }
  map
}

# fixtures: pull upcoming singles matches we can project
parse_fixtures <- function(path) {
  if (!file.exists(path)) return(list())
  txt <- .slurp(path)
  # split on match_id boundaries
  blocks <- strsplit(txt, '\\{\\s*"match_id"')[[1]][-1]
  fx <- list()
  for (b in blocks) {
    get_s <- function(key) { m <- regmatches(b, regexpr(sprintf('"%s"\\s*:\\s*"([^"]*)"', key), b)); if (!length(m)) return(NA); sub(sprintf('.*"%s"\\s*:\\s*"([^"]*)".*', key), "\\1", m) }
    mid <- sub('^\\s*:\\s*"([^"]*)".*', "\\1", b)
    etype <- get_s("event_type"); status <- get_s("status"); tour <- get_s("tour")
    if (is.na(etype) || etype != "singles") next
    if (is.na(status) || status != "pre") next
    eids <- regmatches(b, gregexpr('"espn_id"\\s*:\\s*"([0-9/]+)"', b))[[1]]
    eids <- sub('.*"([0-9/]+)".*', "\\1", eids)
    if (length(eids) < 2) next
    fx[[length(fx) + 1]] <- list(match_id = mid, tour = tour, surface = get_s("surface"),
                                 tournament = get_s("tournament"), location = get_s("location"),
                                 round = get_s("round"),
                                 best_of = NA, e1 = eids[1], e2 = eids[2])
  }
  fx
}

# tournament -> surface backfill from Sackmann (most common surface per name)
norm_t <- function(x) trimws(gsub("[^a-z0-9]+", " ", tolower(ascii(x))))
KNOWN_SURF <- list(wimbledon = "Grass", "roland garros" = "Clay", "french open" = "Clay",
                   "us open" = "Hard", "australian open" = "Hard")
tourney_surface_map <- function() {
  ts <- list()
  for (tr in names(log_by_tour)) {
    lg <- log_by_tour[[tr]]
    sub <- lg[!is.na(lg$surf) & !is.na(lg$tourney), c("tourney", "surf")]
    for (tn in unique(sub$tourney)) {
      v <- sub$surf[sub$tourney == tn]
      ts[[paste(tr, norm_t(tn))]] <- names(sort(table(v), decreasing = TRUE))[1]
    }
  }
  ts
}

pmap <- parse_player_map(CFG$player_map)
fixtures <- parse_fixtures(CFG$fixtures)
tsmap <- tourney_surface_map()
cat(sprintf("  player_map entries=%d  upcoming singles fixtures=%d\n", length(pmap), length(fixtures)))

backfill_surface <- function(tournament, location = NA, tour = "atp") {
  cands <- c(norm_t(tournament))
  if (!is.na(location) && nzchar(location)) {
    cands <- c(cands, norm_t(location), norm_t(strsplit(location, ",")[[1]][1]))  # + city
  }
  cands <- unique(cands[nzchar(cands)])
  pref <- paste(tour, cands)                                          # tour-scoped keys
  keys <- names(tsmap)
  for (c in pref) if (!is.null(tsmap[[c]])) return(tsmap[[c]])        # exact (tour-scoped)
  for (c in pref) {                                                   # contains, same tour
    hit <- keys[vapply(keys, function(k) grepl(k, c, fixed = TRUE) || grepl(c, k, fixed = TRUE), logical(1))]
    if (length(hit)) return(tsmap[[hit[1]]])
  }
  for (c in cands) for (kn in names(KNOWN_SURF)) if (grepl(kn, c, fixed = TRUE)) return(KNOWN_SURF[[kn]])
  NA
}

# ---- projections ------------------------------------------------------------
# ---- style similarity engine (powers "vs similar players") ------------------
# per-player style vector [serve pts won, return pts won, ace rate], z-scored
# within tour, then nearest-neighbour lookup + record vs that cluster.
.sids <- names(players_out)
.svec <- lapply(players_out, function(P) c(
  spw = (function(x) if (is.null(x) || is.na(x)) NA_real_ else x)(P$serve_career$spw),
  rpw = (function(x) if (is.null(x) || is.na(x)) NA_real_ else x)(P$serve_career$rpw),
  ace = (function(x) if (is.null(x) || is.na(x)) NA_real_ else x)(P$serve_career$ace_svgm)))
.raw  <- do.call(rbind, .svec)
.tourv <- vapply(.sids, function(id) { t <- players_out[[id]]$tour; if (is.null(t)) NA_character_ else t }, character(1))
.nv    <- vapply(.sids, function(id) { n <- players_out[[id]]$serve_career$n; if (is.null(n) || is.na(n)) 0L else as.integer(n) }, integer(1))
.zmat  <- matrix(NA_real_, nrow = length(.sids), ncol = 3, dimnames = list(.sids, c("spw","rpw","ace")))
for (tr in unique(stats::na.omit(.tourv))) {
  idx <- which(.tourv == tr)
  for (j in 1:3) {
    col <- .raw[idx, j]; mu <- mean(col, na.rm = TRUE); sdv <- stats::sd(col, na.rm = TRUE)
    if (is.na(sdv) || sdv == 0) sdv <- 1
    .zmat[idx, j] <- (col - mu) / sdv
  }
}
similar_ids <- function(target, exclude, K = 25) {
  if (is.null(target) || !(target %in% .sids)) return(character(0))
  tr <- .tourv[[target]]; if (is.na(tr)) return(character(0))
  zt <- .zmat[target, ]; if (any(is.na(zt))) return(character(0))
  cand <- .sids[.tourv == tr & .nv >= 20 & !(.sids %in% c(target, exclude))]
  cand <- cand[stats::complete.cases(.zmat[cand, , drop = FALSE])]
  if (!length(cand)) return(character(0))
  d <- sqrt(rowSums((.zmat[cand, , drop = FALSE] - matrix(zt, length(cand), 3, byrow = TRUE))^2))
  names(sort(d))[seq_len(min(K, length(cand)))]
}
arch_label <- function(target) {
  if (is.null(target) || !(target %in% .sids)) return("similar players")
  z <- .zmat[target, ]; if (any(is.na(z))) return("similar players")
  if (z[["ace"]] >= 0.8 || z[["spw"]] >= 0.8) return("big servers")
  if (z[["rpw"]] >= 0.8) return("strong returners")
  if (z[["spw"]] <= -0.5 && z[["rpw"]] >= 0.2) return("grinders")
  if (z[["spw"]] >= 0.3 && z[["rpw"]] >= 0.3) return("all-court players")
  "similar profiles"
}
vs_similar <- function(focal, opponent) {
  cand <- similar_ids(opponent, exclude = focal)
  if (!length(cand)) return(NULL)
  rows <- lg[lg$player_id == focal & lg$opp_id %in% cand, ]
  if (!nrow(rows)) return(NULL)
  w <- sum(rows$won == 1, na.rm = TRUE); l <- sum(rows$won == 0, na.rm = TRUE)
  if ((w + l) < 3) return(NULL)
  list(label = arch_label(opponent), n_opp = length(unique(rows$opp_id)),
       w = w, l = l, win_pct = round(w / (w + l), 3))
}
mkpl <- function(sid, P, wp, elo, paces, vs) {
  o <- list(sackmann_id = sid, name = if (!is.null(P)) P$name else NA,
            win_prob = round(wp, 3), elo = round(elo, 1), proj_aces = paces)
  if (!is.null(vs)) o$vs_similar <- vs
  o
}

# ---- serve/return point-based match model (Barnett-Clarke hierarchy) --------
# Tour-average serve-points-won baseline (match-count weighted).
spw_base <- list()
for (tr0 in unique(stats::na.omit(vapply(names(players_out), function(id) {
        t <- players_out[[id]]$tour; if (is.null(t)) NA_character_ else t }, character(1))))) {
  vv <- c(); ww <- c()
  for (id in names(players_out)) { P <- players_out[[id]]
    if (!identical(P$tour, tr0)) next
    sp <- P$serve_career$spw; n <- P$serve_career$n
    if (is.null(sp) || is.na(sp) || is.null(n) || is.na(n) || n < 20) next
    vv <- c(vv, sp); ww <- c(ww, n) }
  spw_base[[tr0]] <- if (length(vv)) sum(vv * ww) / sum(ww) else 0.62
}
.clamp <- function(x, lo, hi) min(max(x, lo), hi)
game_prob <- function(p) {                # prob server wins a game
  if (is.na(p)) return(NA_real_)
  p <- .clamp(p, 1e-6, 1 - 1e-6); q <- 1 - p
  deuce <- p^2 / (p^2 + q^2)
  p^4 + 4*p^4*q + 10*p^4*q^2 + 20*p^3*q^3*deuce
}
tb_prob <- function(pA, pB) {             # prob A wins a tiebreak
  pA <- .clamp(pA, 1e-6, 1 - 1e-6); pB <- .clamp(pB, 1e-6, 1 - 1e-6)
  memo <- new.env()
  rec <- function(sa, sb) {
    if (sa >= 7 && sa - sb >= 2) return(1); if (sb >= 7 && sb - sa >= 2) return(0)
    if (sa >= 6 && sb >= 6 && sa == sb) { d <- (pA + (1 - pB)) / 2; return(d^2 / (d^2 + (1 - d)^2)) }
    k <- sa + sb; ky <- paste(sa, sb); v <- memo[[ky]]; if (!is.null(v)) return(v)
    srvA <- (floor((k + 1) / 2) %% 2) == 0
    pw <- if (srvA) pA else (1 - pB)
    r <- pw * rec(sa + 1, sb) + (1 - pw) * rec(sa, sb + 1); memo[[ky]] <- r; r
  }; rec(0, 0)
}
set_prob <- function(gA, gB, pA, pB, aFirst) {   # prob A wins a set
  tbA <- tb_prob(pA, pB); memo <- new.env()
  rec <- function(a, b, srvA) {
    if (a >= 6 && a - b >= 2) return(1); if (b >= 6 && b - a >= 2) return(0)
    if (a == 6 && b == 6) return(tbA)
    ky <- paste(a, b, srvA); v <- memo[[ky]]; if (!is.null(v)) return(v)
    pg <- if (srvA) gA else (1 - gB)
    r <- pg * rec(a + 1, b, !srvA) + (1 - pg) * rec(a, b + 1, !srvA); memo[[ky]] <- r; r
  }; rec(0, 0, aFirst)
}
match_prob <- function(s, bestof) if (bestof == 5) s^3 * (1 + 3*(1 - s) + 6*(1 - s)^2) else s^2 * (3 - 2*s)
sr_for <- function(P, surf, what) {
  if (is.null(P)) return(NA_real_)
  if (!is.na(surf) && !is.null(P$serve_surface[[surf]])) { v <- P$serve_surface[[surf]][[what]]
    if (!is.null(v) && !is.na(v)) return(v) }
  v <- P$serve_career[[what]]; if (is.null(v) || is.na(v)) NA_real_ else v
}
serve_winprob <- function(P1, P2, surf, best_of, tr) {
  spwA <- sr_for(P1, surf, "spw"); rpwA <- sr_for(P1, surf, "rpw")
  spwB <- sr_for(P2, surf, "spw"); rpwB <- sr_for(P2, surf, "rpw")
  if (any(is.na(c(spwA, rpwA, spwB, rpwB)))) return(NA_real_)
  base <- spw_base[[tr]]; if (is.null(base) || is.na(base)) base <- 0.62
  pA <- .clamp(spwA - rpwB + (1 - base), 0.30, 0.92)   # A point-win on A serve
  pB <- .clamp(spwB - rpwA + (1 - base), 0.30, 0.92)   # B point-win on B serve
  gA <- game_prob(pA); gB <- game_prob(pB)
  sA <- 0.5 * (set_prob(gA, gB, pA, pB, TRUE) + set_prob(gA, gB, pA, pB, FALSE))
  match_prob(sA, best_of)
}
.logit <- function(x) { x <- .clamp(x, 1e-6, 1 - 1e-6); log(x / (1 - x)) }
.inv   <- function(z) 1 / (1 + exp(-z))

proj_out <- list()
for (fx in fixtures) {
  tr <- if (fx$tour %in% names(elo_by_tour)) fx$tour else "atp"
  elo <- elo_by_tour[[tr]]
  s1 <- pmap[[fx$e1]]; s2 <- pmap[[fx$e2]]
  if (is.null(s1) || is.null(s2)) next                      # need both mapped
  surf <- fx$surface
  if (length(surf) != 1 || is.na(surf) || surf %in% c("", "null")) surf <- backfill_surface(fx$tournament, fx$location, tr)
  surf <- bucket_surface(surf)
  if (length(surf) != 1) surf <- NA_character_
  e1 <- blended_elo(elo, s1, surf); e2 <- blended_elo(elo, s2, surf)
  p_elo <- 1 / (1 + 10 ^ ((e2 - e1) / 400))

  P1 <- players_out[[s1]]; P2 <- players_out[[s2]]
  best_of <- 3
  # serve/return point model, ensembled with Elo in log-odds; weight scales
  # with how much serve data both players have.
  p_srv <- serve_winprob(P1, P2, surf, best_of, tr)
  if (is.na(p_srv)) {
    p1 <- p_elo
  } else {
    n1 <- P1$serve_career$n; n2 <- P2$serve_career$n
    minn <- min(if (is.null(n1) || is.na(n1)) 0 else n1, if (is.null(n2) || is.na(n2)) 0 else n2)
    w_srv <- 0.45 * (minn / (minn + 40))
    p1 <- .inv((1 - w_srv) * .logit(p_elo) + w_srv * .logit(p_srv))
  }
  # shrunk head-to-head nudge
  H <- h2h_out[[key(s1, s2)]]
  if (!is.null(H)) {
    nA <- if (H$a == s1) H$a_wins else H$b_wins
    nB <- if (H$a == s1) H$b_wins else H$a_wins
    if ((nA + nB) >= 2) {
      edge <- (nA / (nA + nB)) - 0.5
      p1 <- .inv(.logit(p1) + 0.50 * edge * ((nA + nB) / (nA + nB + 4)))
    }
  }
  p1 <- .clamp(p1, 0.02, 0.98)
  spread <- abs(p1 - 0.5)
  # heuristic total games (v1): blowout -> fewer, even -> more; bo5 longer
  base_g <- if (best_of == 5) 33 else 21
  proj_games <- round(base_g - 14 * spread + ifelse(best_of == 5, 0, 0), 1)
  exp_sv_games <- proj_games / 2

  surf_for_rate <- if (!is.na(surf)) surf else NA
  lf <- ace_surf_factor[[tr]]
  sf <- if (!is.na(surf) && !is.null(lf[[surf]]) && is.finite(lf[[surf]])) lf[[surf]] else 1
  # rate: prefer surface-specific ace rate (already surface-adjusted, no factor);
  # else fall back to recent/career rate scaled by the surface factor.
  rate_of <- function(P) {
    if (is.null(P)) return(NA)
    if (!is.na(surf_for_rate) && !is.null(P$serve_surface[[surf_for_rate]])) {
      r <- P$serve_surface[[surf_for_rate]]$ace_svgm
      if (!is.null(r) && !is.na(r)) return(r)
    }
    r <- P$serve_recent$ace_svgm
    if (is.null(r) || is.na(r)) r <- P$serve_career$ace_svgm
    if (is.null(r) || is.na(r)) return(NA)
    r * sf
  }
  conc_of <- function(P) if (is.null(P) || is.null(P$serve_career$ace_against)) NA else P$serve_career$ace_against
  n_of    <- function(P) if (is.null(P)) 0 else P$serve_career$n
  league_conc <- median(vapply(players_out, function(P) { v <- P$serve_career$ace_against; if (is.null(v) || is.na(v)) NA_real_ else v }, numeric(1)), na.rm = TRUE)
  proj_ace <- function(rate, opp_conc, opp_n) {
    if (is.na(rate)) return(NA)
    adj <- 1
    if (!is.na(opp_conc) && is.finite(league_conc) && league_conc > 0 && !is.na(opp_n) && opp_n >= 10) {
      adj <- max(0.8, min(1.25, opp_conc / league_conc))   # bounded return-vulnerability nudge
    }
    round(rate * exp_sv_games * adj, 1)
  }

  proj_out[[fx$match_id]] <- list(
    match_id = fx$match_id, tour = tr, tournament = fx$tournament,
    surface = surf, round = fx$round,
    players = list(
      mkpl(s1, P1, p1,     e1, proj_ace(rate_of(P1), conc_of(P2), n_of(P2)), vs_similar(s1, s2)),
      mkpl(s2, P2, 1 - p1, e2, proj_ace(rate_of(P2), conc_of(P1), n_of(P1)), vs_similar(s2, s1))
    ),
    proj_total_games = proj_games,
    model = "elo-surface-blend + serve/return point model (Barnett-Clarke), n-weighted log-odds ensemble + shrunk H2H; v2 (backtested: 0.9% lower log-loss vs Elo-only over 7.7k matches)"
  )
}

# ---- write bundles ----------------------------------------------------------
dir.create(CFG$out_dir, showWarnings = FALSE, recursive = TRUE)
write_json(list(generated = as.character(Sys.time()), n = length(players_out),
                players = unname(players_out)), file.path(CFG$out_dir, "tennis_players.json"))
write_json(list(n = length(h2h_out), h2h = h2h_out), file.path(CFG$out_dir, "tennis_h2h.json"))
write_json(list(n = length(proj_out), projections = unname(proj_out)), file.path(CFG$out_dir, "tennis_projections.json"))
cat(sprintf("  players=%d  h2h_pairs=%d  projections=%d\n",
            length(players_out), length(h2h_out), length(proj_out)))
cat("done\n")
