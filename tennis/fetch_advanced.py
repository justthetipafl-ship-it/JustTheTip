#!/usr/bin/env python3
"""
fetch_advanced.py — advanced player profiles from the Match Charting Project.

MCP is crowd-charted, current (2025/2026 matches present), all surfaces. Coverage
is uneven (deep for notable players, sparse for the field), so every metric here
is ADDITIVE — the front-end shows these sections only where data exists.

Pulls all useful MCP stat files and aggregates per player:
  Overview         winners / unforced (FH-BH split), winner:UFE ratio
  ServeBasics      serve placement (wide/body/T), unreturned-serve %
  KeyPointsServe   break points SAVED while serving (clutch serve)
  KeyPointsReturn  break points CONVERTED while returning (clutch return)
  NetPoints        net-points-won %, net approaches per match
  Rally            win % by rally length (1-3 / 4-6 / 7+ shots) — style fingerprint
  ReturnDepth      deep-return %
  SnV              serve-and-volley frequency + win %

Joins to Sackmann ids via the core matcher. Emits data/tennis_advanced.json:
    { "<id>": { "name":..., "adv": {...} }, ... }
"""
from __future__ import annotations
import argparse, io, json, os, sys, urllib.request
import numpy as np, pandas as pd
from match_players import normalise, SackmannIndex, load_csv

MCP = "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master"
MIN_CHARTED = 3

def log(m): print(m, file=sys.stderr)

def fetch_csv(name):
    frames = []
    for g in ("m", "w"):
        try:
            with urllib.request.urlopen(f"{MCP}/charting-{g}-stats-{name}.csv", timeout=60) as r:
                frames.append(pd.read_csv(io.BytesIO(r.read())))
        except Exception:
            pass
    return pd.concat(frames, ignore_index=True) if frames else None

def fetch_matches():
    frames = []
    for g in ("m", "w"):
        try:
            with urllib.request.urlopen(f"{MCP}/charting-{g}-matches.csv", timeout=60) as r:
                frames.append(pd.read_csv(io.BytesIO(r.read())))
        except Exception:
            pass
    return pd.concat(frames, ignore_index=True) if frames else None

