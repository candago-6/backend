const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const QRCode = require('qrcode');
const express = require('express');
const axios = require('axios');

const app = express();
app.use(express.json());
const PORT = 8003;
const PLN_URL = process.env.PLN_URL || 'http://pln-pipeline:8001/api/fasttext/knn';
const MANAGER_URL = process.env.MANAGER_URL || 'http://service-manager:8002/api/v1';
const BOT_SECRET = process.env.BOT_SECRET || 'dev-bot-secret-change-me';

let latestQR = null;
const pendingBotMessages = new Set();

async function botReply(msg, text) {
    pendingBotMessages.add(text);
    const reply = await msg.reply(text);
    setTimeout(() => pendingBotMessages.delete(text), 5000);
    return reply;
}

async function botSend(chatId, text) {
    pendingBotMessages.add(text);
    const msg = await client.sendMessage(chatId, text);
    setTimeout(() => pendingBotMessages.delete(text), 5000);
    return msg;
}

app.get('/api/v1/health', (req, res) => res.json({ status: 'ok', service: 'whatsapp-bot (node)' }));

// Outbound send: used by service_manager when an analyst replies from the dashboard (live handover).
app.post('/send', async (req, res) => {
    if (req.headers['x-bot-secret'] !== BOT_SECRET) return res.status(401).json({ error: 'unauthorized' });
    const { chatId, text } = req.body || {};
    if (!chatId || !text) return res.status(400).json({ error: 'chatId and text required' });
    try {
        await botSend(chatId, text);
        res.json({ status: 'sent' });
    } catch (e) {
        res.status(500).json({ error: 'send failed', detail: String(e) });
    }
});
app.get('/qr', async (req, res) => {
    if (!latestQR) return res.status(404).send('<h2>Aguarde o QR Code...</h2>');
    const pngBuffer = await QRCode.toBuffer(latestQR, { scale: 8 });
    res.setHeader('Content-Type', 'image/png');
    res.send(pngBuffer);
});
app.listen(PORT, () => console.log(`Bot na porta ${PORT}. QR: http://localhost:${PORT}/qr`));

// BUSCA UNIFICADA: Tenta achar o usuário por Telefone ou por WhatsApp ID (LID/JID)
async function findUser(phone, whatsappId) {
    try {
        // 1. Tenta pelo Telefone
        if (phone) {
            const r = await axios.get(`${MANAGER_URL}/users/phone/${phone}`);
            if (r.data) return r.data;
        }
    } catch (e) {}
    
    try {
        // 2. Tenta pelo WhatsApp ID (LID)
        if (whatsappId) {
            const r = await axios.get(`${MANAGER_URL}/users/whatsapp-id/${whatsappId}`);
            if (r.data) return r.data;
        }
    } catch (e) {}
    
    return null;
}

async function findActiveConversation(userId) {
    try { return (await axios.get(`${MANAGER_URL}/conversations/active/${userId}`)).data; } catch (e) { return null; }
}

async function getOrCreateUser(phone, whatsappId) {
    const user = await findUser(phone, whatsappId);
    if (user) {
        // Se achou mas o whatsappId ou phone estava faltando, atualiza
        if ((whatsappId && user.whatsapp_id !== whatsappId) || (phone && user.phone !== phone)) {
            const updated = await axios.put(`${MANAGER_URL}/users/${user.id}`, { phone, whatsapp_id: whatsappId });
            return updated.data;
        }
        return user;
    }
    return (await axios.post(`${MANAGER_URL}/users`, { name: "Cliente WhatsApp", phone, whatsapp_id: whatsappId, cpf: "" })).data;
}

async function getOrCreateConversation(userId) {
    const conv = await findActiveConversation(userId);
    if (conv) return conv;
    return (await axios.post(`${MANAGER_URL}/conversations`, { user_id: userId, protocol: `PROCON-${Date.now()}`, status: "open" })).data;
}

async function saveMessage(conversationId, role, content) {
    try { await axios.post(`${MANAGER_URL}/messages`, { conversation_id: conversationId, role, content }); } catch (e) {}
}

async function saveFeedback(conversationId, rating) {
    try { await axios.post(`${MANAGER_URL}/feedback`, { conversation_id: conversationId, rating, is_best_answer: rating >= 4 }); return true; } catch (e) { return false; }
}

const onboardingState = new Map();
const isValidCpf = (v) => v.replace(/\D/g, '').length === 11;

