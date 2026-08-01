FROM node:22-alpine AS build
WORKDIR /app
ARG API_URL=http://localhost:8000
ENV API_URL=$API_URL
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app ./
EXPOSE 3000
CMD ["npm", "start"]
