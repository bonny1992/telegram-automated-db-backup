FROM python:3.12-alpine

# Install build tools + mariadb client libs + 7zip + curl (for supercronic)
RUN apk add --no-cache \
    gcc \
    musl-dev \
    mariadb-connector-c-dev \
    mariadb-client \
    p7zip \
    libffi-dev \
    bash \
    curl

# Latest releases available at https://github.com/aptible/supercronic/releases
ENV SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.36/supercronic-linux-amd64 \
    SUPERCRONIC_SHA1SUM=53a484404b0c559d64f78e9481a3ec22f782dc46 \
    SUPERCRONIC=supercronic-linux-amd64

RUN curl -fsSLO "$SUPERCRONIC_URL" \
 && echo "${SUPERCRONIC_SHA1SUM}  ${SUPERCRONIC}" | sha1sum -c - \
 && chmod +x "$SUPERCRONIC" \
 && mv "$SUPERCRONIC" "/usr/local/bin/${SUPERCRONIC}" \
 && ln -s "/usr/local/bin/${SUPERCRONIC}" /usr/local/bin/supercronic

# Create workdir
WORKDIR /app

# Copy requirements inline (so Docker caches dependencies unless requirements change)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy script
COPY backup.py .

# Create session dir
RUN mkdir -p /sessions /logs

# Default environment
ENV ZIP_BINARY=/usr/bin/7z
ENV SESSION_DIR=/sessions
ENV LOG_FILE=/logs/backup.log
ENV CRON_SCHEDULE="0 4 * * *"

VOLUME /sessions
VOLUME /logs

# Write the cron job dynamically at container start
ENTRYPOINT ["/bin/sh", "-c", "echo \"$CRON_SCHEDULE python /app/backup.py\" > /app/cronjob && exec $SUPERCRONIC /app/cronjob"]