def to_num(df, cols):
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def rate(num, den):
    return round(float(num) / float(den), 3) if den else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atp-csv", default="data/atp_players.csv")
    ap.add_argument("--wta-csv", default="data/wta_players.csv")
    ap.add_argument("--out", default="data/tennis_advanced.json")
    args = ap.parse_args()
    log("JTT Tennis advanced stats (Match Charting Project)")

    acc = {}
    def slot(name): return acc.setdefault(name, {})

    ov = fetch_csv("Overview")
    if ov is None:
        log("  FATAL: no Overview data"); return 1
    ov = ov[ov["set"] == "Total"].copy()
    ov = to_num(ov, ["winners","winners_fh","winners_bh","unforced","unforced_fh","unforced_bh"])
    g = ov.groupby("player")
    for name, r in pd.DataFrame({
        "m": g["match_id"].nunique(), "win": g["winners"].sum(), "wfh": g["winners_fh"].sum(),
        "ufe": g["unforced"].sum(), "ufh": g["unforced_fh"].sum(),
    }).iterrows():
        d = slot(name)
        d["charted_matches"] = int(r["m"])
        d["winners_pm"] = round(r["win"]/r["m"],1) if r["win"] else 0
        d["ufe_pm"] = round(r["ufe"]/r["m"],1) if r["ufe"] else 0
        d["w_ufe"] = round(r["win"]/r["ufe"],2) if r["ufe"] else None
        d["fh_winner_pct"] = rate(r["wfh"], r["win"])
        d["fh_ufe_pct"] = rate(r["ufh"], r["ufe"])
    log(f"  Overview: {len(acc)} players")

    sb = fetch_csv("ServeBasics")
    if sb is not None:
        sb = to_num(sb[sb["row"]=="Total"].copy(), ["pts","unret","wide","body","t"]).groupby("player").sum(numeric_only=True)
        for name, r in sb.iterrows():
            if name not in acc: continue
            place = (r.get("wide",0) or 0)+(r.get("body",0) or 0)+(r.get("t",0) or 0)
            d = acc[name]
            d["unret_pct"] = rate(r.get("unret",0), r.get("pts",0))
            if place:
                d["serve_wide_pct"]=rate(r["wide"],place); d["serve_body_pct"]=rate(r["body"],place); d["serve_t_pct"]=rate(r["t"],place)

    kps = fetch_csv("KeyPointsServe")
    if kps is not None:
        bp = to_num(kps[kps["row"]=="BP"].copy(), ["pts","pts_won"]).groupby("player").sum(numeric_only=True)
        for name, r in bp.iterrows():
            if name in acc and r.get("pts",0)>=10: acc[name]["bp_save_pct"]=rate(r["pts_won"], r["pts"])

    kpr = fetch_csv("KeyPointsReturn")
    if kpr is not None:
        bpo = to_num(kpr[kpr["row"]=="BPO"].copy(), ["pts","pts_won"]).groupby("player").sum(numeric_only=True)
        for name, r in bpo.iterrows():
            if name in acc and r.get("pts",0)>=10: acc[name]["bp_conv_pct"]=rate(r["pts_won"], r["pts"])

    npf = fetch_csv("NetPoints")
    if npf is not None:
        nt = to_num(npf[npf["row"]=="NetPoints"].copy(), ["net_pts","pts_won"])
        gg = nt.groupby("player")
        for name, r in pd.DataFrame({"m":gg["match_id"].nunique(),"np":gg["net_pts"].sum(),"w":gg["pts_won"].sum()}).iterrows():
            if name in acc and r["np"]:
                acc[name]["net_pts_pm"]=round(r["np"]/r["m"],1); acc[name]["net_won_pct"]=rate(r["w"], r["np"])

    rd = fetch_csv("ReturnDepth")
    if rd is not None:
        t = to_num(rd[rd["row"]=="Total"].copy(), ["returnable","deep","very_deep"]).groupby("player").sum(numeric_only=True)
        for name, r in t.iterrows():
            if name in acc and r.get("returnable",0)>=30:
                dp=rate((r.get("deep",0) or 0)+(r.get("very_deep",0) or 0), r["returnable"])
                if dp is not None: acc[name]["deep_return_pct"]=min(1.0, dp)

    snv = fetch_csv("SnV")
    if snv is not None:
        s = to_num(snv[snv["row"]=="SnV"].copy(), ["snv_pts","pts_won"])
        gg = s.groupby("player")
        for name, r in pd.DataFrame({"m":gg["match_id"].nunique(),"p":gg["snv_pts"].sum(),"w":gg["pts_won"].sum()}).iterrows():
            if name in acc and r["p"]>=10:
                acc[name]["snv_pm"]=round(r["p"]/r["m"],1); acc[name]["snv_won_pct"]=rate(r["w"], r["p"])

    rally = fetch_csv("Rally"); matches = fetch_matches()
    if rally is not None and matches is not None:
        mm = matches.rename(columns={"Player 1":"p1","Player 2":"p2"})[["match_id","p1","p2"]]
        rl = rally[rally["row"].isin(["1-3","4-6","7-9","10"])].copy()
        rl = to_num(rl, ["pts","pl1_won","pl2_won"]).merge(mm, on="match_id", how="left")
        bucket = {"1-3":"short","4-6":"mid","7-9":"long","10":"long"}
        rl["grp"] = rl["row"].map(bucket)
        a = rl[["p1","grp","pts","pl1_won"]].rename(columns={"p1":"player","pl1_won":"won"})
        b = rl[["p2","grp","pts","pl2_won"]].rename(columns={"p2":"player","pl2_won":"won"})
        lg = pd.concat([a,b], ignore_index=True).dropna(subset=["player"])
        gg = lg.groupby(["player","grp"]).sum(numeric_only=True)
        for name in acc:
            for grp, key in (("short","rally_short_pct"),("mid","rally_mid_pct"),("long","rally_long_pct")):
                try:
                    row = gg.loc[(name, grp)]
                    if row["pts"]>=30: acc[name][key]=rate(row["won"], row["pts"])
                except KeyError:
                    pass

    idx = SackmannIndex(load_csv(args.atp_csv) + load_csv(args.wta_csv))
    cache = {}
    def resolve(name):
        if name not in cache:
            c = idx.exact(normalise(name))
            cache[name] = max(c, key=lambda x: x["active"])["player_id"] if c else None
        return cache[name]

    merged, unresolved = {}, 0
    for name, adv in acc.items():
        if adv.get("charted_matches",0) < MIN_CHARTED: continue
        sid = resolve(name)
        if not sid: unresolved += 1; continue
        merged[sid] = {"name": name, "adv": adv}

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    json.dump({"n":len(merged),"players":merged}, open(args.out,"w",encoding="utf-8"), indent=1, ensure_ascii=False)
    log(f"  merged {len(merged)} players  (unresolved: {unresolved})")
    log(f"  wrote {args.out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
