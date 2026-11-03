# nginx with brotli: same base as the central DWH stack
# (fholzer/nginx-brotli — Alpine + nginx with brotli filter/static modules;
# brotli is already switched on in the image's nginx.conf). SAME FILES are
# used by the ReportCollector standalone stack at
# ReportCollector/infra/nginx — keep both trees identical;
# infra/check-drift.py fails on drift.
#
# fholzer/nginx-brotli ships no /docker-entrypoint.sh: its ENTRYPOINT is
# plain `nginx`, so the official template envsubst and docker-entrypoint.d
# hooks do not run on their own. This image therefore installs its own
# entrypoint that renders /etc/nginx/templates (NGINX_ENVSUBST_FILTER, as in
# the official image) and prepares TLS certificates before exec-ing `nginx`.
FROM fholzer/nginx-brotli:v1.31

RUN apk add --no-cache openssl gettext-envsubst

COPY 05-prepare-tls.sh /app/05-prepare-tls.sh
COPY entrypoint.sh /app/entrypoint.sh
COPY default.conf.template /etc/nginx/templates/default.conf.template
RUN chmod +x /app/05-prepare-tls.sh /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
