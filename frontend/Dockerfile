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
# v1.0.0 flipped to fully runtime config — no NEXT_PUBLIC_* ARG at build.
# One image serves any env; RTD_* env vars are read per-request by
# app/layout.tsx and injected into the SSR HTML.
FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
ENV NEXT_OUTPUT=standalone
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
