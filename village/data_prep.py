"""Build data/village/villages.json for the Village engine demo.

REAL data:
  - Village names + coordinates: OpenStreetMap (Overpass API), place=village
    inside Tuljapur taluka (OSM relation 10349973), Dharashiv district
    (Osmanabad in Census 2011). Raw response: data/village/osm_tuljapur_raw.json
  - Population: Census 2011 village totals for Tuljapur taluka (Census
    sub-district 04241), as tabulated by census2011.co.in (a secondary
    reproduction of the Census 2011 PCA; the official PCA xlsx could not be
    downloaded). Only the extracted table is kept:
    data/village/census2011_tuljapur_villages.csv (code, name, population);
    see data/village/README.md for the source note.
    Joined to OSM by fuzzy name match (match rate printed + saved).
  - Filling points: Tuljapur town (taluka HQ, tanker base) and, optionally,
    Naldurg town, both from OSM. ASSUMPTION: plausible tanker filling points,
    not confirmed ones.

SIMULATED (fixed seed, every such field ends in "_sim"):
  livestock counts, local-source-dry status, days since last tanker, request date.

Run from repo root:  .venv\\Scripts\\python.exe -m village.data_prep [--refresh]
"""

import argparse
import csv
import datetime as dt
import difflib
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "village"
OSM_RAW = DATA / "osm_tuljapur_raw.json"
CENSUS_CSV = DATA / "census2011_tuljapur_villages.csv"
OUT = DATA / "villages.json"

TALUKA_REL_ID = 10349973  # OSM relation: Tuljapur taluka (admin_level 6), Dharashiv district
OVERPASS_QUERY = f"""[out:json][timeout:120];
area(id:{3600000000 + TALUKA_REL_ID})->.t;
(
  node(area.t)["place"~"village|town|hamlet"];
  nwr(area.t)["water"="reservoir"];
  nwr(area.t)["landuse"="reservoir"];
  nwr(area.t)["waterway"="dam"];
);
out center tags;"""
OVERPASS_MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
CENSUS_URL = "https://www.census2011.co.in/data/subdistrict/4241-tuljapur-osmanabad-maharashtra.html"
USER_AGENT = "maharashtra-drought-water-ops/0.1 (hackathon demo)"

MATCH_THRESHOLD = 0.80  # counted as a match for the reported match rate
DEMO_MATCH_THRESHOLD = 0.90  # only high-confidence matches are used in the demo
N_DEMO_VILLAGES = 32
SEED = 2026
REQUEST_DAY = dt.date(2026, 10, 8)
FILL_POINT_NAMES = ["Tuljapur", "Naldurg"]  # [base, optional second filling point]


def fetch(refresh: bool) -> None:
    if refresh or not OSM_RAW.exists():
        data = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode()
        for url in OVERPASS_MIRRORS:
            try:
                req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
                OSM_RAW.write_bytes(urllib.request.urlopen(req, timeout=180).read())
                break
            except Exception as exc:  # mirrors are often overloaded
                print(f"{url} failed: {exc}")
        else:
            sys.exit("All Overpass mirrors failed")
    if refresh or not CENSUS_CSV.exists():
        req = urllib.request.Request(CENSUS_URL, headers={"User-Agent": USER_AGENT})
        html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", errors="replace")
        write_census_csv(parse_census_html(html))  # keep only the extracted table, not the page


def parse_census_html(html: str) -> list[dict]:
    """Village rows (Census 2011 location code, name, population) from the census2011.co.in table."""
    rows = re.findall(r'href="/data/village/(\d+)-[^"]*">([^<]+)</a></td>\s*<td>[^<]*</td>\s*<td>([\d,]+)</td>', html)
    return [{"code": c, "name": n.strip(), "population": int(p.replace(",", ""))} for c, n, p in rows]


def write_census_csv(rows: list[dict]) -> None:
    with CENSUS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["code", "name", "population"])
        w.writeheader()
        w.writerows(rows)


def read_census_csv() -> list[dict]:
    with CENSUS_CSV.open(newline="", encoding="utf-8") as f:
        return [{"code": r["code"], "name": r["name"], "population": int(r["population"])} for r in csv.DictReader(f)]


