FROM python:3.11-slim

# Node.js + opencode CLI (used by server.py to run search_sharepoint queries)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g opencode-ai \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data/local_files

# Persistent volume mount point (attach a Railway volume at /data)
ENV MIRROR_DIR=/data/local_files
ENV ADMIN_DB_PATH=/data/admin.db

EXPOSE 8001

CMD ["python", "server.py"]
