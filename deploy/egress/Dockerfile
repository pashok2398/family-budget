# The fetcher's only way to the internet: an HTTPS (CONNECT) proxy that allows only the bank domains in
# allowed-domains and denies and logs everything else. The fetcher itself is on an internal network.
FROM alpine:3.20
RUN apk add --no-cache tinyproxy
COPY tinyproxy.conf allowed-domains /etc/tinyproxy/
USER tinyproxy
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
