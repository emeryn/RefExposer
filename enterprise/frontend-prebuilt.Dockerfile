# Serve a frontend that was built elsewhere.
#
# npm has no comfortable offline story, so when the build machine cannot reach any npm registry, build the
# assets where npm works and bring them in. This image is the serving half of frontend/Dockerfile — same
# nginx configuration, same unprivileged account, same health check — with the npm step removed.
#
#   # where npm can reach a registry
#   cd frontend && npm ci && npm run build          # produces frontend/dist/
#
#   # on the build machine, with frontend/dist/ transferred into place
#   docker buildx build -f enterprise/frontend-prebuilt.Dockerfile \
#     --build-context dist=frontend/dist \
#     --build-arg NGINX_IMAGE=registry.example.com/refexposer/nginxinc/nginx-unprivileged:1.31-alpine-slim \
#     -t refexposer-frontend:latest ./frontend
#
# The assets come in as a named build context rather than from ./frontend, so frontend/.dockerignore (which
# excludes dist/ from the normal build, where it is generated) needs no change.
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.31-alpine-slim

FROM ${NGINX_IMAGE}
# Alpine mirror replacing dl-cdn.alpinelinux.org (empty: the public one)
ARG APK_MIRROR=
# 1: apply the security updates of the base image. 0: the base image already carries them
ARG OS_UPDATE=1
USER root
RUN if [ "$OS_UPDATE" = "1" ]; then \
        if [ -n "$APK_MIRROR" ]; then \
            sed -i "s|https://dl-cdn.alpinelinux.org|$APK_MIRROR|g; s|http://dl-cdn.alpinelinux.org|$APK_MIRROR|g" \
                /etc/apk/repositories; \
        fi; \
        apk --no-cache upgrade; \
    fi
# Same configuration as the normal image, generated when the container starts: see frontend/Dockerfile for
# why (the `resolver`, which is what lets the interface survive a backend that comes back on another address)
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY docker-entrypoint.d/ /docker-entrypoint.d/
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    NGINX_ENVSUBST_OUTPUT_DIR=/tmp/nginx-conf \
    NGINX_ENVSUBST_FILTER='^(REFEX_BACKEND|NGINX_LOCAL_RESOLVERS)$' \
    REFEX_BACKEND=backend:8000
RUN chmod +x /docker-entrypoint.d/*.envsh \
    && sed -i 's|include /etc/nginx/conf.d/\*\.conf;|include /tmp/nginx-conf/*.conf;|' /etc/nginx/nginx.conf \
    && grep -q 'include /tmp/nginx-conf/\*\.conf;' /etc/nginx/nginx.conf
# Built by `npm run build` on a machine that has a registry
COPY --from=dist / /usr/share/nginx/html
# The base image already runs as this account; stated here so the image is checked as unprivileged
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
