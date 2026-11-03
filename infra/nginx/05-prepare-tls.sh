#!/bin/sh
set -eu

mkdir -p /etc/nginx/tls

if [ -n "${NGINX_TLS_CERT_PATH:-}" ] && [ -n "${NGINX_TLS_KEY_PATH:-}" ]; then
    cp "${NGINX_TLS_CERT_PATH}" /etc/nginx/tls/tls.crt
    cp "${NGINX_TLS_KEY_PATH}" /etc/nginx/tls/tls.key
else
    openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
        -keyout /etc/nginx/tls/tls.key \
        -out /etc/nginx/tls/tls.crt \
        -subj "/CN=${NGINX_SERVER_NAME:-reportcollector.local}" \
        -addext "subjectAltName=DNS:${NGINX_SERVER_NAME:-reportcollector.local},DNS:localhost,IP:127.0.0.1"
fi
