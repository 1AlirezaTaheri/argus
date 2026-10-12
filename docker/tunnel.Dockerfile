# A shell for the tunnel wrapper, and nothing else.
#
# `cloudflare/cloudflared` is a scratch image: one static binary, no /bin/sh,
# nothing. So a wrapper cannot run in it, and the first attempt at this produced
#
#     OCI runtime create failed: ... exec: "/bin/sh": no such file or directory
#
# which took the tunnel down, and with it the only thing serving the Mini App --
# HTTP 530, origin unreachable. That is the whole reason this file exists rather
# than a three-line wrapper in the compose service.
#
# ## Why the base is caddy:2-alpine
#
# It is the thinnest image on this host that already has both things this needs:
# a shell, and a CA bundle for cloudflared to verify Cloudflare over. `alpine`
# has a shell but no certificates, and installing them with `apk add` fails:
#
#     WARNING: fetching ... alpine/v3.20/main: temporary error
#
# This host has no direct egress to the Alpine mirrors. `node:20-alpine` has the
# certificates but is far larger for one static binary.
#
# ## Why the base is caddy:2-alpine and not `alpine:3.20`
#
# Because only `alpine:latest` is present locally, and naming a tag that is not
# present makes the builder resolve it against the registry before it checks
# what is on disk:
#
#     failed to resolve reference "docker.io/library/alpine:3.20"
#     dial tcp [2606:4700:...]:443: connect: network is unreachable
#
# Every image this file needs is already pulled, so the build is hermetic. That
# is worth having for its own sake: a tunnel image that needs the network to
# build is one more thing that fails when the network is the broken part.
FROM caddy:2-alpine

# Copied from the official image rather than downloaded, so the version is pinned
# by the reference below and a rebuild cannot silently pick up another release.
COPY --from=cloudflare/cloudflared:2024.10.0 /usr/local/bin/cloudflared \
     /usr/local/bin/cloudflared

COPY docker/tunnel-entrypoint.sh /usr/local/bin/tunnel-entrypoint.sh
RUN chmod +x /usr/local/bin/tunnel-entrypoint.sh /usr/local/bin/cloudflared

ENTRYPOINT ["/bin/sh", "/usr/local/bin/tunnel-entrypoint.sh"]
CMD ["tunnel", "--no-autoupdate", "--protocol", "http2", "--url", "http://admin:8080"]