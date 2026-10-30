# the code sandbox: python (pyodide) + javascript (quickjs) in wasm, one throwaway
# node process per job. runs with NO network, NO secrets and a read-only filesystem
# (see docker-compose.yml). build stage has network to fetch pyodide's packages,
# the final image never needs it.
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY fetch-packages.js ./
RUN node fetch-packages.js

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production HOME=/tmp
COPY --from=build /app/node_modules ./node_modules
COPY package.json server.js child.js ./
# the base image's unprivileged "node" user (uid 1000)
USER node
EXPOSE 8080
HEALTHCHECK --interval=120s --timeout=5s --start-period=60s CMD node -e "fetch('http://127.0.0.1:8080/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
