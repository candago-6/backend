const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const express = require('express');
const axios = require('axios');

// Iniciando um servidor express simples apenas para o healthcheck do docker-compose
const app = express();
const PORT = 8003;
const PLN_URL = process.env.PLN_URL || 'http://pln-pipeline:8001/api/fasttext';

app.get('/api/v1/health', (req, res) => {
    res.json({ status: 'ok', service: 'whatsapp-bot (node)' });
});

app.listen(PORT, () => {
    console.log(`Serviço de Healthcheck do Bot ouvindo na porta ${PORT}`);
});

// Iniciando o Client do WhatsApp
const client = new Client({
    authStrategy: new LocalAuth(),
    puppeteer: {
        executablePath: '/usr/bin/chromium',
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
    console.log(`Mensagem recebida de ${msg.from}: ${msg.body}`);

    try {
        // Envia a mensagem para o pipeline de PLN
        const response = await axios.post(PLN_URL, {
            raw_text: msg.body
        });

        const reply = response.data.class_response;
        
        // Responde ao usuário no WhatsApp
        await msg.reply(reply);
        console.log(`Resposta enviada para ${msg.from}`);

    } catch (error) {
        console.error('Erro ao processar mensagem no pipeline:', error.message);
        // Opcional: Enviar uma mensagem de erro genérica ou não responder
        // await msg.reply('Desculpe, estou com dificuldades técnicas agora. Tente novamente mais tarde.');
    }
});

client.initialize();

