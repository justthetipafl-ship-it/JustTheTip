#!/usr/bin/env python3
"""
fetch_advanced.py — optional advanced-stats layer for JTT Tennis player profiles.

Two free Sackmann sources, both ADDITIVE (sparse coverage, so the front-end
shows these sections only where data exists):

  1. Match Charting Project  -> winners / unforced errors (FH/BH split),
     winners:UFE ratio, serve placement (wide/body/T), unreturned-serve %.
     Crowd-charted, ~7.5k men's matches (+ women's), skewed to notable players.

Joins to Sackmann player_ids via the SAME matcher as the core pipeline.
Emits data/tennis_advanced.json keyed by sackmann_id:
    { "<id>": { "name":..., "adv": {...} }, ... }

Usage:
    python fetch_advanced.py
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.request

import numpy as np
import pandas as pd

from match_players import normalise, SackmannIndex, load_csv  # reuse tested matcher

MCP  = "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master"

MIN_CHARTED = 3      # require >= this many charted matches for MCP section


def log(m): print(m, file=sys.stderr)


def fetch_csv(url):
    try:
        with urllib.request.urlopen(url, timeout=40) as r:
            return pd.read_csv(io.BytesIO(r.read()))
    except Exception:
        return None


def num(s):
    return pd.to_numeric(s, errors="coerce")


# --------------------------------------------------------------------------- #
# Match Charting Project
# --------------------------------------------------------------------------- #
def load_mcp() -> dict[str, dict]:
    ov_frames, sb_frames = [], []
    for g in ("m", "w"):
        ov = fetch_csv(f"{MCP}/charting-{g}-stats-Overview.csv")
        sb = fetch_csv(f"{MCP}/charting-{g}-stats-ServeBasics.csv")
        if ov is not None: ov_frames.append(ov)
        if sb is not None: sb_frames.append(sb)
    if not ov_frames:
        log("  MCP: no Overview data fetched"); return {}
    ov = pd.concat(ov_frames, ignore_index=True)
    ov = ov[ov["set"] == "Total"].copy()
    for c in ("serve_pts","winners","winners_fh","winners_bh","unforced","unforced_fh","unforced_bh"):
        ov[c] = num(ov[c])
    g = ov.groupby("player")
    agg = pd.DataFrame({
        "matches": g["match_id"].nunique(),
        "winners": g["winners"].sum(),
        "winners_fh": g["winners_fh"].sum(),
        "winners_bh": g["winners_bh"].sum(),
        "unforced": g["unforced"].sum(),
        "unforced_fh": g["unforced_fh"].sum(),
        "unforced_bh": g["unforced_bh"].sum(),
    })
    if sb_frames:
        sb = pd.concat(sb_frames, ignore_index=True)
        sb = sb[sb["row"] == "Total"].copy()
        for c in ("pts","unret","wide","body","t"):
            sb[c] = num(sb[c])
        sg = sb.groupby("player")
        agg = agg.join(pd.DataFrame({
            "sb_pts": sg["pts"].sum(), "unret": sg["unret"].sum(),
            "wide": sg["wide"].sum(), "body": sg["body"].sum(), "t": sg["t"].sum(),
        }), how="left")

    out = {}
    for name, r in agg.iterrows():
        m = int(r["matches"])
        if m < MIN_CHARTED:
            continue
        win, ufe = r["winners"], r["unforced"]
        place = (r.get("wide", np.nan) or 0) + (r.get("body", np.nan) or 0) + (r.get("t", np.nan) or 0)
        rec = {
            "charted_matches": m,
            "winners_pm": round(win / m, 1) if win else 0,
            "ufe_pm": round(ufe / m, 1) if ufe else 0,
            "w_ufe": round(win / ufe, 2) if ufe else None,
            "fh_winner_pct": round(r["winners_fh"] / win, 3) if win else None,
            "fh_ufe_pct": round(r["unforced_fh"] / ufe, 3) if ufe else None,
        }
        if pd.notna(r.get("sb_pts")) and r.get("sb_pts"):
            pts = r["sb_pts"]
            rec["unret_pct"] = round(r["unret"] / pts, 3) if pd.notna(r["unret"]) else None
            if place:
                rec["serve_wide_pct"] = round(r["wide"] / place, 3)
                rec["serve_body_pct"] = round(r["body"] / place, 3)
                rec["serve_t_pct"] = round(r["t"] / place, 3)
        out[name] = rec
    log(f"  MCP: {len(out)} players with >= {MIN_CHARTED} charted matches")
    return out


# ----- name -> Sackmann id
# --------------------------------------------------------------------------- #
def build_resolver(atp_csv, wta_csv):
    idx = SackmannIndex(load_csv(atp_csv) + load_csv(wta_csv))
    cache = {}
    def resolve(name):
        if name in cache:
            return cache[name]
        cands = idx.exact(normalise(name))
        sid = max(cands, key=lambda c: c["active"])["player_id"] if cands else None
        cache[name] = sid
        return sid
    return resolve


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atp-csv", default="data/atp_players.csv")
    ap.add_argument("--wta-csv", default="data/wta_players.csv")
    ap.add_argument("--out", default="data/tennis_advanced.json")
    args = ap.parse_args()

    log("JTT Tennis advanced stats")
    mcp = load_mcp()

    resolve = build_resolver(args.atp_csv, args.wta_csv)
    merged, unresolved = {}, 0
    for name, adv in mcp.items():
        sid = resolve(name)
        if not sid: unresolved += 1; continue
        merged.setdefault(sid, {"name": name})["adv"] = adv

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    json.dump({"n": len(merged), "players": merged},
              open(args.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    log(f"  merged {len(merged)} players  (unresolved names: {unresolved})")
    log(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
