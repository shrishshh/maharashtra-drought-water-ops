# Project: Maharashtra Drought Water Ops
Hackathon: WeMakeDevs x AWS "Environmental Hacks", Oct 8-11 2026. Track: Heat and Water.
One web app, two modules:
- City module: helps a municipal body implement Maharashtra's mandatory 10% water cut (from Oct 16, 2026) fairly, using EPANET hydraulic simulation via the WNTR Python library. Compares a "blunt cut" vs rotating ward schedules, flags likely leak zones from night-flow anomalies, sends ward-wise timings in Marathi.
- Village module: for drought-hit talukas (demo district: Dharashiv). Ranks villages by water need, plans tanker routes (Google OR-Tools vehicle routing), flags suspicious tanker trips by comparing GPS traces vs claimed trips.
Stack: Python 3.12, wntr, OR-Tools, pandas; AWS (region ap-south-1): Lambda (container images), API Gateway, DynamoDB, S3, Step Functions, Amazon Location Service, Amazon Translate, SNS, Amplify Hosting; frontend React + Leaflet.
Folder layout: city/ (City engine), village/ (Village engine), backend/ (AWS infra + Lambda handlers), frontend/, data/, outputs/.
Rules to respect:
- All code is written fresh during the event; commit often with clear messages.
- Never commit AWS keys or .env files (keep them in .gitignore).
- Keep a list of AI tools used in README (Claude Code).
- Ask before running any command that creates or deletes AWS resources.
- Simulated/sample data must be labelled clearly as simulated in code and UI.
