#!/usr/bin/env python3
"""
fetch_wc_weather.py — pulls hourly weather forecasts for WC 2026 fixtures
from Open-Meteo (free, no API key needed) and writes worldcup_weather.json.

Open-Meteo gives forecasts up to 16 days ahead. We query once per stadium
(not once per fixture — saves requests) and pick the forecast hour closest
to each fixture's kickoff timestamp.

Indoor / closed-AC venues are still fetched (it's free and lets us show
"outside it's 35°C" context even when the stadium itself is climate-controlled).
The UI decides whether to surface weather data based on roof type.

Usage:
    python fetch_wc_weather.py
    python fetch_wc_weather.py --fixtures path/to/fixtures.json --out path/to/weather.json

Output JSON shape:
    {
      "updatedAt": "2026-06-07T09:00:00Z",
      "source":    "open-meteo",
      "byMatch": {
        "1489369": {
          "tempC":              28.5,
          "humidity":           70,
          "precipitationProb":  15,
          "windKph":            12,
          "weatherCode":        2,
          "condition":          "partly_cloudy",   # derived label
          "kickoffISO":         "2026-06-12T05:00:00Z",
          "stadium":            "Estadio Azteca"
        },
        ...
      }
    }
"""

import argparse
import datetime as dt
import json
import time
from pathlib import Path
from urllib import request, parse, error

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# --- Stadium → (lat, lng) — MUST stay in lockstep with the STADIUM_INFO
# object in JTT_WC.html. If you add a venue there, add it here too.
STADIUM_LATLNG = {
    "MetLife Stadium":           (40.8135,  -74.0744),
    "AT&T Stadium":              (32.7473,  -97.0945),
    "Mercedes-Benz Stadium":     (33.7553,  -84.4006),
    "NRG Stadium":               (29.6847,  -95.4107),
    "Hard Rock Stadium":         (25.9580,  -80.2389),
    "SoFi Stadium":              (33.9535, -118.3392),
    "Lumen Field":               (47.5953, -122.3316),
    "Levi's Stadium":            (37.4032, -121.9698),
    "Lincoln Financial Field":   (39.9008,  -75.1675),
    "Gillette Stadium":          (42.0909,  -71.2643),
    "Arrowhead Stadium":         (39.0489,  -94.4839),
    "BMO Field":                 (43.6332,  -79.4185),
    "BC Place":                  (49.2768, -123.1119),
    "Estadio Azteca":            (19.3029,  -99.1505),
    "Estadio Akron":             (20.6824, -103.4622),
    "Estadio BBVA":              (25.6694, -100.2444),
}

# Open-Meteo "weather_code" → human label
# Truncated to the codes we actually care about for UI labelling.
# Full list at https://open-meteo.com/en/docs (WMO Weather interpretation codes).
WEATHER_CODE_LABELS = {
    0:  "clear",
    1:  "mostly_clear",
    2:  "partly_cloudy",
    3:  "overcast",
    45: "fog",
    48: "fog",
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    61: "rain",
    63: "rain",
    65: "heavy_rain",
    71: "snow",
    73: "snow",
    75: "heavy_snow",
    80: "rain_shower",
    81: "rain_shower",
    82: "heavy_rain_shower",
    95: "thunderstorm",
    96: "thunderstorm_hail",
    99: "thunderstorm_hail",
}


