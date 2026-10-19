FROM node:22-bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-noto-core ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10002 --create-home fetcher
ENV PUPPETEER_SKIP_DOWNLOAD=true CHROMIUM_PATH=/usr/bin/chromium HOME=/tmp NODE_ENV=production TZ=Asia/Jerusalem
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY lib.mjs service.mjs hapoalim.mjs ./
USER 10002:10002
CMD ["node", "service.mjs"]
