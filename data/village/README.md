# Village data (Tuljapur taluka, Dharashiv district)

| File | What it is | Source |
|---|---|---|
| `osm_tuljapur_raw.json` | Raw Overpass API response: villages, towns and reservoirs inside Tuljapur taluka (OSM relation 10349973) | © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright) |
| `census2011_tuljapur_villages.csv` | Census 2011 village totals for Tuljapur sub-district (04241), Osmanabad district: `code` (Census 2011 location code), `name`, `population` | Census of India 2011, as tabulated by census2011.co.in (see below) |
| `villages.json` | The 32 demo villages: OSM name + location joined to Census population by fuzzy name match, plus SIMULATED fields ending in `_sim` | built by `python -m village.data_prep` |
| `route_matrix_tuljapur.json` | Road distance/time matrix from Amazon Location Service. **Not in git**: AWS Service Terms allow caching route results for at most 30 days | `scripts/build_route_matrix.py` |

## Census population: source note

The official Census 2011 Primary Census Abstract spreadsheet could not be downloaded
during the hackathon, so village populations were taken from the Census 2011 table for
Tuljapur taluka published at:

https://www.census2011.co.in/data/subdistrict/4241-tuljapur-osmanabad-maharashtra.html

Only the extracted table (123 villages: Census location code, name, population) is kept
here, not the web page. `python -m village.data_prep --refresh` re-downloads the page and
rewrites the CSV. The two Census towns (Tuljapur and Naldurg Municipal Councils) are not
villages and are excluded.

## What is simulated

Livestock counts, whether a village's own water source is dry, days since the last
tanker, and request dates are SIMULATED with a fixed seed (2026). Every simulated field
name ends in `_sim`. The filling points (Tuljapur and Naldurg towns) are assumptions.
