# syntax=docker/dockerfile:1.7
# Caddy with the SPA and the Caddyfile baked in, built from frontend/ (the build context) plus
# the named context `deploy` (deploy/), so Coolify, which copies no files, needs no bind mount.
FROM node:24.22.2-alpine AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml .npmrc ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM caddy:2.11.9-alpine
COPY --from=build /app/build /srv
# `deploy` is a named build context (--build-context / additional_contexts), not a stage.
# hadolint ignore=DL3022
COPY --from=deploy Caddyfile /etc/caddy/Caddyfile
