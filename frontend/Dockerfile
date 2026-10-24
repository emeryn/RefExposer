# --- build
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# --- serve: official unprivileged nginx (runs as the nginx account, uid 101; listens on 8080)
FROM nginxinc/nginx-unprivileged:1.31-alpine
# Security updates of the base image (the tag is rebuilt less often than the Alpine advisories)
USER root
RUN apk --no-cache upgrade
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# The base image already runs as this account; stated here so the image is checked as unprivileged
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