def fetch_stadium_forecast(lat: float, lng: float) -> dict | None:
    """Hourly forecast for the next 16 days at a single point.
    Returns the raw JSON or None on failure. Keeps payload small by only
    asking for the variables we need."""
    params = {
        "latitude":  str(lat),
        "longitude": str(lng),
        "hourly":    "temperature_2m,relative_humidity_2m,precipitation_probability,wind_speed_10m,weather_code",
        "timezone":  "UTC",
        "wind_speed_unit": "kmh",
        "forecast_days": "16",
    }
    url = OPEN_METEO_URL + "?" + parse.urlencode(params)
    try:
        with request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except (error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        print(f"  ! fetch failed for {lat},{lng}: {e}")
        return None


def closest_hour_index(hourly_times_iso: list[str], target_ts: int) -> int | None:
    """Given a list of ISO hour strings (UTC), find the index closest to
    target_ts (a unix epoch). Returns None when the target is outside the
    forecast horizon (i.e. >16 days out)."""
    target_dt = dt.datetime.fromtimestamp(target_ts, tz=dt.timezone.utc)
    best_idx, best_delta = None, None
    for i, t in enumerate(hourly_times_iso):
        try:
            # Open-Meteo returns "YYYY-MM-DDTHH:00" without timezone — treat as UTC
            t_dt = dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        delta = abs((t_dt - target_dt).total_seconds())
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_idx = i
    # If best match is >2 hours off, fixture is outside the forecast window
    if best_delta is None or best_delta > 7200:
        return None
    return best_idx


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", default="data/worldcup_fixtures_2026.json",
                    help="Path to the WC fixtures JSON file.")
    ap.add_argument("--out", default="data/worldcup_weather.json",
                    help="Output path for the weather JSON.")
    args = ap.parse_args()

    fixtures_path = Path(args.fixtures)
    if not fixtures_path.exists():
        raise SystemExit(f"Fixtures file not found: {fixtures_path}")

    with fixtures_path.open() as f:
        fixtures_doc = json.load(f)
    fixtures = fixtures_doc.get("fixtures", [])

    # Bucket fixtures by venue so we only hit Open-Meteo once per stadium.
    by_venue: dict[str, list[dict]] = {}
    for fx in fixtures:
        venue = fx.get("venue")
        if not venue:
            continue
        if venue not in STADIUM_LATLNG:
            # Unknown venue (likely a name not in our lookup yet) — skip
            continue
        by_venue.setdefault(venue, []).append(fx)

    by_match: dict[str, dict] = {}
    now_ts = int(time.time())

    print(f"Fetching forecasts for {len(by_venue)} stadiums "
          f"covering {sum(len(v) for v in by_venue.values())} fixtures …")

    for venue, fxs in by_venue.items():
        lat, lng = STADIUM_LATLNG[venue]
        # Skip stadiums with no upcoming fixtures (every match in the past)
        upcoming = [fx for fx in fxs if fx.get("timestamp", 0) >= now_ts - 7200]
        if not upcoming:
            print(f"  ↩ {venue}: no upcoming fixtures, skipping")
            continue

        print(f"  → {venue} ({lat:.4f}, {lng:.4f}) — {len(upcoming)} fixture(s)")
        data = fetch_stadium_forecast(lat, lng)
        if not data or "hourly" not in data:
            continue

        hourly = data["hourly"]
        times = hourly.get("time", [])
        if not times:
            continue

        temps      = hourly.get("temperature_2m", [])
        humidities = hourly.get("relative_humidity_2m", [])
        precs      = hourly.get("precipitation_probability", [])
        winds      = hourly.get("wind_speed_10m", [])
        codes      = hourly.get("weather_code", [])

        for fx in upcoming:
            ts = fx.get("timestamp")
            if ts is None:
                continue
            idx = closest_hour_index(times, ts)
            if idx is None:
                # Outside forecast horizon — skip this fixture for now
                continue
            try:
                code = int(codes[idx]) if idx < len(codes) else None
                entry = {
                    "tempC":             round(temps[idx], 1) if idx < len(temps) else None,
                    "humidity":          int(humidities[idx]) if idx < len(humidities) else None,
                    "precipitationProb": int(precs[idx])      if idx < len(precs)      else None,
                    "windKph":           round(winds[idx], 1) if idx < len(winds)      else None,
                    "weatherCode":       code,
                    "condition":         WEATHER_CODE_LABELS.get(code, "unknown"),
                    "kickoffISO":        dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).isoformat(),
                    "stadium":           venue,
                }
            except (IndexError, TypeError):
                continue
            by_match[str(fx["matchId"])] = entry

        # Be polite — small delay between stadiums even though Open-Meteo
        # doesn't strictly require it on the free tier.
        time.sleep(0.3)

    out_doc = {
        "updatedAt": dt.datetime.utcnow().isoformat() + "Z",
        "source":    "open-meteo",
        "byMatch":   by_match,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(out_doc, f, separators=(",", ":"))

    print(f"\n✓ Wrote {len(by_match)} fixture forecasts to {out_path}")


if __name__ == "__main__":
    main()
