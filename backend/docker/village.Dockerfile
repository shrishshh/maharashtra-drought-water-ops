# village_plan / village_fleet / village_fraud workers (handler set per function
# via ImageConfig.Command). Build context = repo root (see backend/template.yaml).
FROM public.ecr.aws/lambda/python:3.12

COPY backend/requirements-village.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements-village.txt

# Engine + data it reads (real OSM/Census village data + SIMULATED fields; Part C plan for the fraud demo)
COPY village/ ${LAMBDA_TASK_ROOT}/village/
COPY data/village/villages.json ${LAMBDA_TASK_ROOT}/data/village/
COPY outputs/partC_results.json ${LAMBDA_TASK_ROOT}/outputs/
COPY backend/src/jalnyay_backend/ ${LAMBDA_TASK_ROOT}/jalnyay_backend/

CMD ["jalnyay_backend.village_worker.plan_handler"]
