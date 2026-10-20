#!/usr/bin/env python3
"""NBA shot actions - how a man gets his points, and what a defence gives up to each.

    python nba/build_nba_playtypes.py --out nba/data [--seasons 2026,2027]
      (no --seasons = the season just gone and the one under way, same window as build_nba_zones)
    python nba/build_nba_playtypes.py --selftest          # offline, no network, no pandas

THIS IS NOT SYNERGY AND MUST NEVER BE LABELLED AS IF IT WERE
The thing people picture when they say "play types" is Synergy's: Spot Up, Off Screen, Handoff,
P&R Roll Man, Isolation. Those are tagged possessions - a human or Synergy's own model decides
what the offence RAN. Nothing in this repo has them and ESPN does not publish them, so they are
not in this file and no amount of arithmetic here would produce them.

What ESPN does carry is an action label on every field-goal attempt: 51 distinct values across
235,319 attempts in a season, from "Pullup Jump Shot" to "Cutting Dunk Shot" to "Tip Shot". That
describes the SHOT, not the set that created it. A spot-up three and a three off a flare screen
both arrive here as "Jump Shot", and there is no way to separate them. So the grouping below is
honest about what it is: shot actions, named after what the shooter did.

WHAT IS DELIBERATELY ABSENT
  Spot Up, Off Screen, Handoff, P&R Roll Man - need the possession tagged, which ESPN does not do.
  Catch & Shoot - an assist is only recorded on a MADE basket. Catch-and-shoot misses are
    invisible, so a frequency built from assists would be wrong by the whole miss column, and
    wrong by more for bad shooters than good ones. A number that breaks hardest exactly where it
    matters is worse than no number.

WHY IT IS WORTH HAVING ANYWAY
Position defence treats every centre alike. Shot zones say WHERE he shoots. This says HOW he gets
there - and the two are not the same question. Two centres who both live at the rim are different
propositions if one gets there by cutting and the other by rolling off an offensive rebound, and
they meet different defences: the same club can be excellent at walling up drives and poor at
boxing out. Both halves of that are computed here from the same feed, so the player's frequency
and the defence's concession are on one scale and comparable.

RANK DIRECTION - matches zones.json, which the page already shows beside this
  rank 1 = TOUGHEST, the defence allowing the lowest percentage on that action.
  rank 30 = softest.
The reference material people bring to this usually runs the other way, so the page prints the
word as well as the number. Two numbers on one screen that count in opposite directions is a bug
whichever way round they are.

THREE-POINTERS
Needed for effective field goal percentage. On a MADE shot score_value is authoritative. On a miss
there is no score_value, so the text is read for "three point" - which disagrees with score_value
on 3.3% of makes, and presumably about as often on misses. eFG here is therefore accurate to about
a third of a percentage point, which is fine for ranking thirty clubs and is stated on the page.
"""

import argparse
import datetime
import io
import json
import os
import sys
import urllib.request

REL = 'https://github.com/sportsdataverse/sportsdataverse-data/releases/download'
PBP = REL + '/espn_nba_pbp/play_by_play_%s.parquet'
BOX = REL + '/espn_nba_player_boxscores/player_box_%s.parquet'

# All-Star and other exhibition "clubs" arrive in the feed looking like any other team and nobody
# guards anybody in them, so they post absurd percentages and displace real clubs in every ranking.
# Same floor and same reasoning as build_nba_zones.py: across five seasons the largest exhibition
# entry faced 67 shots and the smallest real club 6,838, so a thousand sits inside a hundredfold gap.
MIN_DEF_ATT = 1000
MIN_PLAYER_SHOTS = 25          # same floor zones.json uses, so the two files name the same men
MIN_ACTION_ATT = 10            # below this a player's action rate is noise and is dropped
# Below this share of real between-club variance the defensive ranking is not worth printing.
# 0.5 is a deliberate choice rather than a found one: at half, the better-ranked club is right
# about two times in three, which is weak but not nothing. Under it the order is mostly luck.
RANK_REAL_FLOOR = 0.5

