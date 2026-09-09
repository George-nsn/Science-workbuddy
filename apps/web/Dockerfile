FROM node:24-alpine AS dependencies
WORKDIR /app
COPY package.json package-lock.json* ./
COPY apps/web/package.json ./apps/web/package.json
RUN npm install

FROM node:24-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_URL=http://127.0.0.1:8000/api/v1
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
COPY --from=dependencies /app/node_modules ./node_modules
COPY package.json ./package.json
COPY apps/web ./apps/web
RUN npm run build:web

FROM node:24-alpine AS runtime
ENV NODE_ENV=production
WORKDIR /app
RUN addgroup -S app && adduser -S app -G app
COPY --from=builder --chown=app:app /app/package.json ./package.json
COPY --from=builder --chown=app:app /app/node_modules ./node_modules
COPY --from=builder --chown=app:app /app/apps/web ./apps/web
USER app
EXPOSE 3000
CMD ["npm", "--workspace", "apps/web", "run", "start"]
