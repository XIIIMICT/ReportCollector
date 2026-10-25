FROM oven/bun:1.4-slim AS base
# Port из compose.yaml (FRONT_PORT в .env) — не решается внутри Dockerfile.
ARG PORT=5000
ENV NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=${PORT} \
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
# V8's heap ceiling for the build; CI passes a larger one because Turbopack was
# killed by the runner's OOM killer at the default size.
ARG NODE_OPTIONS
ENV NODE_OPTIONS=${NODE_OPTIONS}
COPY . .
# Persist Next's compiler cache between builds. Without it every CI run
# recompiles from scratch and the Turbopack build peaks at over a gigabyte —
# it was killed with SIGKILL ("cannot allocate memory") on the runner.
RUN --mount=type=cache,target=/app/.next/cache \
    NEXT_TELEMETRY_DISABLED=1 bun run build

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
