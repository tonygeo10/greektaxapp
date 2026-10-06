FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    DIGEST_DB=/data/digest.db \
    DIGEST_FETCH_HOURS=6

WORKDIR /app
COPY digest.py textutil.py sources.json ./
COPY web ./web

# Run as a normal user; /data is where the database lives (mount a volume here to keep it).
RUN useradd -m app && mkdir -p /data && chown app /data
USER app
VOLUME ["/data"]
EXPOSE 8080

# DIGEST_ADMIN_KEY must be provided by the host (the server refuses to start without it).
CMD ["python", "digest.py", "serve"]
