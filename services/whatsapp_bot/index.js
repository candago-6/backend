const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const QRCode = require('qrcode');
const express = require('express');
const axios = require('axios');

// Iniciando um servidor express simples para healthcheck e exibição do QR Code
const app = express();
const PORT = 8003;
const PLN_URL = process.env.PLN_URL || 'http://pln-pipeline:8001/api/fasttext/knn';
const MANAGER_URL = process.env.MANAGER_URL || 'http://service-manager:8002/api/v1';

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

// Variável para armazenar o ID da conversa ativa (opcional, já que buscamos do banco)
let currentConversationId = null;

async function saveMessage(conversationId, role, content) {
    try {
        const response = await axios.post(`${MANAGER_URL}/messages`, {
            conversation_id: conversationId,
            role: role,
            content: content
        });
        return response.data.id;
    } catch (error) {
        console.error('Erro ao salvar mensagem:', error.message);
        return null;
    }
}

async function saveFeedback(conversationId, rating) {
    try {
        await axios.post(`${MANAGER_URL}/feedback`, {
            conversation_id: conversationId,
            rating: rating,
            is_best_answer: rating >= 4
        });
        return true;
    } catch (error) {
        console.error('Erro ao salvar feedback:', error.message);
        return false;
    }
}

// Coleta de dados reais do cliente (nome/CPF), substituindo o placeholder inicial
const onboardingState = new Map(); // phone -> { step: 'name' | 'cpf', name?: string }

function isValidCpf(value) {
    return value.replace(/\D/g, '').length === 11;
}

async function updateUserData(userId, data) {
    try {
        await axios.put(`${MANAGER_URL}/users/${userId}`, data);
        return true;
    } catch (error) {
        console.error('Erro ao atualizar dados do cliente:', error.message);
        return false;
    }
}

// ... (Client initialization)
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
    qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
    // Limpa o QR Code após autenticação bem-sucedida
    latestQR = null;
    console.log('Cliente WhatsApp está pronto e conectado!');
});

client.on('message', async (msg) => {
    // 1. Lógica de Feedback (agora por conversa)
    if (msg.body.toLowerCase().startsWith('feedback ')) {
        const rating = parseInt(msg.body.split(' ')[1]);
        
        // Obter usuário e conversa para ter o ID da conversa atual
        const phone = msg.from.split('@')[0];
        const user = await getOrCreateUser(phone);
        const conversation = await getOrCreateConversation(user.id);
        
        if (rating >= 1 && rating <= 5) {
            const success = await saveFeedback(conversation.id, rating);
            if (success) {
                await msg.reply('Obrigado pelo seu feedback sobre este atendimento!');
            } else {
                await msg.reply('Erro ao salvar feedback.');
            }
        } else {
            await msg.reply('Por favor, envie "feedback" seguido de uma nota de 1 a 5.');
        }
        return;
    }

    // 2. Continuação da coleta de dados (nome/CPF), se já iniciada para este número
    const onboardingPhone = msg.from.split('@')[0];
    const onboarding = onboardingState.get(onboardingPhone);

    if (onboarding) {
        if (onboarding.step === 'name') {
            onboarding.name = msg.body.trim();
            onboarding.step = 'cpf';
            await msg.reply('Obrigado! Agora informe seu CPF (apenas números).');
            return;
        }

        if (onboarding.step === 'cpf') {
            if (!isValidCpf(msg.body)) {
                await msg.reply('CPF inválido. Por favor, envie os 11 números do seu CPF.');
                return;
            }

            const user = await getOrCreateUser(onboardingPhone);
            await updateUserData(user.id, { name: onboarding.name, cpf: msg.body.replace(/\D/g, '') });
            onboardingState.delete(onboardingPhone);

            await msg.reply('Dados confirmados! Agora me conte como posso te ajudar (mencione "procon" na sua mensagem).');
            return;
        }
    }

    // Trava de segurança: só responde se a mensagem contiver "procon" (case insensitive)
    if (!msg.body.toLowerCase().includes('procon')) {
        return;
    }

    console.log(`Mensagem recebida de ${msg.from}: ${msg.body}`);

    try {
        // 1. Persistência: Identificar usuário e conversa
        const contact = await msg.getContact();
        const phone = contact.id.user; // O número real está aqui
        const user = await getOrCreateUser(phone);

        // Inicia a coleta de nome/CPF reais antes de seguir com o atendimento
        if (!user.cpf) {
            onboardingState.set(phone, { step: 'name' });
            await msg.reply('Olá! Sou o assistente virtual do Procon Jacareí. Antes de continuar, preciso confirmar alguns dados.\n\nQual o seu nome completo?');
            return;
        }

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
        await msg.reply(reply + '\n\nAo finalizar, avalie este atendimento enviando "feedback 1 a 5"');
        console.log(`Resposta enviada para ${msg.from}`);

    } catch (error) {
        console.error('Erro no processamento da mensagem:', error.message);
    }
});

client.initialize();
