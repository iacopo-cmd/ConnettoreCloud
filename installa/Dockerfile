# Connettore cloud – immagine del server MCP (sola lettura)
FROM python:3.13-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends systemd \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "mcp==1.30.0" "psutil==7.2.2" "uvicorn==0.38.0" \
 && groupadd -g 10110 connettore && useradd -u 10110 -g 10110 -M -s /usr/sbin/nologin connettore
WORKDIR /srv
COPY app /srv/app
USER 10110:10110
CMD ["python", "-m", "app.server"]
