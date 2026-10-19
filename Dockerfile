FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FAMILY_BUDGET_DATA_DIR=/data FAMILY_BUDGET_STATIC=/app/frontend/dist
WORKDIR /app
COPY pyproject.toml ./
COPY backend/ backend/
RUN pip install --no-cache-dir . && useradd --uid 10001 --no-create-home budget
COPY --from=frontend /build/dist frontend/dist
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["family-budget", "serve", "--container"]
