FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# Dev target: live source via bind mount in compose; runs `next dev` with HMR.
FROM node:22-alpine AS dev
WORKDIR /app
ENV NODE_ENV=development
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

# Prod build: standalone Next.js bundle for Container Apps.
FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
# next.config.mjs reads NEXT_OUTPUT to decide whether to emit a
# standalone bundle (Container App, this Dockerfile) or a static export
# (Static Web Apps, release.yml's publish-viewer-static job).
ENV NEXT_OUTPUT=standalone
ARG NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
# Next.js standalone runs `node server.js` directly — npm is never invoked
# at runtime. Removing the bundled global npm eliminates a CVE surface
# (CVE-2026-33671 in npm's bundled picomatch, etc.) and shrinks the image.
RUN rm -rf /usr/local/lib/node_modules /usr/local/bin/npm /usr/local/bin/npx
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
