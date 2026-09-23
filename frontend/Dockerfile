# بناء الواجهة ثم تقديمها مع وكيل /api عبر nginx (المنفذ الوحيد المنشور).
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /src/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/proxy_params.inc /etc/nginx/conf.d/proxy_params.inc
EXPOSE 80 443
