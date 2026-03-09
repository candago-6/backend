FROM node:20-slim

WORKDIR /app

# Instala dependências do Chromium para o Puppeteer (necessário no Debian slim)
RUN apt-get update \
    && apt-get install -y wget gnupg \
    && apt-get install -y libxss1 libappindicator1 libindicator7 \
    libnss3 libatk-bridge2.0-0 libgtk-3-0 libasound2 \
    fonts-liberation libnss3 libx11-xcb1 \
    && rm -rf /var/lib/apt/lists/*

COPY package.json ./
RUN npm install

COPY . .

EXPOSE 8000

CMD ["npm", "start"]
