# ---- deps ----
FROM node:22-alpine AS deps
WORKDIR /web
COPY package.json package-lock.json* ./
RUN npm ci || npm install

# ---- build ----
FROM node:22-alpine AS build
WORKDIR /web
COPY --from=deps /web/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---- run ----
FROM node:22-alpine AS run
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /web/.next/standalone ./
COPY --from=build /web/.next/static ./.next/static
COPY --from=build /web/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
