const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const QRCode = require('qrcode');
const express = require('express');
const axios = require('axios');

// Iniciando um servidor express simples para healthcheck e exibição do QR Code
const app = express();
const PORT = 8003;
const GATEWAY_URL = process.env.GATEWAY_URL || 'http://service_manager:8002/api/v1/process-message';
const FILTER_KEYWORD = process.env.FILTER_KEYWORD || 'Procon';

// Armazena o último QR Code recebido para servir via HTTP
let latestQR = null;

app.get('/api/v1/health', (req, res) => {
    res.json({ status: 'ok', service: 'whatsapp-bot (node)' });
});

// Serve o QR Code como imagem PNG para escanear pelo browser
app.get('/qr', async (req, res) => {
    if (!latestQR) {
        return res.status(404).send('<h2>QR Code ainda não disponível. Aguarde alguns segundos e recarregue.</h2>');
    }
    try {
        const pngBuffer = await QRCode.toBuffer(latestQR, { scale: 8 });
        res.setHeader('Content-Type', 'image/png');
        res.send(pngBuffer);
    } catch (err) {
        res.status(500).send('Erro ao gerar QR Code');
    }
});

app.listen(PORT, () => {
    console.log(`Serviço do Bot ouvindo na porta ${PORT}`);
    console.log(`QR Code disponível em: http://localhost:${PORT}/qr`);
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
    // Salva o QR Code para servir via HTTP
    latestQR = qr;
    console.log('Novo QR Code gerado. Acesse http://localhost:8003/qr no seu browser para escanear.');

    // Também exibe no terminal como fallback (pode ficar corrompido no Docker, mas está disponível via browser)
    qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
    // Limpa o QR Code após autenticação bem-sucedida
    latestQR = null;
    console.log('Cliente WhatsApp está pronto e conectado!');
});

client.on('message', async (msg) => {
    console.log(`Mensagem recebida de ${msg.from}: ${msg.body}`);

    try {
        // Envia a mensagem para o Gateway, que aplica o filtro e consulta o PLN
        const response = await axios.post(
            `${GATEWAY_URL}?keyword=${encodeURIComponent(FILTER_KEYWORD)}`,
            {
                from_number: msg.from,
                text: msg.body,
            }
        );

        // Gateway retornou 204: mensagem não passou no filtro, ignora
        if (response.status === 204) {
            console.log(`Mensagem de ${msg.from} ignorada (não contém keyword: "${FILTER_KEYWORD}")`);
            return;
        }

        const reply = response.data.class_response;

        // Responde ao usuário no WhatsApp
        await msg.reply(reply);
        console.log(`Resposta enviada para ${msg.from}`);

    } catch (error) {
        console.error('Erro ao processar mensagem no Gateway:', error.message);
    }
});

client.initialize();
