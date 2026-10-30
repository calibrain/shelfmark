ARG TARGETPLATFORM
ARG TARGETARCH
ARG BUILDPLATFORM
ARG BUILDARCH

# Every stage builds on Chainguard's Wolfi base. Free-tier Chainguard images
# only publish `latest` (the `node` and `python` images track Current/3.15), so
# runtime versions are pinned through Wolfi's versioned packages instead of tags.

# Frontend build stage.
FROM --platform=$BUILDPLATFORM cgr.dev/chainguard/wolfi-base:latest@sha256:88a20e2d017bd20cdc0cb3910de603e7bea9512a6c351a8dd3c9df212ab03d73 AS frontend-builder

RUN apk add --no-cache nodejs-24 npm

# Helpful debug output to see what platforms BuildKit thinks it's using
RUN echo "BUILDPLATFORM=$BUILDPLATFORM BUILDARCH=$BUILDARCH TARGETPLATFORM=$TARGETPLATFORM TARGETARCH=$TARGETARCH"

WORKDIR /frontend

# Copy frontend package files
COPY src/frontend/package*.json ./

# Install dependencies (cache mount for faster rebuilds)
RUN --mount=type=cache,target=/root/.npm \
    npm ci

# Copy frontend source
COPY src/frontend/ ./

# Build the frontend
RUN npm run build

# uv is a build-time tool only, so it is mounted into the RUNs that need it rather
# than copied into the image. A COPY here would land ~24 MB in a `base` layer that
# every published image inherits, and a later `rm` cannot take it back out again --
# a RUN adds a layer, it does not rewrite the one underneath.
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

# --- Tor (PINNED to 0.4.9.15) ---
# Wolfi has no tor package, so it is built from the upstream release. Bump
# TOR_VERSION and TOR_SHA256 together; the hash is the one published next to the
# tarball on dist.torproject.org. Only the binary reaches the final image.
FROM cgr.dev/chainguard/wolfi-base:latest@sha256:88a20e2d017bd20cdc0cb3910de603e7bea9512a6c351a8dd3c9df212ab03d73 AS tor-builder

ARG TOR_VERSION=0.4.9.15
ARG TOR_SHA256=1911f5b216a6814ed143a08bbcff1c6dc96d2ca3b505df9a21876080a92963f3

RUN apk add --no-cache build-base curl openssl-dev libevent-dev zlib-dev && \
    curl -fsSLo /tmp/tor.tar.gz "https://dist.torproject.org/tor-${TOR_VERSION}.tar.gz" && \
    echo "${TOR_SHA256}  /tmp/tor.tar.gz" | sha256sum -c - && \
    tar -xzf /tmp/tor.tar.gz -C /tmp && \
    cd "/tmp/tor-${TOR_VERSION}" && \
    ./configure --prefix=/usr --sysconfdir=/etc --localstatedir=/var \
        --disable-asciidoc --disable-manpage --disable-html-manual \
        --disable-lzma --disable-zstd --disable-unittests && \
    make -j"$(nproc)" && \
    install -Dm755 src/app/tor /out/usr/bin/tor

# --- Chrome for Testing (PINNED to 156.0.8078.47) ---
# Wolfi's chromium package stopped receiving updates in June 2026, so the browser
# is Google's Chrome for Testing build of the same Chromium release. Bump the
# version and both per-arch hashes together (Google publishes no checksums; take
# them from the downloaded zips).
FROM --platform=$BUILDPLATFORM cgr.dev/chainguard/wolfi-base:latest@sha256:88a20e2d017bd20cdc0cb3910de603e7bea9512a6c351a8dd3c9df212ab03d73 AS chrome-download

ARG TARGETARCH
ARG CHROME_VERSION=156.0.8078.47
ARG CHROME_SHA256_AMD64=e6c94490f0b6b337225aeb2d2a5034858516b7017356980ba94f5176d8774a50
ARG CHROME_SHA256_ARM64=9e37000e3ee2731e45e475a6a2eac587d3c0ee5f8aef7cab5211ecd52cf3fb7b

RUN apk add --no-cache curl && \
    case "${TARGETARCH}" in \
        amd64) platform=linux64; sha="${CHROME_SHA256_AMD64}" ;; \
        arm64) platform=linux-arm64; sha="${CHROME_SHA256_ARM64}" ;; \
        *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac && \
    curl -fsSLo /tmp/chrome.zip \
        "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/${platform}/chrome-${platform}.zip" && \
    echo "${sha}  /tmp/chrome.zip" | sha256sum -c - && \
    mkdir -p /out/opt && \
    unzip -q /tmp/chrome.zip -d /tmp/chrome && \
    mv "/tmp/chrome/chrome-${platform}" /out/opt/chrome-for-testing

