FROM oven/bun:1.3-slim AS base
ENV NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=5000
WORKDIR /app

FROM base AS deps
# Если SSL_ISSUES_WHEN_INSTALLING_DEPS не пуст — ставим системные сертификаты
# (базовый образ bun slim их не содержит) и включаем NODE_OPTIONS=--use-openssl-ca
# при установке пакетов.
ARG SSL_ISSUES_WHEN_INSTALLING_DEPS=""
RUN if [ -n "$SSL_ISSUES_WHEN_INSTALLING_DEPS" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends ca-certificates \
      && rm -rf /var/lib/apt/lists/* \
      && export NODE_OPTIONS=--use-openssl-ca; \
    fi
COPY package.json bun.lock ./
RUN --mount=type=cache,target=/root/.bun/install/cache \
    bun install --frozen-lockfile

FROM deps AS dev
EXPOSE ${PORT}
COPY . .
CMD ["bun", "dev"]

FROM deps AS build
COPY . .
RUN bun run build

FROM node:25-alpine AS prod
ARG PORT=5000
EXPOSE ${PORT}
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=${PORT}
WORKDIR /app
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
CMD ["node", "server.js"]
