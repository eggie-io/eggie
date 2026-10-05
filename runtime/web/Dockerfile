# The build stage runs on the builder's own platform: its output is static
# files, identical for every target architecture.
FROM --platform=$BUILDPLATFORM node:24-alpine AS build
WORKDIR /runtime/web
COPY package.json package-lock.json ./
COPY packages/ui/package.json packages/ui/
COPY apps/console/package.json apps/console/
RUN npm ci
COPY . .
# slugify.test.ts shares its cases with the API's tests, from outside
# runtime/web/; build with `--build-context fixtures=tests/fixtures`.
COPY --from=fixtures slugify-cases.json /tests/fixtures/
# The agent manifests live in runtime/agents/, outside this build context;
# build with `--build-context agents=runtime/agents`.
COPY --from=agents . /runtime/agents/
RUN npm run typecheck && npm test && npm run build && npm run check-offline

FROM nginxinc/nginx-unprivileged:stable-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.inc /etc/nginx/security-headers.inc
COPY --from=build /runtime/web/apps/console/dist /usr/share/nginx/html
# Outside Vite's build output: guides are data read at page load, not bundled.
COPY --from=build /runtime/agents /usr/share/nginx/html/agent-guides
EXPOSE 8080