FROM cgr.dev/chainguard/wolfi-base:latest@sha256:88a20e2d017bd20cdc0cb3910de603e7bea9512a6c351a8dd3c9df212ab03d73 AS base

# wolfi-base ships only busybox sh; bash must exist before SHELL can use it.
RUN apk add --no-cache bash

# Set shell to bash with pipefail option
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Consistent environment variables grouped together
ENV DOCKERMODE=true \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=UTF-8 \
    NAME=Shelfmark \
    PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app \
    # PUID/PGID will be handled by entrypoint script, but TZ/Locale are still needed
    LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8

# Set ARG for build-time expansion (FLASK_PORT), ENV for runtime access
ENV FLASK_PORT=8084

# Configure locale, timezone, and perform initial cleanup in a single layer
RUN apk add --no-cache \
    # Runtime interpreter. The minor is fixed by the package name; moving to a
    # new minor is a deliberate change (see the python notes in dependabot.yml).
    python-3.14 \
    # No C toolchain: every compiled dependency (cffi, gevent, greenlet,
    # zope-interface) ships a cp314 manylinux wheel for amd64 and arm64, and
    # the sdist-only ones are pure Python. If a dependency ever needs
    # compiling, build it in a separate stage.
    # For locale (en_US.UTF-8 ships precompiled; no locale-gen step)
    glibc-locale-en tzdata \
    # For healthcheck
    curl \
    # For entrypoint (groupadd/useradd, getent)
    dumb-init shadow posix-libc-utils \
    # GNU grep: tor.sh parses the Tor check with `grep -oP`, which BusyBox lacks
    grep \
    # For debug
    zip iputils \
    # For user switching
    gosu \
    # --- Tor support (activated via USING_TOR=true) ---
    # tor itself is built from source (see tor-builder); these are its libraries.
    so:libevent-2.1.so.7 so:libssl.so.3 so:libcrypto.so.3 \
    # Installing the legacy backend makes `iptables`/`ip6tables` resolve to it,
    # which tor.sh expects.
    iptables iptables-legacy \
    # --- WireGuard support (activated via USING_WIREGUARD=true) ---
    wireguard-tools \
    iproute2 \
    procps \
    ca-certificates && \
    # Default to UTC timezone but will be overridden by the entrypoint script
    ln -snf /usr/share/zoneinfo/UTC /etc/localtime && echo UTC > /etc/timezone && \
    echo "LC_ALL=en_US.UTF-8" >> /etc/environment && \
    echo "LANG=en_US.UTF-8" > /etc/locale.conf

# Tor: the source-built binary plus the account and paths tor.sh expects
# (Debian's tor package used to create them).
COPY --from=tor-builder /out/usr/bin/tor /usr/bin/tor
RUN groupadd -r debian-tor && \
    useradd -r -g debian-tor -d /var/lib/tor -s /sbin/nologin debian-tor && \
    install -d -m 0755 /etc/tor && \
    install -d -m 0700 -o debian-tor -g debian-tor /var/lib/tor && \
    install -d -m 2750 -o debian-tor -g adm /var/log/tor && \
    tor --version

# supervisor (tor.sh's process manager) comes from PyPI: Wolfi's package runs on
# python3.13. It gets its own hash-pinned venv on the image's interpreter and is
# linked to the paths tor.sh expects.
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    printf 'supervisor==4.3.0 --hash=sha256:0bcb763fddafba410f35cbde226aa7f8514b9fb82eb05a0c85f6588d1c13f8db\n' \
        > /tmp/supervisor-requirements.txt && \
    uv venv --python /usr/bin/python3.14 /opt/supervisor && \
    uv pip install --python /opt/supervisor/bin/python --no-cache --require-hashes \
        -r /tmp/supervisor-requirements.txt && \
    rm /tmp/supervisor-requirements.txt && \
    ln -s /opt/supervisor/bin/supervisord /usr/bin/supervisord && \
    ln -s /opt/supervisor/bin/supervisorctl /usr/bin/supervisorctl && \
    mkdir -p /etc/supervisor

