# بناء الواجهة ثم تقديمها مع وكيل /api عبر nginx (المنفذ الوحيد المنشور).
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
RUN apk add --no-cache openssl
COPY --from=build /src/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/proxy_params.inc /etc/nginx/conf.d/proxy_params.inc
COPY deploy/certs.sh /docker-entrypoint.d/05-certs.sh
RUN sed -i 's/\r$//' /docker-entrypoint.d/05-certs.sh && chmod +x /docker-entrypoint.d/05-certs.sh
EXPOSE 80 443
