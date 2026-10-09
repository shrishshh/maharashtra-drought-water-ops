# city_evaluate worker. Build context = repo root (see backend/template.yaml).
FROM public.ecr.aws/lambda/python:3.12

# matplotlib (a WNTR dependency) needs a writable config dir; only /tmp is writable in Lambda
ENV MPLCONFIGDIR=/tmp/matplotlib

COPY backend/requirements-city.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements-city.txt

# Engine + data it reads (EPA Net3 sample network; Part B wards and plans, SIMULATED)
COPY city/ ${LAMBDA_TASK_ROOT}/city/
COPY data/networks/ ${LAMBDA_TASK_ROOT}/data/networks/
COPY outputs/wards.json outputs/partB_results.json ${LAMBDA_TASK_ROOT}/outputs/
COPY backend/src/jalnyay_backend/ ${LAMBDA_TASK_ROOT}/jalnyay_backend/

CMD ["jalnyay_backend.city_worker.handler"]
