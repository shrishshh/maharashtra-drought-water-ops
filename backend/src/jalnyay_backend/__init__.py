"""JalNyay backend: thin AWS Lambda handlers around the city/ and village/ engines.

api.py            HTTP API (API Gateway HTTP API -> Lambda): async jobs + read-only endpoints
jobstore.py       DynamoDB job rows + S3 payloads, shared by the API and workers
validation.py     job parameter validation (no heavy imports; used by API and workers)
city_worker.py    city_evaluate worker (WNTR image)
village_worker.py village_plan / village_fleet / village_fraud workers (OR-Tools image)
"""
