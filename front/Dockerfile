FROM oven/bun:1.4-slim AS base
# Порт фронтенда. Единственная точка в Dockerfile: значение задаёт
# compose.yaml из FRONT_PORT в .env.
ARG PORT=5000
ENV NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=${PORT} \
    # Установка зависимостей не проверяет сертификаты (сервер за корпоративным прокси).
    NODE_TLS_REJECT_UNAUTHORIZED=0
WORKDIR /app

FROM base AS deps
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

FROM node:26-alpine AS prod
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