# Create a fixed runtime user/group so hardened Docker/Kubernetes deployments
# can start the container directly as a non-root user with a passwd entry.
RUN groupadd -g 1000 shelfmark && \
    useradd -u 1000 -g shelfmark -d /home/shelfmark -s /usr/sbin/nologin shelfmark && \
    mkdir -p /home/shelfmark && \
    chown 1000:1000 /home/shelfmark

# Set working directory
WORKDIR /app

# Install core Python dependencies first for better layer caching
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-default-groups

# The application code is deliberately NOT copied here. `base` is shared by the
# final stages, so a COPY of the source at this point invalidates every layer
# built on top of it -- the Chromium install, the browser dependency sync and
# the SeleniumBase driver download -- on any source change. Each stage copies
# the source as its last step instead, so a code-only rebuild rewrites one small
# layer and every expensive layer is reused. `[tool.uv] package = false` is what
# makes this safe: no `uv sync` needs the project source.

# Expose the application port
EXPOSE ${FLASK_PORT}

# Add healthcheck for container status
# Uses /api/health which doesn't require authentication.
# curl needs -f so an HTTP error status fails the probe instead of passing it:
# plain `curl -s` exits 0 on a 500, which reported a broken app as healthy.
# timeout stays well under interval so a hung probe cannot occupy a whole cycle.
# --start-interval matches the daemon default (5s), made explicit so startup
# probing does not depend on that default staying put.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --start-interval=5s --retries=3 \
    CMD curl -fsS http://localhost:${FLASK_PORT}/api/health > /dev/null || exit 1

# Use dumb-init as the entrypoint to handle signals properly
ENTRYPOINT ["/usr/bin/dumb-init", "--"]


FROM base AS shelfmark

# --- Chromium: Chrome for Testing, PINNED in the chrome-download stage ---
COPY --from=chrome-download /out/opt/chrome-for-testing /opt/chrome-for-testing

RUN apk add --no-cache \
    # For dumb display. Xvfb exits at startup without xkbcomp, which Wolfi's
    # xvfb package does not pull in (Debian's did, via x11-xkb-utils).
    xvfb xkbcomp \
    # For screen recording
    ffmpeg \
    # Chrome for Testing's runtime libraries (its deb.deps, mapped to Wolfi)
    glib libnss libnspr libatk-1.0 libatk-bridge-2.0 cups-libs libxkbcommon alsa-lib mesa-gbm \
    cairo pango eudev-libs libxcomposite libxdamage libxfixes libxrandr \
    font-liberation \
    # For tkinter (pyautogui)
    python-3.14-tk \
    # For RAR extraction (rarfile uses 7z; it also reads RAR5, unlike unrar-free)
    7zip && \
    # Keep the Debian-era path that SeleniumBase and the rest of the image use
    ln -s /opt/chrome-for-testing/chrome /usr/bin/chromium

# Install the browser automation stack used by the full image
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-default-groups --extra browser

# Deterministically resolve the Xlib namespace collision.
# pyautogui/mouseinfo pull the stale `python3-xlib` (0.15, 2014), while the
# `--extra browser` set pulls `python-xlib` (0.33). Both packages install into
# the same top-level `Xlib/` namespace, so whichever lands last wins. When the
# 2014 build wins, `Xlib.X` is missing `FamilyServerInterpreted`, which the
# SeleniumBase Pure-CDP driver requires at browser startup -> every bypass fails
# with "module 'Xlib.X' has no attribute 'FamilyServerInterpreted'" and no
# Cloudflare/DDoS-Guard protected download can complete. Drop the stale package
# and force python-xlib 0.33 to own the namespace. pyautogui runs fine against
# 0.33 (superset API).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv pip uninstall --python /app/.venv/bin/python python3-xlib && \
    uv pip install --python /app/.venv/bin/python --reinstall python-xlib==0.33 && \
    /app/.venv/bin/python -c "import Xlib.X; assert hasattr(Xlib.X, 'FamilyServerInterpreted'), 'Xlib.X.FamilyServerInterpreted missing after fix'; print('Xlib namespace OK:', Xlib.__version__)"

# The venv's own pip goes too. uv seeded a copy into /app/.venv and nothing
# installs at runtime. setuptools deliberately STAYS: several deps still import
# pkg_resources on the hot path.
RUN rm -rf /app/.venv/bin/pip /app/.venv/bin/pip3 /app/.venv/bin/pip3.* \
           /app/.venv/lib/python*/site-packages/pip \
           /app/.venv/lib/python*/site-packages/pip-*.dist-info