# Ordered: the FIRST rule that matches wins, so the more specific descriptor takes precedence.
# "Running Pullup Jump Shot" is a pull-up, not a run-out. "Running Alley Oop Dunk Shot" is a lob.
# "Driving Floating Bank Jump Shot" is a drive. Order is the whole of the logic here, so the
# selftest below pins every one of the 51 values ESPN actually uses against its expected group.
ACTIONS = [
    ('putback',  'Putback',      ('tip', 'putback')),
    ('lob',      'Lob',          ('alley oop',)),
    ('cut',      'Cut',          ('cutting',)),
    ('stepback', 'Step-back',    ('step back',)),
    ('pullup',   'Pull-up',      ('pullup', 'pull up')),
    ('post',     'Post / fade',  ('turnaround', 'fade away', 'fadeaway', 'hook')),
    ('drive',    'Drive',        ('driving',)),
    ('runout',   'Run-out',      ('running',)),
    ('float',    'Floater',      ('floating',)),
    ('rim',      'At the rim',   ('dunk', 'layup', 'finger roll')),
    ('jumper',   'Catch jumper', ('jump shot', 'jumper')),
]
ACTION_KEYS = [a[0] for a in ACTIONS]
ACTION_LABEL = {a[0]: a[1] for a in ACTIONS}


def classify(type_text):
    """ESPN's action label -> one of ACTION_KEYS, or '' when it is not a field goal we count."""
    t = (type_text or '').strip().lower()
    if not t or t.startswith('free throw') or t.startswith('no shot'):
        return ''
    for key, _label, needles in ACTIONS:
        for n in needles:
            if n in t:
                return key
    return ''


