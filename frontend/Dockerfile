# syntax=docker/dockerfile:1

# ---- Stage 1: build the SPA ----------------------------------------------
# Resilient to the in-flight CRA→Vite migration: if a vite.config.* exists run
# the Vite build (npx vite build), otherwise the CRA build (npm run build).
# Either output directory is normalized to /app/build for stage 2.
FROM node:20-alpine AS builder

WORKDIR /app

ENV REACT_APP_BACKEND_URL="" \
    VITE_API_BASE_URL="" \
    GENERATE_SOURCEMAP=false \
    NODE_OPTIONS="--max-old-space-size=2048"

COPY package.json ./
# Lockfile optional while the CRA→Vite migration lands; frozen install when present
COPY package-lock.json* yarn.lock* ./

RUN set -eux; \
    if [ -f package-lock.json ]; then \
      npm ci; \
    elif [ -f yarn.lock ]; then \
      corepack enable && yarn install --frozen-lockfile; \
    else \
      npm install --no-audit --no-fund; \
    fi

COPY . .

RUN set -eux; \
    if ls vite.config.* >/dev/null 2>&1; then \
      npx vite build; \
    else \
      npm run build; \
    fi; \
    if [ -d dist ] && [ ! -d build ]; then mv dist build; fi

# ---- Stage 2: nginx serving the SPA + proxying api/SSE -------------------
FROM nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/build /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost:80/ || exit 1
