# MOCK-BACKED toju server. NOT the real toju.network backend.
FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --omit=dev
COPY index.js ./
EXPOSE 5057
CMD ["node", "index.js"]
