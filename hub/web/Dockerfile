FROM node:22-alpine AS builder
WORKDIR /app
COPY hub/web/package.json hub/web/package-lock.json ./
RUN npm ci --ignore-scripts
COPY hub/web/ ./
RUN npm run build

FROM nginx:1.28-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY hub/web/nginx.conf /etc/nginx/conf.d/default.conf
