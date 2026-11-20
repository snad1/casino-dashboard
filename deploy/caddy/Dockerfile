# syntax=docker/dockerfile:1.7
# Caddy with the SPA baked in, built from frontend/ (the build context), so a deploy from a
# fresh clone (Coolify) needs no Node on the host.
FROM node:24.22.2-alpine AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml .npmrc ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM caddy:2.11.9-alpine
COPY --from=build /app/build /srv
