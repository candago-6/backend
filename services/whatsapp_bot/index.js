const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const express = require('express');
const axios = require('axios');

// Iniciando um servidor express simples apenas para o healthcheck do docker-compose
const app = express();
const PORT = 8003;
const PLN_URL = process.env.PLN_URL || 'http://pln-pipeline:8001/api/fasttext';
const MANAGER_URL = process.env.MANAGER_URL || 'http://service-manager:8002/api/v1';

app.get('/api/v1/health', (req, res) => {
    res.json({ status: 'ok', service: 'whatsapp-bot (node)' });
});

app.listen(PORT, () => {
    console.log(`Serviço de Healthcheck do Bot ouvindo na porta ${PORT}`);
});

// Funções auxiliares para persistência
async function getOrCreateUser(phone) {
    try {
        const response = await axios.get(`${MANAGER_URL}/users/phone/${phone}`);
        return response.data;
    } catch (error) {
        if (error.response && error.response.status === 404) {
            const createResponse = await axios.post(`${MANAGER_URL}/users`, {
                name: "Cliente WhatsApp",
                phone: phone,
                cpf: "" // Ficará vazio por enquanto
            });
            return createResponse.data;
        }
        throw error;
    }
}

async function getOrCreateConversation(userId) {
    try {
        const response = await axios.get(`${MANAGER_URL}/conversations/active/${userId}`);
        return response.data;
    } catch (error) {
        if (error.response && error.response.status === 404) {
            const protocol = `PROCON-${Date.now()}`;
            const createResponse = await axios.post(`${MANAGER_URL}/conversations`, {
                user_id: userId,
                protocol: protocol,
                status: "open"
            });
            return createResponse.data;
        }
        throw error;
    }
}

async function saveMessage(conversationId, role, content) {
    try {
        await axios.post(`${MANAGER_URL}/messages`, {
            conversation_id: conversationId,
            role: role,
            content: content
        });
    } catch (error) {
        console.error('Erro ao salvar mensagem:', error.message);
    }
}

// Iniciando o Client do WhatsApp
const client = new Client({
    authStrategy: new LocalAuth(),
    puppeteer: {
        executablePath: '/usr/bin/chromium',
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    }
});

client.on('qr', (qr) => {
    qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
    console.log('Cliente WhatsApp está pronto e conectado!');
});

client.on('message', async (msg) => {
    // Trava de segurança: só responde se a mensagem contiver "procon" (case insensitive)
    if (!msg.body.toLowerCase().includes('procon')) {
        return;
    }

    console.log(`Mensagem recebida de ${msg.from}: ${msg.body}`);

    try {
        // 1. Persistência: Identificar usuário e conversa
        const phone = msg.from.split('@')[0];
        const user = await getOrCreateUser(phone);
        const conversation = await getOrCreateConversation(user.id);

        // 2. Salvar mensagem do usuário no banco
        await saveMessage(conversation.id, 'user', msg.body);

        // 3. Obter resposta do PLN
        const plnResponse = await axios.post(PLN_URL, {
            raw_text: msg.body
        });

        const reply = plnResponse.data.class_response;
        
        // 4. Salvar resposta do bot no banco
        await saveMessage(conversation.id, 'bot', reply);

        // 5. Responder no WhatsApp
        await msg.reply(reply);
        console.log(`Resposta enviada para ${msg.from}`);

    } catch (error) {
        console.error('Erro no processamento da mensagem:', error.message);
    }
});

client.initialize();
