FROM python:3.11-slim@sha256:a01e48f10f90ac3ee65ef6937b9d7c831a3570c81b98d39a547ff09fb359de7f AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim@sha256:a01e48f10f90ac3ee65ef6937b9d7c831a3570c81b98d39a547ff09fb359de7f
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/bash -m appuser
WORKDIR /app
COPY --from=builder /root/.local /home/appuser/.local
COPY ./app /app/app
ENV PATH=/home/appuser/.local/bin:$PATH
# Berechtigung für SQLite-Datenbank erstellen
RUN touch /app/minisashimi.db && mkdir -p /app/keys /app/audit && chown -R appuser:appgroup /app
USER appuser
EXPOSE 8000
ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
