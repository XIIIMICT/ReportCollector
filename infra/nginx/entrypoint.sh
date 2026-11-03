#!/bin/sh
# Own entrypoint for the brotli nginx image (fholzer/nginx-brotli builds a
# bare Alpine + nginx: it carries NO /docker-entrypoint.sh, so neither the
# official template envsubst nor /docker-entrypoint.d scripts run on their
# own). This script restores that behaviour narrowly:
#   1. render /etc/nginx/templates/*.template -> /etc/nginx/conf.d/",
#      substituting ONLY the env variables matching NGINX_ENVSUBST_FILTER
#      (same semantics as the official nginx image); an empty/garbage filter
#      substitutes nothing;
#   2. run 05-prepare-tls.sh (self-signed fallback or host certificates);
#   3. exec the container command (nginx).
set -eu

render_templates() {
    filter="${NGINX_ENVSUBST_FILTER:-}"
    # NB: the substitution is intentionally UNquoted — names arrive as
    # newline-separated words and must word-split into separate printf args.
    defined_envs="$(printf '${%s} ' $(env | cut -d= -f1 | grep -E -- "$filter" || true))"
    # Without a strict filter envsubst would rewrite nginx's own $variables
    # ($host, $http_upgrade...) in the template — refuse to do that.
    if [ -z "$defined_envs" ]; then
        echo "no NGINX_... template variables matched" >&2
        return 1
    fi
    for template in /etc/nginx/templates/*.template; do
        [ -e "$template" ] || continue
        target="/etc/nginx/conf.d/$(basename "$template" .template)"
        envsubst "$defined_envs" < "$template" > "$target"
        chmod 0644 "$target"
        echo "rendered $template -> $target"
    done
}

render_templates
/app/05-prepare-tls.sh

exec "$@"
