# Minimal, pinned environment for reproducible linux/amd64 builds.
FROM --platform=linux/amd64 python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /app
COPY --chmod=644 selector.py /app/selector.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65534:65534
CMD ["python", "-B", "/app/selector.py"]
