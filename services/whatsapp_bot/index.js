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

    if (msg.body === '!ping') {
        await msg.reply('pong');
    } else {
        try {
            // Função de resposta via WhatsApp mantida conforme solicitado
            await msg.reply('Olá! Sua mensagem foi recebida e o fluxo de processamento foi iniciado.');

            // Orquestração: Envia para o Service Manager
            const response = await fetch('http://service_manager:8002/api/v1/process-message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    from_number: msg.from,
                    text: msg.body
                })
            });

            if (!response.ok) {
                console.error(`Erro na orquestração pelo Gateway: ${response.status} - ${await response.text()}`);
            } else {
                const data = await response.json();
                console.log('Orquestração e processamento PLN concluídos com sucesso:', JSON.stringify(data, null, 2));
            }
        } catch (error) {
            console.error('Erro ao enviar mensagem ou orquestrar:', error);
        }
    }
});

client.initialize();
