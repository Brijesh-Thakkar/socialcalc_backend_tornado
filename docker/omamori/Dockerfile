# Omamori x402 pre-signature firewall (seetadev/yakusoku, apps/firewall) as an
# internal sidecar. Started only with `docker compose --profile omamori up`.
#
# The source is fetched at build time from a pinned commit (no submodule, no
# vendored copy). Override with --build-arg OMAMORI_REPO / OMAMORI_REF. It is
# GitHub's tarball for that exact commit rather than `ADD <repo>.git#<ref>`,
# because the latter needs BuildKit and the legacy builder silently downloads
# it as a plain file.
FROM oven/bun:1.3
ARG OMAMORI_REPO=https://github.com/seetadev/yakusoku
# upstream main as of 2026-10-01, verified with `git ls-remote`
ARG OMAMORI_REF=d12e66afd47819000abe17f2e49115e872806121
ADD ${OMAMORI_REPO}/archive/${OMAMORI_REF}.tar.gz /tmp/omamori.tar.gz
WORKDIR /app
RUN tar -xzf /tmp/omamori.tar.gz --strip-components=1 -C /app && rm /tmp/omamori.tar.gz \
    && test -f apps/firewall/index.ts
# Only the firewall workspace (and @yakusoku/shared, which it depends on).
# Bun's isolated linker keeps those deps in apps/firewall/node_modules, so the
# demo's mandate helper (apps/agent/scripts/dev-intent.ts, which only imports
# viem and @yakusoku/shared) resolves them through a symlink instead of a
# second, much larger install of the whole agent workspace.
RUN bun install --frozen-lockfile --production --filter @yakusoku/firewall \
    && ln -s ../firewall/node_modules apps/agent/node_modules \
    && mkdir -p /data && chown -R bun:bun /app /data
USER bun
ENV FIREWALL_DATA_DIR=/data PORT=4001
EXPOSE 4001
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
  CMD ["bun", "-e", "fetch('http://localhost:4001/health').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]
CMD ["bun", "run", "apps/firewall/index.ts"]
