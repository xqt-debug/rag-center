ARG CADDY_VERSION=2.10.2
FROM caddy:${CADDY_VERSION}-builder AS builder
ARG CADDY_VERSION
RUN xcaddy build v${CADDY_VERSION} \
    --with github.com/caddy-dns/alidns@v1.0.29

FROM caddy:${CADDY_VERSION}-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
