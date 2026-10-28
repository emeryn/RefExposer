# Base images, npm registry and Alpine mirror: every default is the public one, and each can be pointed at
# a company repository instead (see enterprise/README.md). Nothing else in the build has to change.
ARG NODE_IMAGE=node:22-alpine
# -alpine-slim: nginx without the dynamic modules we do not use (xslt, image-filter, njs) and without
# curl. 21 packages instead of 71, so libxml2, libcurl and brotli - and their advisories - are simply
# not in the image. nginx.conf only uses built-in directives (gzip, proxy_pass, map, try_files).
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.31-alpine-slim

# --- build
FROM ${NODE_IMAGE} AS build
# npm registry, and anything else npm needs to reach it (--ca, --strict-ssl=false...)
ARG NPM_REGISTRY=https://registry.npmjs.org/
ARG NPM_EXTRA_ARGS=
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund --registry "$NPM_REGISTRY" $NPM_EXTRA_ARGS
COPY . .
RUN npm run build

# --- serve: official unprivileged nginx (runs as the nginx account, uid 101; listens on 8080)
FROM ${NGINX_IMAGE}
# Alpine mirror replacing dl-cdn.alpinelinux.org (empty: the public one)
ARG APK_MIRROR=
# 1: apply the security updates of the base image. 0: the base image already carries them (a build with
# nothing to download) — see enterprise/README.md.
ARG OS_UPDATE=1
# Security updates of the base image (the tag is rebuilt less often than the Alpine advisories)
USER root
RUN if [ "$OS_UPDATE" = "1" ]; then \
        if [ -n "$APK_MIRROR" ]; then \
            sed -i "s|https://dl-cdn.alpinelinux.org|$APK_MIRROR|g; s|http://dl-cdn.alpinelinux.org|$APK_MIRROR|g" \
                /etc/apk/repositories; \
        fi; \
        apk --no-cache upgrade; \
    fi
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# The base image already runs as this account; stated here so the image is checked as unprivileged
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
