# the browser: headless chromium (playwright) behind a small http api, plus an egress filter
# that only lets it reach public addresses. runs non-root, read-only fs, no caps, no secrets,
# on its own network that can't reach noodle (see docker-compose.yml). the playwright base
# image already has chromium and its system libraries; keep its tag in step with the
# playwright-core version in package.json.
FROM mcr.microsoft.com/playwright:v1.56.1-noble AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM mcr.microsoft.com/playwright:v1.56.1-noble
WORKDIR /app
ENV NODE_ENV=production HOME=/tmp
COPY --from=build /app/node_modules ./node_modules
COPY package.json server.js session.js proxy.js ./
# the base image's unprivileged user (uid 1000)
USER pwuser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD node -e "fetch('http://127.0.0.1:8080/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