async function startMonitor() {
    console.log('[Monitor] Vigilante iniciado.');
    setInterval(async () => {
        try {
            const r = await axios.get(`${MANAGER_URL}/conversations`);
            const now = new Date();
            for (const conv of r.data) {
                if (!['open', 'waiting_human', 'confirming_closure'].includes(conv.status)) continue;
                const diff = (now - new Date(conv.updated_at)) / (1000 * 60);
                if (diff > 720) continue; 

                const u = (await axios.get(`${MANAGER_URL}/users/${conv.user_id}`)).data;
                const chatId = u.whatsapp_id || `${u.phone}@c.us`;

                if (conv.status === 'open' && diff >= 1) {
                    await botSend(chatId, 'Vi que você não mandou mais nada. Seu atendimento acabou?\n\n(Responda *Sim* para encerrar e avaliar)');
                    await axios.post(`${MANAGER_URL}/conversations/${conv.id}/update-status?status=confirming_closure`);
                } else if (conv.status === 'waiting_human' && diff >= 3 && !conv.patience_msg_sent) {
                    await botSend(chatId, 'Nossa fila está um pouco cheia no momento, agradecemos sua paciência! Logo um atendente falará com você.');
                    await axios.post(`${MANAGER_URL}/conversations/${conv.id}/mark-patience-sent`);
                } else if (conv.status === 'confirming_closure' && diff >= 5) {
                    await axios.post(`${MANAGER_URL}/conversations/${conv.id}/close`);
                }
            }
        } catch (e) {}
    }, 60000);
}

const client = new Client({
    authStrategy: new LocalAuth(),
    puppeteer: { executablePath: '/usr/bin/chromium', args: ['--no-sandbox'] },
    // Chromium can stall briefly during WhatsApp Web sync; give CDP calls more room before timing out.
    protocolTimeout: 120000,
});

client.on('qr', (qr) => { latestQR = qr; qrcode.generate(qr, { small: true }); });
client.on('ready', () => { latestQR = null; console.log('Bot pronto!'); startMonitor(); });
client.on('disconnected', (reason) => console.error('[Client] desconectado:', reason));

// A single Chromium/CDP hiccup must not take the whole bot down.
process.on('unhandledRejection', (err) => console.error('[unhandledRejection]', err));

// 1. EVENTO: VOCÊ FALANDO
client.on('message_create', async (msg) => {
    if (!msg.fromMe) return;
    try {
        const chat = await msg.getChat();
        if (chat.isGroup) return;

        const contact = await client.getContactById(msg.to);
        const whatsappId = contact.id._serialized;
        // Extrai apenas os números do ID (antes do @) para garantir que temos o telefone real
        const phone = whatsappId.split('@')[0].replace(/\D/g, '');

        if (pendingBotMessages.has(msg.body)) return;

        // Procura o usuário por qualquer um dos IDs
        const user = await findUser(phone, whatsappId);
        if (!user) return; 
        const conv = await findActiveConversation(user.id);
        if (!conv) return;

        if (msg.body === '#finalizar') {
            await axios.post(`${MANAGER_URL}/conversations/${conv.id}/close`);
            await botSend(msg.to, '✅ *Atendimento encerrado com sucesso.*');
            return;
        }

        if (msg.body === '#fallback') {
            await axios.post(`${MANAGER_URL}/conversations/${conv.id}/update-status?status=waiting_human`);
            await botSend(msg.to, '🔁 *Atendimento marcado como pendente para um atendente humano.*');
            return;
        }

        if (!msg.body.startsWith('!') && !msg.body.startsWith('#')) {
            if (conv.status === 'waiting_human' || conv.status === 'open' || conv.status === 'confirming_closure') {
                console.log(`[Handover] SUCESSO! Mudando usuário ${user.id} para human_handover.`);
                await axios.post(`${MANAGER_URL}/conversations/${conv.id}/update-status?status=human_handover`);
                await saveMessage(conv.id, 'bot', `[Atendimento Manual] ${msg.body}`);
            }
        }
    } catch (e) {}
});

