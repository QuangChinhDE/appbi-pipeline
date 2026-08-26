# ---- deps ----
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

# ---- build ----
FROM node:20-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_BASE_URL=/api
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
# Next inlines NEXT_PUBLIC_* at build time, so this has to be an ARG here and
# not a runtime environment variable. Default false: the demo credential hint
# is opt-in, and a production image must not carry it.
# Next inlines NEXT_PUBLIC_* at build time, so these are ARGs and not runtime
# variables. Empty by default: with nothing supplied, no credential exists
# anywhere in the built bundle -- not rendered, and not present to be read.
ARG NEXT_PUBLIC_DEMO_EMAIL=
ENV NEXT_PUBLIC_DEMO_EMAIL=$NEXT_PUBLIC_DEMO_EMAIL
ARG NEXT_PUBLIC_DEMO_PASSWORD=
ENV NEXT_PUBLIC_DEMO_PASSWORD=$NEXT_PUBLIC_DEMO_PASSWORD
RUN npm run build

# ---- run ----
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
