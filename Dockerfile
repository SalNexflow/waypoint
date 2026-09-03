# Python 3.12, not 3.14: OR-Tools wheels lag new CPython releases, and
# discovering that at phase 4 would be an annoying surprise.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Requirements copied and installed before the source, so editing a .py file
# does not invalidate the pip layer and trigger a full reinstall.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY api ./api
COPY routing ./routing
COPY solver ./solver
COPY bench ./bench
COPY worker ./worker
COPY dispatch ./dispatch
COPY data ./data
COPY tests ./tests
COPY pytest.ini .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