// 2. EVENTO: CLIENTE FALANDO
client.on('message', async (msg) => {
  try {
    const chat = await msg.getChat();
    if (chat.isGroup) return;

    const contact = await msg.getContact();
    const whatsappId = contact.id._serialized;
    const phone = whatsappId.split('@')[0].replace(/\D/g, '');

    const user = await findUser(phone, whatsappId);
    const conv = user ? await findActiveConversation(user.id) : null;

    if (conv && (conv.status === 'waiting_human' || conv.status === 'human_handover')) {
        await saveMessage(conv.id, 'user', msg.body);
        return; 
    }

    const onboarding = onboardingState.get(msg.from);
    if (onboarding) {
        const u = await getOrCreateUser(phone, whatsappId);
        const c = await getOrCreateConversation(u.id);
        
        if (onboarding.step === 'name') {
            onboarding.name = msg.body.trim();
            onboarding.step = 'cpf';
            await botReply(msg, 'Obrigado! Agora informe seu CPF (apenas números).');
        } else if (onboarding.step === 'cpf') {
            const cleanCpf = msg.body.replace(/\D/g, '');
            if (!isValidCpf(cleanCpf)) return await botReply(msg, 'CPF inválido. Envie os 11 números.');
            onboarding.cpf = cleanCpf;
            onboarding.step = 'confirm';
            await botReply(msg, `Confirma seus dados?\n\n*Nome:* ${onboarding.name}\n*CPF:* ${onboarding.cpf}\n\nResponda *Sim* para confirmar ou *Não* para corrigir.`);
        } else if (onboarding.step === 'confirm') {
            const resp = msg.body.toLowerCase();
            if (resp.includes('sim')) {
                await axios.put(`${MANAGER_URL}/users/${u.id}`, { name: onboarding.name, cpf: onboarding.cpf });
                await axios.post(`${MANAGER_URL}/conversations/${c.id}/mark-onboarded`);
                onboardingState.delete(msg.from);
                await botReply(msg, `Dados confirmados! Seu protocolo é: ${c.protocol}\n\nComo posso ajudar? (mencione "procon")`);
            } else if (resp.includes('não') || resp.includes('nao')) {
                onboarding.step = 'name';
                await botReply(msg, 'Entendido. Vamos recomeçar.\n\nQual o seu nome completo?');
            } else {
                await botReply(msg, 'Por favor, responda apenas *Sim* ou *Não*.');
            }
        }
        return;
    }

    if (conv) {
        if (conv.status === 'confirming_closure') {
            if (msg.body.toLowerCase().includes('sim')) {
                await axios.post(`${MANAGER_URL}/conversations/${conv.id}/update-status?status=awaiting_feedback`);
                await botReply(msg, 'Entendido! Para encerrar, envie uma nota de 1 a 5 para o meu atendimento.');
            } else {
                await axios.post(`${MANAGER_URL}/conversations/${conv.id}/update-status?status=open`);
                await botReply(msg, 'Certo! Como posso continuar te ajudando?');
            }
            return;
        }
        if (conv.status === 'awaiting_feedback') {
            const rating = parseInt(msg.body.trim());
            if (rating >= 1 && rating <= 5) {
                await saveFeedback(conv.id, rating);
                await axios.post(`${MANAGER_URL}/conversations/${conv.id}/close`);
                await botReply(msg, 'Muito obrigado! Atendimento encerrado.');
            } else { await botReply(msg, 'Envie apenas o número de 1 a 5.'); }
            return;
        }
    }

    if (msg.body.toLowerCase().includes('procon')) {
        const u = await getOrCreateUser(phone, whatsappId);
        const c = await getOrCreateConversation(u.id);

        if (!c.is_onboarded) {
            onboardingState.set(msg.from, { step: 'name' });
            await botReply(msg, 'Olá! Sou o assistente virtual do Procon Jacareí. Antes de continuar, preciso confirmar seus dados.\n\nQual o seu nome completo?');
        } else {
            await saveMessage(c.id, 'user', msg.body);
            let pln;
            try {
                pln = await axios.post(PLN_URL, { raw_text: msg.body }, { timeout: 15000 });
            } catch (e) {
                console.error('[PLN indisponível]', e?.message || e);
                return await botReply(msg, 'Estou com uma instabilidade técnica no momento. Por favor, tente novamente em instantes.');
            }
            let reply = pln.data.class_response;
            let isFallback = pln.data.is_fallback;

            if (isFallback && c.failed_attempts === 1) {
                try {
                    const rag = await axios.post('http://pln-pipeline:8001/api/rag_remote', { question: msg.body, top_k: 3 }, { timeout: 45000 });
                    if (rag.data.answer && !rag.data.answer.includes("não encontrei")) {
                        reply = "*[IA Avançada - Contingência]* " + rag.data.answer;
                        isFallback = false;
                    }
                } catch (e) {}
            }

            await saveMessage(c.id, 'bot', reply);
            if (isFallback) {
                const updated = await axios.post(`${MANAGER_URL}/conversations/${c.id}/increment-failures`);
                if (updated.data.status === 'waiting_human') return await botReply(msg, 'Estou te transferindo para um atendente humano. Aguarde.');
            } else { await axios.post(`${MANAGER_URL}/conversations/${c.id}/reset-failures`); }

            await botReply(msg, reply);
        }
    }
  } catch (e) {
    console.error('[message handler]', e?.message || e);
  }
});

client.initialize();
