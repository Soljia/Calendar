# Override to build from a registry mirror, e.g.
#   docker compose build --build-arg BASE_REGISTRY=mirror.gcr.io
ARG BASE_REGISTRY=docker.io
FROM ${BASE_REGISTRY}/library/node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY tsconfig*.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

# ---------------------------------------------------------------------------
FROM ${BASE_REGISTRY}/library/nginx:1.27-alpine AS runtime

# nginx earns its place here: it serves the built SPA and reverse-proxies /api
# and /ws to the backend, which puts the UI and the API on ONE origin. That is
# what lets the HttpOnly session cookie authenticate the WebSocket upgrade --
# browsers cannot attach custom headers to one -- with no CORS configuration
# and no token ever exposed to page scripts.
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