def norm(name: str) -> str:
    """Normalise Marathi-to-Latin transliteration variants before fuzzy matching."""
    s = re.sub(r"\(.*?\)", "", name.lower())
    s = re.sub(r"[^a-z ]", "", s)
    for a, b in [("w", "v"), ("ee", "i"), ("oo", "u"), ("aa", "a"), ("ph", "f"), ("bh", "b"), ("dh", "d"),
                 ("th", "t"), ("kh", "k"), ("gh", "g"), ("sh", "s"), ("ch", "c"), ("z", "j"), ("y", "i")]:
        s = s.replace(a, b)
    return s.replace(" ", "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download OSM data and the Census table")
    fetch(ap.parse_args().refresh)

    elements = json.loads(OSM_RAW.read_text(encoding="utf-8"))["elements"]
    osm = [e for e in elements if e["tags"].get("place") == "village" and e["tags"].get("name")]
    towns = {e["tags"]["name"]: e for e in elements if e["tags"].get("place") == "town"}

    census = read_census_csv()

    # One-to-one greedy fuzzy match on normalised names.
    scored = sorted(((difflib.SequenceMatcher(None, norm(o["tags"]["name"]), norm(c["name"])).ratio(), i, j)
                     for i, o in enumerate(osm) for j, c in enumerate(census)), reverse=True)
    used_o, used_c, matches = set(), set(), {}
    for s, i, j in scored:
        if s < MATCH_THRESHOLD:
            break
        if i not in used_o and j not in used_c:
            used_o.add(i), used_c.add(j)
            matches[i] = (round(s, 3), census[j])
    unmatched = [osm[i]["tags"]["name"] for i in range(len(osm)) if i not in matches]
    print(f"OSM villages: {len(osm)} | Census villages: {len(census)} | matched (>= {MATCH_THRESHOLD}): "
          f"{len(matches)} ({len(matches) / len(osm):.0%})")

    rng = np.random.default_rng(SEED)
    confident = sorted(i for i, (s, _) in matches.items() if s >= DEMO_MATCH_THRESHOLD)
    chosen = sorted(rng.choice(confident, size=N_DEMO_VILLAGES, replace=False))

    villages = []
    for k, i in enumerate(chosen):
        o, (score, c) = osm[i], matches[i]
        pop = c["population"]
        villages.append({
            "id": f"V{k + 1:02d}",
            "name": o["tags"]["name"],
            "name_mr": o["tags"].get("name:mr"),
            "lat": round(o["lat"], 6),
            "lon": round(o["lon"], 6),
            "osm_node_id": o["id"],
            "population": pop,
            "census2011_code": c["code"],
            "census2011_name": c["name"],
            "name_match_score": score,
            # ---- SIMULATED below (seed SEED) ----
            "large_animals_sim": int(round(pop * rng.uniform(0.15, 0.35))),
            "small_animals_sim": int(round(pop * rng.uniform(0.10, 0.40))),
            "source_dry_sim": bool(rng.random() < 0.55),
            "days_since_last_tanker_sim": int(rng.integers(0, 13)),
            "request_date_sim": (REQUEST_DAY - dt.timedelta(days=int(rng.integers(0, 10)))).isoformat(),
        })

    out = {
        "label": "Tuljapur taluka, Dharashiv district (real OSM village locations, Census 2011 population); "
                 "livestock / source status / tanker history / requests SIMULATED",
        "sources": {
            "village_locations": f"OpenStreetMap via Overpass API, place=village in OSM relation {TALUKA_REL_ID} "
                                 "(Tuljapur taluka). (c) OpenStreetMap contributors, ODbL.",
            "population": f"Census 2011 village totals, Tuljapur sub-district 04241 (Osmanabad district), via {CENSUS_URL}",
            "fill_point": "OSM place=town 'Tuljapur' (taluka HQ) - ASSUMED filling point, not confirmed",
            "fill_points": "Tuljapur (base) + Naldurg (optional second), OSM place=town - ASSUMED, not confirmed",
        },
        "simulated_fields": ["large_animals_sim", "small_animals_sim", "source_dry_sim",
                             "days_since_last_tanker_sim", "request_date_sim"],
        "simulation_seed": SEED,
        "census_match": {
            "osm_villages": len(osm), "census_villages": len(census), "matched": len(matches),
            "match_rate": round(len(matches) / len(osm), 3), "threshold": MATCH_THRESHOLD,
            "demo_uses_threshold": DEMO_MATCH_THRESHOLD, "unmatched_osm_names": unmatched,
        },
        "fill_points": [
            {"name": n, "lat": round(towns[n]["lat"], 6), "lon": round(towns[n]["lon"], 6), "osm_node_id": towns[n]["id"],
             "note": "ASSUMED " + ("tanker base / filling point (taluka HQ town)" if i == 0 else "optional second filling point")}
            for i, n in enumerate(FILL_POINT_NAMES)],
        "villages": villages,
    }
    out["fill_point"] = out["fill_points"][0]  # backwards compatible single filling point
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {OUT} with {len(villages)} villages")


if __name__ == "__main__":
    main()
