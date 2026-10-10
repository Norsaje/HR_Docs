# Образ Caddy со статикой фронтендов. В фазе 0 — заглушки; в фазе 1 здесь появится
# стадия node, которая собирает web/applicant и web/operator.
FROM caddy:2.10.2-alpine
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY infra/caddy/placeholder/applicant /srv/applicant
COPY infra/caddy/placeholder/operator /srv/operator
