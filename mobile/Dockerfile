FROM node:24-slim

WORKDIR /app

# Manifests first so the install layer survives source edits.
COPY package.json package-lock.json* ./
RUN npm ci || npm install

COPY . .

EXPOSE 3002
CMD ["npm", "run", "dev"]