def grab(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (JTT build)'})
    with urllib.request.urlopen(req, timeout=300) as r:
        return io.BytesIO(r.read())


def pid(v):
    t = str(v).strip()
    if t.endswith('.0'):
        t = t[:-2]
    return t if t.isdigit() else ''


def rate(made, att):
    return round(made / att, 4) if att else None


def efg_of(made, threes_made, att):
    """(FG + 0.5 * 3PM) / FGA - a three is worth half a basket more, so a 35% three-point shooter
    and a 52% finisher are the same shot. Without it every perimeter man looks inefficient."""
    return round((made + 0.5 * threes_made) / att, 4) if att else None


def build(seasons, out_dir, verbose=True):
    import pandas as pd

    names = {}
    # key -> [att, made, threes]
    pl = {}          # (playerId, action)
    dfn = {}         # (defTeam, action)
    lg = {}          # action
    pl_team = {}
    seen = []

    for season in seasons:
        try:
            d = pd.read_parquet(grab(PBP % season), columns=[
                'type_text', 'text', 'scoring_play', 'shooting_play', 'score_value',
                'athlete_id_1', 'team_id', 'home_team_id',
                'home_team_abbrev', 'away_team_abbrev'])
        except Exception as e:
            if verbose:
                print('  %s: play-by-play not available (%s)' % (season, str(e)[:70]))
            continue
        seen.append(season)
        try:
            box = pd.read_parquet(grab(BOX % season),
                                  columns=['athlete_id', 'athlete_display_name', 'team_abbreviation'])
            for _, r in box.drop_duplicates(subset=['athlete_id']).iterrows():
                k = pid(r['athlete_id'])
                if k:
                    names[k] = str(r['athlete_display_name'])
                    pl_team[k] = str(r.get('team_abbreviation') or '')
        except Exception:
            pass

        sh = d[d.shooting_play.fillna(False).astype(bool)].copy()
        sh['act'] = sh.type_text.map(classify)
        sh = sh[sh.act != '']
        made = sh.scoring_play.fillna(False).astype(bool)
        sv = sh.score_value.fillna(0).astype(int)
        # authoritative where it exists, text only where it cannot
        is3 = (sv == 3) | (~made & sh.text.fillna('').str.contains('three point', case=False))

        # the defending club is whichever of the two is not the shooter's
        home_id = sh.home_team_id.astype(str)
        tid = sh.team_id.astype(str)
        defend = sh.away_team_abbrev.where(tid == home_id, sh.home_team_abbrev)

        for act, m, three, who, dfd in zip(sh.act.tolist(), made.tolist(), is3.tolist(),
                                           sh.athlete_id_1.tolist(), defend.tolist()):
            a = lg.setdefault(act, [0, 0, 0])
            a[0] += 1; a[1] += int(m); a[2] += int(m and three)
            k = pid(who)
            if k:
                r = pl.setdefault((k, act), [0, 0, 0])
                r[0] += 1; r[1] += int(m); r[2] += int(m and three)
            t = str(dfd or '').strip()
            if t:
                r = dfn.setdefault((t, act), [0, 0, 0])
                r[0] += 1; r[1] += int(m); r[2] += int(m and three)
        if verbose:
            print('  %s: %d classified attempts' % (season, len(sh)))

    if not seen:
        raise SystemExit('no seasons read; nothing written')

    lg_total = sum(v[0] for v in lg.values())
    league = {k: {'att': v[0], 'freq': round(v[0] / lg_total, 4) if lg_total else None,
                  'pct': rate(v[1], v[0]), 'efg': efg_of(v[1], v[2], v[0])}
              for k, v in lg.items()}

    # --- defences ---------------------------------------------------------------------------
    tot_by_team = {}
    for (t, _a), v in dfn.items():
        tot_by_team[t] = tot_by_team.get(t, 0) + v[0]
    real = sorted(t for t, n in tot_by_team.items() if n >= MIN_DEF_ATT)
    defence = {}
    for t in real:
        rec = {'team': t}
        for a in ACTION_KEYS:
            v = dfn.get((t, a))
            if not v or not v[0]:
                continue
            rec[a] = {'att': v[0], 'pct': rate(v[1], v[0]), 'efg': efg_of(v[1], v[2], v[0]),
                      'freq': round(v[0] / tot_by_team[t], 4)}
        defence[t] = rec
    # rank 1 = toughest = lowest eFG allowed. eFG and not FG%, or every club that funnels you into
    # threes ranks well for conceding a lower percentage on shots worth more.
    for a in ACTION_KEYS:
        have = [t for t in real if defence[t].get(a) and defence[t][a]['efg'] is not None]
        have.sort(key=lambda t: defence[t][a]['efg'])
        for i, t in enumerate(have):
            defence[t][a]['rank'] = i + 1
            defence[t][a]['of'] = len(have)

    # --- players ----------------------------------------------------------------------------
    tot_by_player = {}
    for (k, _a), v in pl.items():
        tot_by_player[k] = tot_by_player.get(k, 0) + v[0]
    players = []
    for k, tot in tot_by_player.items():
        if tot < MIN_PLAYER_SHOTS or k not in names:
            continue
        rec = {'playerId': k, 'name': names[k], 'team': pl_team.get(k, ''), 'shots': tot}
        kept = 0
        for a in ACTION_KEYS:
            v = pl.get((k, a))
            if not v or v[0] < MIN_ACTION_ATT:
                continue
            rec[a] = {'att': v[0], 'share': round(v[0] / tot, 4),
                      'pct': rate(v[1], v[0]), 'efg': efg_of(v[1], v[2], v[0])}
            kept += 1
        if kept:
            players.append(rec)
    players.sort(key=lambda r: -r['shots'])

    # --- is the gap between clubs bigger than chance? ----------------------------------------
    # A rank is only worth printing if the clubs actually differ. The median defence faces about
    # 108 floaters in a season, and 108 coin flips scatter by +-9 points on their own - so a
    # floater ranking would order thirty clubs almost entirely on luck while looking exactly like
    # the drive ranking, which rests on 1,635 attempts and is real.
    #
    # The split is cheap to measure. Observed variance between clubs, minus the binomial variance
    # you would see if every club were identical, leaves the part that belongs to the defences.
    # Negative means the clubs are indistinguishable. The page reads `real` and refuses to print a
    # rank below a floor, which is the only thing standing between this file and thirty confident
    # orderings of noise.
    spread = {}
    for a in ACTION_KEYS:
        vals = [(defence[t][a]['efg'], defence[t][a]['att'])
                for t in real if defence[t].get(a) and defence[t][a].get('efg') is not None]
        if len(vals) < 10:
            continue
        n = len(vals)
        mean = sum(v for v, _ in vals) / n
        obs = sum((v - mean) ** 2 for v, _ in vals) / n
        # eFG is not a plain proportion, so the binomial term is scaled by the same factor the
        # metric applies; p(1-p)/n understates it slightly, which makes this test conservative -
        # it will call something noise sooner than it will wave it through.
        noise = sum(mean * (1 - mean) / att for _, att in vals) / n
        frac = (obs - noise) / obs if obs > 0 else 0.0
        spread[a] = {'obs': round(obs ** 0.5, 5), 'noise': round(noise ** 0.5, 5),
                     'real': round(max(0.0, min(1.0, frac)), 3),
                     'medAtt': int(sorted(att for _, att in vals)[n // 2])}

    out = {'seasons': seen, 'actions': ACTION_KEYS, 'labels': ACTION_LABEL,
           'minShots': MIN_PLAYER_SHOTS, 'minActionAtt': MIN_ACTION_ATT,
           'league': league, 'defence': defence, 'players': players, 'spread': spread,
           'rankFloor': RANK_REAL_FLOOR,
           'source': 'ESPN play-by-play shot actions (sportsdataverse). NOT Synergy play types.',
           'updated': __import__('datetime').datetime.now(
               __import__('datetime').timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'playtypes.json')
    with io.open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, separators=(',', ':'))
    if verbose:
        print('wrote %s  (%d players, %d defences)' % (path, len(players), len(defence)))
        print('  between-club spread that is real, not sampling noise:')
        for a in ACTION_KEYS:
            if a in spread:
                sp = spread[a]
                print('    %-13s %4.0f%%  (median %d faced)%s' % (
                    ACTION_LABEL[a], sp['real'] * 100, sp['medAtt'],
                    '' if sp['real'] >= RANK_REAL_FLOOR else '   <- no rank printed'))
        print('  league: ' + '  '.join(
            '%s %.1f%% of shots, %.1f%% eFG' % (ACTION_LABEL[a], league[a]['freq'] * 100,
                                                league[a]['efg'] * 100)
            for a in ACTION_KEYS if a in league))
    return out


# Every type_text ESPN actually used last season, with the group it must land in. This is the
# whole of the classifier's correctness: the rules are order-dependent and a reordering that looks
# harmless silently moves thousands of shots between groups.
SELFTEST = [
    ('Jump Shot', 'jumper'), ('Jump Shot Bank', 'jumper'),
    ('Pullup Jump Shot', 'pullup'), ('Running Pullup Jump Shot', 'pullup'),
    ('Pullup Bank Jump Shot', 'pullup'),
    ('Step Back Jump Shot', 'stepback'), ('Step Back Bank Jump Shot', 'stepback'),
    ('Driving Layup Shot', 'drive'), ('Driving Floating Jump Shot', 'drive'),
    ('Driving Finger Roll Layup', 'drive'), ('Driving Dunk Shot', 'drive'),
    ('Layup Driving Reverse', 'drive'), ('Driving Floating Bank Jump Shot', 'drive'),
    ('Driving Jump Shot Bank', 'drive'), ('Driving Reverse Dunk Shot', 'drive'),
    # a driving hook is a hook: the post rule sits above the drive rule on purpose
    ('Driving Hook Shot', 'post'), ('Hook Driving Bank', 'post'),
    ('Cutting Layup Shot', 'cut'), ('Cutting Dunk Shot', 'cut'),
    ('Cutting Finger Roll Layup Shot', 'cut'),
    ('Tip Shot', 'putback'), ('Layup Shot Putback', 'putback'),
    ('Tip Dunk Shot', 'putback'), ('Putback Dunk Shot', 'putback'),
    ('Alley Oop Dunk Shot', 'lob'), ('Alley Oop Layup Shot', 'lob'),
    ('Running Alley Oop Dunk Shot', 'lob'), ('Running Alley Oop Layup Shot', 'lob'),
    ('Turnaround Fade Away Jump Shot', 'post'), ('Turnaround Hook Shot', 'post'),
    ('Turnaround Jump Shot', 'post'), ('Fade Away Jump Shot', 'post'),
    ('Hook Shot', 'post'), ('Turnaround Bank Jump Shot', 'post'),
    ('Hook Turnaround Bank', 'post'), ('Fade Away Bank Jump Shot', 'post'),
    ('Turnaround Fadeaway Bank Jump Shot', 'post'), ('Hook Shot Bank', 'post'),
    ('Running Layup Shot', 'runout'), ('Running Jump Shot', 'runout'),
    ('Running Dunk Shot', 'runout'), ('Running Finger Roll Layup', 'runout'),
    ('Layup Running Reverse', 'runout'), ('Running Reverse Dunk Shot', 'runout'),
    ('Floating Jump Shot', 'float'),
    ('Layup Shot', 'rim'), ('Dunk Shot', 'rim'), ('Reverse Layup Shot', 'rim'),
    ('Reverse Dunk Shot', 'rim'), ('Finger Roll Layup', 'rim'),
    ('Free Throw - 1 of 1', ''), ('Free Throw - Technical', ''),
    ('No Shot (Default Shot)', ''), ('', ''),
]


def selftest():
    bad = 0
    for text, want in SELFTEST:
        got = classify(text)
        if got != want:
            print('  FAIL %-38s want %-9s got %s' % (text, want or "''", got or "''"))
            bad += 1
    # eFG arithmetic, checked by hand: 10 of 20 with 4 threes -> (10 + 2) / 20 = 0.6
    if efg_of(10, 4, 20) != 0.6:
        print('  FAIL efg_of(10,4,20) = %s, want 0.6' % efg_of(10, 4, 20)); bad += 1
    if efg_of(0, 0, 0) is not None:
        print('  FAIL efg_of on zero attempts must be None'); bad += 1
    if rate(5, 10) != 0.5:
        print('  FAIL rate(5,10)'); bad += 1
    # every group must be reachable, or a rule is dead and the taxonomy silently has a hole
    reached = {classify(t) for t, _ in SELFTEST} - {''}
    missing = [a for a in ACTION_KEYS if a not in reached]
    if missing:
        print('  FAIL groups no sample reaches: %s' % ', '.join(missing)); bad += 1
    print('selftest: %d cases, %d failed' % (len(SELFTEST) + 3, bad))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='nba/data')
    ap.add_argument('--seasons', default=None)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    # THE SAME ROLLING WINDOW build_nba_zones.py USES, and for the same reason: these two files sit
    # side by side on one screen, so a fixed default here would have quietly pinned shot actions to
    # last season while zones rolled onto the new one, and the page would have shown a man's shot
    # diet from one season against a defence measured in another with nothing to say it had.
    # A season with no play-by-play yet is skipped by build() rather than failing, which is what
    # makes asking for the season about to start harmless.
    today = datetime.date.today()
    nxt = today.year + 1 if today.month >= 9 else today.year
    seasons = [s.strip() for s in (a.seasons or '%d,%d' % (nxt - 1, nxt)).split(',') if s.strip()]
    build(seasons, a.out)


if __name__ == '__main__':
    main()