# Keep SeleniumBase's bundled driver cache writable for the fixed non-root user.
RUN SELENIUMBASE_DRIVERS_DIR=$(/app/.venv/bin/python -c "import pathlib, seleniumbase; print(pathlib.Path(seleniumbase.__file__).resolve().parent / 'drivers')") && \
    chown -R 1000:1000 "${SELENIUMBASE_DRIVERS_DIR}" && \
    chmod -R u+rwX,go+rX "${SELENIUMBASE_DRIVERS_DIR}" && \
    if [ -f "${SELENIUMBASE_DRIVERS_DIR}/uc_driver" ]; then chmod +x "${SELENIUMBASE_DRIVERS_DIR}/uc_driver"; fi

# Grant read/execute permissions to others
RUN chmod -R o+rX /opt/chrome-for-testing

# --- Application code: last, so every expensive layer above stays cached ---

COPY . .

COPY --from=frontend-builder /frontend/dist /app/frontend-dist

# The frontend SOURCE cannot be excluded via .dockerignore — that file applies
# to every stage and frontend-builder needs src/. Only the built dist (copied
# above) is served at runtime, so drop the source here.
RUN rm -rf /app/src

# Image-owned runtime paths for the fixed non-root user. Root/PUID mode still
# re-homes ownership at startup when needed.
RUN mkdir -p \
        /config \
        /books \
        /var/log/shelfmark \
        /tmp/shelfmark/seleniumbase/downloaded_files \
        /tmp/shelfmark/seleniumbase/archived_files && \
    rm -rf /app/downloaded_files /app/archived_files && \
    ln -s /tmp/shelfmark/seleniumbase/downloaded_files /app/downloaded_files && \
    ln -s /tmp/shelfmark/seleniumbase/archived_files /app/archived_files && \
    chown -R 1000:1000 /config /books /home/shelfmark /tmp/shelfmark /var/log/shelfmark && \
    chmod -R a+rX /app && \
    chmod +x /app/entrypoint.sh /app/tor.sh /app/wireguard.sh /app/genDebug.sh

# Default command to run the application entrypoint script

# Version stamp last. These carry the commit sha, so they change on every
# build and everything below them rebuilds. Kept here, the dependency and
# browser layers above stay valid and a pull only fetches what changed.
ARG BUILD_VERSION
ENV BUILD_VERSION=${BUILD_VERSION}
ARG RELEASE_VERSION
ENV RELEASE_VERSION=${RELEASE_VERSION}

CMD ["/app/entrypoint.sh"]


FROM base AS shelfmark-lite

ENV USING_EXTERNAL_BYPASSER=true

# --- Application code: last, so every expensive layer above stays cached ---

COPY . .

COPY --from=frontend-builder /frontend/dist /app/frontend-dist

# The frontend SOURCE cannot be excluded via .dockerignore — that file applies
# to every stage and frontend-builder needs src/. Only the built dist (copied
# above) is served at runtime, so drop the source here.
RUN rm -rf /app/src

# Image-owned runtime paths for the fixed non-root user. Root/PUID mode still
# re-homes ownership at startup when needed.
RUN mkdir -p \
        /config \
        /books \
        /var/log/shelfmark \
        /tmp/shelfmark/seleniumbase/downloaded_files \
        /tmp/shelfmark/seleniumbase/archived_files && \
    rm -rf /app/downloaded_files /app/archived_files && \
    ln -s /tmp/shelfmark/seleniumbase/downloaded_files /app/downloaded_files && \
    ln -s /tmp/shelfmark/seleniumbase/archived_files /app/archived_files && \
    chown -R 1000:1000 /config /books /home/shelfmark /tmp/shelfmark /var/log/shelfmark && \
    chmod -R a+rX /app && \
    chmod +x /app/entrypoint.sh /app/tor.sh /app/wireguard.sh /app/genDebug.sh


# Version stamp last. These carry the commit sha, so they change on every
# build and everything below them rebuilds. Kept here, the dependency and
# browser layers above stay valid and a pull only fetches what changed.
ARG BUILD_VERSION
ENV BUILD_VERSION=${BUILD_VERSION}
ARG RELEASE_VERSION
ENV RELEASE_VERSION=${RELEASE_VERSION}

CMD ["/app/entrypoint.sh"]
