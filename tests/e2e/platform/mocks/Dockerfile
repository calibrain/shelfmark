# Mock services image for the Shelfmark e2e platform.
# One image, many roles (selected via MOCK_ROLE): origin-aa, cloudflare,
# flaresolverr, prowlarr, doh. See mock_services.py for the role contracts.
FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mock_services.py .
# Needed by the origin-aa role to build the `full` profile's webseed .torrent.
COPY make_webseed_torrent.py .
COPY fixtures ./fixtures

ENV PORT=80
ENV MOCK_ROLE=all

# gunicorn keeps the mock responsive under concurrent e2e load.
RUN pip install --no-cache-dir gunicorn
EXPOSE 80
EXPOSE 443
# DOH_TLS=1 (the `doh` role) self-signs a cert and serves HTTPS on 443; every
# other role serves plain HTTP on $PORT via gunicorn.
CMD ["sh", "-c", "if [ \"$DOH_TLS\" = 1 ]; then python mock_services.py; else gunicorn -w 2 -b 0.0.0.0:${PORT} mock_services:app; fi"]
