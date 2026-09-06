# syntax=docker/dockerfile:1.7
#
# Staging/production image for the Next.js operator dashboard (Module 3E). This is a
# separate deployable artifact from the FastAPI research API image at the repository
# root -- API and dashboard ownership stay clearly separated (see
# docs/MODULE_3E_STAGING_DEPLOYMENT_AND_SCHEDULER.md). It never bundles a backend
# credential: the dashboard authenticates to the API the same way any other client
# does, via the environment variables read at request time (see app/session.ts,
# proxy.ts), injected only at container runtime, never baked into this image.

FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS base
# Matches CI's own frontend toolchain invocation exactly (.github/workflows/verify.yml):
# plain `corepack enable`, letting pnpm resolve from the registry rather than pinning
# an exact pnpm version here -- an earlier attempt to pin one (9.15.4) surfaced a real
# incompatibility with this repo's pnpm-workspace.yaml (which has no `packages:` field)
# that CI's own resolution does not hit.
RUN corepack enable

FROM base AS deps
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile --ignore-scripts

FROM base AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm exec next build

FROM base AS runtime
RUN addgroup -g 10001 dashboard && adduser -D -u 10001 -G dashboard dashboard
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=build --chown=10001:10001 /app/.next ./.next
COPY --from=build --chown=10001:10001 /app/node_modules ./node_modules
COPY --from=build --chown=10001:10001 /app/package.json ./package.json

USER 10001:10001
EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.status<500?0:1)).catch(()=>process.exit(1))"]

# Invoke the installed `next` binary directly -- not `pnpm exec next start`, which
# would make corepack lazily fetch the pnpm binary itself from the network on first
# container start (pnpm's own download cache from the build stages is not carried
# into this stage). `next` is already resolved in ./node_modules from the build.
CMD ["./node_modules/.bin/next", "start", "-p", "3000"]
