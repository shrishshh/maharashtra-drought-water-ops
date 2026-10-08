# maharashtra-drought-water-ops

Water operations toolkit for Maharashtra's drought response (WeMakeDevs x AWS "Environmental Hacks", Oct 2026, Heat and Water track).
See [CLAUDE.md](CLAUDE.md) for project scope and rules.

## Setup (local)

```powershell
python -m venv .venv          # Python 3.12
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Part A - City engine proof of concept (SIMULATED)

```powershell
.\.venv\Scripts\python.exe city\partA_blunt_cut.py
```

Runs on the EPA Net3 sample network (`data/networks/Net3.inp`), a representative
sample network, **not** a real Maharashtra network. Writes `outputs/partA_summary.csv`
and `outputs/partA_maps.png`.

## AI tools used

- Claude Code (Anthropic) - code generation, debugging, and documentation.
