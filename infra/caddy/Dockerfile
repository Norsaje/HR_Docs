# Образ Caddy со статикой обоих фронтендов. Контекст сборки — корень репозитория.
# Стадии node собирают web/applicant (с корня сайта) и web/operator (base '/operator/'),
# финальный образ — только Caddy и готовая статика, без node и исходников.
FROM node:22.11.0-alpine AS applicant
WORKDIR /build
COPY web/applicant/package.json web/applicant/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY web/applicant/ ./
RUN npm run build

FROM node:22.11.0-alpine AS operator
WORKDIR /build
COPY web/operator/package.json web/operator/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY web/operator/ ./
RUN npm run build

FROM caddy:2.10.2-alpine
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=applicant /build/dist /srv/applicant
COPY --from=operator /build/dist /srv/operator
