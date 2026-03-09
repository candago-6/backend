const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const express = require('express');

// Iniciando um servidor express simples apenas para o healthcheck do docker-compose
const app = express();
app.get('/api/v1/health', (req, res) => {
    res.json({ status: 'ok', service: 'whatsapp-bot (node)' });
});
app.listen(8000, () => {
    console.log('Serviço de Healthcheck do Bot ouvindo na porta 8000');
});

// Iniciando o Client do WhatsApp
const client = new Client({
    authStrategy: new LocalAuth(),
    puppeteer: {
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    }
});

client.on('qr', (qr) => {
    // Exibe o QR Code no terminal log para escaneamento
    qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
    console.log('Cliente WhatsApp está pronto e conectado!');
});

client.on('message', async (msg) => {
    // Inicia o fluxo de processamento
    console.log(`Mensagem recebida de ${msg.from}: ${msg.body}`);
    console.log('Iniciando fluxo de processamento em background...');
});

client.initialize();
