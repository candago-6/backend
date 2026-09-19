const fs = require('fs');
const path = require('path');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const QRCode = require('qrcode');
const express = require('express');
const axios = require('axios');
const { isAuthorized } = require('./lib/auth');
const { flagAtivada } = require('./lib/config');

const app = express();
app.use(express.json());
const PORT = 8003;
const PLN_URL = process.env.PLN_URL || 'http://pln-pipeline:8001/api/fasttext/knn';
const MANAGER_URL = process.env.MANAGER_URL || 'http://service-manager:8002/api/v1';
const BOT_SECRET = process.env.BOT_SECRET || 'dev-bot-secret-change-me';

// Cliente do service-manager. O X-Bot-Secret vai como header padrao porque as
// rotas de atendimento exigem credencial (o bot nao tem login). E uma instancia
// separada de proposito: o `axios` solto continua atendendo o pln-pipeline, que
// nao deve receber o segredo.
const manager = axios.create({
    baseURL: MANAGER_URL,
    headers: { 'X-Bot-Secret': BOT_SECRET },
    timeout: 15000,
});

// A palavra-chave é uma trava de desenvolvimento, não uma regra de produto:
// enquanto o bot roda num celular pessoal ele não pode responder todo mundo que
// manda mensagem. Em produção o número é exclusivo do atendimento, então qualquer
// mensagem já inicia a conversa. Para desligar a trava: BOT_MODE=production.
const BOT_MODE = process.env.BOT_MODE || 'development';
const IS_PRODUCTION = BOT_MODE === 'production';
const TRIGGER_KEYWORD = (process.env.TRIGGER_KEYWORD || 'procon').toLowerCase();

// Contingencia por RAG, que o cliente ve como "IA Avancada": quando o DistilBERT
// nao entende, uma LLM remota tenta responder a partir do FAQ antes de a conversa
// contar mais uma falha. Desligada por padrao a pedido do cliente. Com ela
// desligada, toda resposta nao entendida conta falha e a terceira manda a conversa
// para a fila humana — ou seja, mais atendimentos chegam ao analista.
// Para reativar: RAG_FALLBACK_ENABLED=true no docker-compose.yml.
const RAG_FALLBACK_ENABLED = flagAtivada(process.env.RAG_FALLBACK_ENABLED);

let latestQR = null;
const pendingBotMessages = new Set();

// Sends into the chat the message came from. Deliberately not msg.reply(): that
// needs msg.id._serialized, which is undefined on LID-addressed messages.
async function botReply(msg, text) {
    return botSend(msg.from, text);
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
    if (!isAuthorized(req, BOT_SECRET)) return res.status(401).json({ error: 'unauthorized' });
    const { chatId, text } = req.body || {};
    if (!chatId || !text) return res.status(400).json({ error: 'chatId and text required' });
    try {
        await botSend(chatId, text);
        res.json({ status: 'sent' });
    } catch (e) {
        res.status(500).json({ error: 'send failed', detail: String(e) });
    }
});
// O QR e a credencial de pareamento: quem o le conecta o proprio aparelho a conta
// de atendimento, le todas as conversas e responde como se fosse o Procon. Por
// isso exige o mesmo segredo de /send. No navegador, use ?secret=<BOT_SECRET>.
// A porta 8003 nao e publicada no host (docker-compose.yml): o caminho normal de
// pareamento e o QR que o qrcode-terminal imprime em
// `docker compose logs -f whatsapp-bot`.
app.get('/qr', async (req, res) => {
    if (!isAuthorized(req, BOT_SECRET)) return res.status(401).json({ error: 'unauthorized' });
    if (!latestQR) return res.status(404).send('<h2>Aguarde o QR Code...</h2>');
    const pngBuffer = await QRCode.toBuffer(latestQR, { scale: 8 });
    res.setHeader('Content-Type', 'image/png');
    res.send(pngBuffer);
});
app.listen(PORT, () => console.log(`Bot na porta ${PORT} [modo: ${BOT_MODE}${IS_PRODUCTION ? '' : `, palavra-chave: ${TRIGGER_KEYWORD}`}, IA avancada: ${RAG_FALLBACK_ENABLED ? 'ligada' : 'desligada'}]. QR: neste log, ou GET /qr com X-Bot-Secret.`));

// O WhatsApp migrou as conversas 1:1 para endereçamento LID, então as mensagens
// recebidas chegam com `from` no formato <lid>@lid. O whatsapp-web.js 1.34.7 não
// trata LID em getChat()/getContact(): getChatModel() estoura um erro minificado
// ("r") nessas conversas. Resolvemos o LID para o JID de telefone aqui e evitamos
// as duas chamadas. Enviar mensagem continua funcionando porque sendMessage usa
// getChat com getAsModel:false, que não passa por getChatModel.
const lidCache = new Map();
async function lidToPhoneJid(id) {
    if (!id || !id.endsWith('@lid')) return id;
    if (lidCache.has(id)) return lidCache.get(id);

    let resolved = id;
    try {
        const pn = await client.pupPage.evaluate((lid) => {
            const wid = window.require('WAWebWidFactory').createWidFromWidLike(lid);
            return window.require('WAWebLidMigrationUtils').toPn(wid)?._serialized ?? null;
        }, id);
        if (pn) resolved = pn;
        else console.warn('[lidToPhoneJid] sem telefone para', id);
    } catch (e) {
        console.error('[lidToPhoneJid]', e?.message || e);
    }

    lidCache.set(id, resolved);
    return resolved;
}

// Predicados puros (sim/nao, filtro de chat, CPF, texto da verificacao) ficam em
// lib/predicates.js para serem testaveis sem subir o Chromium: npm test
const {
    semAcento,
    ragSemResposta,
    dizSim,
    dizNao,
    PERGUNTA_VERIFICACAO,
    isIgnorableChat,
    isValidCpf,
} = require('./lib/predicates');

// BUSCA UNIFICADA: Tenta achar o usuário por Telefone ou por WhatsApp ID (LID/JID)
async function findUser(phone, whatsappId) {
    try {
        // 1. Tenta pelo Telefone
        if (phone) {
            const r = await manager.get(`/users/phone/${phone}`);
            if (r.data) return r.data;
        }
    } catch (e) {}
    
    try {
        // 2. Tenta pelo WhatsApp ID (LID)
        if (whatsappId) {
            const r = await manager.get(`/users/whatsapp-id/${whatsappId}`);
            if (r.data) return r.data;
        }
    } catch (e) {}
    
    return null;
}

async function findActiveConversation(userId) {
    try { return (await manager.get(`/conversations/active/${userId}`)).data; } catch (e) { return null; }
}

async function getOrCreateUser(phone, whatsappId) {
    const user = await findUser(phone, whatsappId);
    if (user) {
        // Se achou mas o whatsappId ou phone estava faltando, atualiza
        if ((whatsappId && user.whatsapp_id !== whatsappId) || (phone && user.phone !== phone)) {
            const updated = await manager.put(`/users/${user.id}`, { phone, whatsapp_id: whatsappId });
            return updated.data;
        }
        return user;
    }
    return (await manager.post(`/users`, { name: "Cliente WhatsApp", phone, whatsapp_id: whatsappId, cpf: "" })).data;
}

async function getOrCreateConversation(userId) {
    const conv = await findActiveConversation(userId);
    if (conv) return conv;
    return (await manager.post(`/conversations`, { user_id: userId, protocol: `PROCON-${Date.now()}`, status: "open" })).data;
}

async function saveMessage(conversationId, role, content) {
    try { await manager.post(`/messages`, { conversation_id: conversationId, role, content }); } catch (e) {}
}

async function saveFeedback(conversationId, rating) {
    try { await manager.post(`/feedback`, { conversation_id: conversationId, rating, is_best_answer: rating >= 4 }); return true; } catch (e) { return false; }
}

const onboardingState = new Map();

// O whatsapp-web.js pode emitir 'ready' várias vezes na mesma sessão; sem esta
// trava cada emissão criava mais um setInterval e o polling se multiplicava.
let monitorStarted = false;
async function startMonitor() {
    if (monitorStarted) return;
    monitorStarted = true;
    console.log('[Monitor] Vigilante iniciado.');
    setInterval(async () => {
        try {
            const r = await manager.get(`/conversations`);
            const now = new Date();
            for (const conv of r.data) {
                if (!['open', 'waiting_human', 'confirming_closure'].includes(conv.status)) continue;
                const diff = (now - new Date(conv.updated_at)) / (1000 * 60);
                if (diff > 720) continue; 

                const u = (await manager.get(`/users/${conv.user_id}`)).data;
                const chatId = u.whatsapp_id || `${u.phone}@c.us`;

                // 5 min de silêncio, não 1: quem está digitando uma dúvida real
                // leva mais de um minuto e não pode receber "seu atendimento acabou?"
                // no meio da frase.
                if (conv.status === 'open' && diff >= 5) {
                    await botSend(chatId, 'Vi que você não mandou mais nada. Seu atendimento acabou?\n\n(Responda *Sim* para encerrar e avaliar)');
                    await manager.post(`/conversations/${conv.id}/update-status?status=confirming_closure`);
                } else if (conv.status === 'waiting_human' && diff >= 3 && !conv.patience_msg_sent) {
                    await botSend(chatId, 'Nossa fila está um pouco cheia no momento, agradecemos sua paciência! Logo um atendente falará com você.');
                    await manager.post(`/conversations/${conv.id}/mark-patience-sent`);
                } else if (conv.status === 'confirming_closure' && diff >= 5) {
                    await manager.post(`/conversations/${conv.id}/close`);
                }
            }
        } catch (e) { console.error('[Monitor]', e?.stack || e?.message || e); }
    }, 60000);
}

const client = new Client({
    authStrategy: new LocalAuth(),
    puppeteer: { executablePath: '/usr/bin/chromium', args: ['--no-sandbox'] },
    // Chromium can stall briefly during WhatsApp Web sync; give CDP calls more room before timing out.
    protocolTimeout: 120000,
});

const AUTH_DIR = path.join(process.cwd(), '.wwebjs_auth');

// Apaga as credenciais salvas, mas preserva o próprio diretório: ele é o ponto de
// montagem do volume wa_session e não pode ser removido.
function wipeSession() {
    try {
        for (const entry of fs.readdirSync(AUTH_DIR)) {
            fs.rmSync(path.join(AUTH_DIR, entry), { recursive: true, force: true });
        }
        console.log('[Client] sessao local apagada.');
    } catch (e) {
        console.error('[wipeSession]', e?.message || e);
    }
}

// Depois de um disconnect o whatsapp-web.js tenta reinjetar na mesma página e
// falha com "onQRChangedEvent already exists", deixando o cliente morto: nenhum
// evento 'qr' novo é emitido e /qr nunca mais serve um código. Em vez de tentar
// recuperar no mesmo processo, saímos e deixamos o restart do compose devolver um
// Chromium limpo.
let restarting = false;
let shuttingDown = false;
async function restartProcess(reason, wipe) {
    if (restarting || shuttingDown) return;
    restarting = true;
    console.error(`[Client] reiniciando processo (${reason})`);
    try { await client.destroy(); } catch (e) { console.error('[restart] destroy:', e?.message || e); }
    // LOGOUT significa que o WhatsApp matou a sessão de vez: restaurar as
    // credenciais salvas só entra em loop, então elas precisam ir embora.
    if (wipe) wipeSession();
    process.exit(1);
}

client.on('qr', (qr) => { latestQR = qr; qrcode.generate(qr, { small: true }); });
client.on('ready', () => { latestQR = null; console.log('Bot pronto!'); startMonitor(); });
client.on('disconnected', (reason) => restartProcess(`desconectado: ${reason}`, String(reason) === 'LOGOUT'));
client.on('auth_failure', (m) => restartProcess(`falha de autenticacao: ${m}`, true));

// A single Chromium/CDP hiccup must not take the whole bot down.
process.on('unhandledRejection', (err) => console.error('[unhandledRejection]', err));

// 1. EVENTO: VOCÊ FALANDO
client.on('message_create', async (msg) => {
    if (!msg.fromMe) return;
    try {
        if (isIgnorableChat(msg.to)) return;

        const whatsappId = await lidToPhoneJid(msg.to);
        // Extrai apenas os números do ID (antes do @) para garantir que temos o telefone real
        const phone = whatsappId.split('@')[0].replace(/\D/g, '');

        if (pendingBotMessages.has(msg.body)) return;

        // Procura o usuário por qualquer um dos IDs
        const user = await findUser(phone, whatsappId);
        if (!user) return; 
        const conv = await findActiveConversation(user.id);
        if (!conv) return;

        if (msg.body === '#finalizar') {
            await manager.post(`/conversations/${conv.id}/close`);
            await botSend(msg.to, '✅ *Atendimento encerrado com sucesso.*');
            return;
        }

        if (msg.body === '#fallback') {
            await manager.post(`/conversations/${conv.id}/update-status?status=waiting_human`);
            await botSend(msg.to, '🔁 *Atendimento marcado como pendente para um atendente humano.*');
            return;
        }

        if (!msg.body.startsWith('!') && !msg.body.startsWith('#')) {
            if (conv.status === 'waiting_human' || conv.status === 'open' || conv.status === 'confirming_closure') {
                console.log(`[Handover] SUCESSO! Mudando usuário ${user.id} para human_handover.`);
                await manager.post(`/conversations/${conv.id}/update-status?status=human_handover`);
                await saveMessage(conv.id, 'bot', `[Atendimento Manual] ${msg.body}`);
            }
        }
    } catch (e) { console.error('[message_create]', e?.stack || e?.message || e); }
});

// 2. EVENTO: CLIENTE FALANDO
client.on('message', async (msg) => {
  try {
    if (isIgnorableChat(msg.from)) return;

    const whatsappId = await lidToPhoneJid(msg.author || msg.from);
    const phone = whatsappId.split('@')[0].replace(/\D/g, '');
    // Sem o conteúdo da mensagem: os logs do container não são lugar para conversa alheia.
    console.log('[msg in]', msg.from, '->', whatsappId, 'type=', msg.type);

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
                await manager.put(`/users/${u.id}`, { name: onboarding.name, cpf: onboarding.cpf });
                await manager.post(`/conversations/${c.id}/mark-onboarded`);
                onboardingState.delete(msg.from);
                await botReply(msg, `Dados confirmados! Seu protocolo é: ${c.protocol}\n\nComo posso ajudar?`);
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
            if (dizSim(msg.body)) {
                await manager.post(`/conversations/${conv.id}/update-status?status=awaiting_feedback`);
                await botReply(msg, 'Entendido! Para encerrar, envie uma nota de 1 a 5 para o meu atendimento.');
            } else if (dizNao(msg.body)) {
                await manager.post(`/conversations/${conv.id}/update-status?status=open`);
                await botReply(msg, 'Certo! Pode mandar a sua próxima dúvida.');
            } else {
                // Nem sim nem nao: re-pergunta em vez de mandar aos modelos. É o que
                // impede uma despedida ("Obrigado") de virar uma pergunta nova.
                await botReply(msg, 'Por favor, responda apenas *Sim* ou *Não*.');
            }
            return;
        }
        if (conv.status === 'awaiting_feedback') {
            const rating = parseInt(msg.body.trim());
            if (rating >= 1 && rating <= 5) {
                await saveFeedback(conv.id, rating);
                await manager.post(`/conversations/${conv.id}/close`);
                await botReply(msg, 'Muito obrigado! Atendimento encerrado.');
            } else { await botReply(msg, 'Envie apenas o número de 1 a 5.'); }
            return;
        }
    }

    // Em produção qualquer mensagem é atendimento. Em desenvolvimento a palavra-chave
    // só é exigida para *iniciar*: com uma conversa já aberta a pessoa não precisa
    // repetir "procon" a cada pergunta.
    const deveAtender = IS_PRODUCTION || Boolean(conv) || msg.body.toLowerCase().includes(TRIGGER_KEYWORD);
    if (deveAtender) {
        console.log('[atendimento] resolvendo usuario', phone);
        const u = await getOrCreateUser(phone, whatsappId);
        console.log('[atendimento] user', u?.id);
        const c = await getOrCreateConversation(u.id);
        console.log('[atendimento] conversa', c?.id, 'onboarded=', c?.is_onboarded);

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

            if (RAG_FALLBACK_ENABLED && isFallback && c.failed_attempts === 1) {
                try {
                    const rag = await axios.post('http://pln-pipeline:8001/api/rag_remote', { question: msg.body, top_k: 3 }, { timeout: 45000 });
                    if (rag.data.answer && !ragSemResposta(rag.data.answer)) {
                        reply = "*[IA Avançada - Contingência]* " + rag.data.answer;
                        isFallback = false;
                    }
                } catch (e) {}
            }

            await saveMessage(c.id, 'bot', reply);
            if (isFallback) {
                const updated = await manager.post(`/conversations/${c.id}/increment-failures`);
                if (updated.data.status === 'waiting_human') return await botReply(msg, 'Estou te transferindo para um atendente humano. Aguarde.');
            } else { await manager.post(`/conversations/${c.id}/reset-failures`); }

            // A pergunta vai anexada à resposta (uma mensagem só) e a conversa fica
            // em confirming_closure: a próxima mensagem é lida como Sim/Não, não
            // como pergunta nova. O 'reply' salvo no banco fica sem este anexo.
            await botReply(msg, reply + PERGUNTA_VERIFICACAO);
            await manager.post(`/conversations/${c.id}/update-status?status=confirming_closure`);
        }
    } else {
        console.log('[ignorado] sem palavra-chave e sem conversa ativa:', whatsappId);
    }
  } catch (e) {
    console.error('[message handler]', {
        message: e?.message,
        stack: e?.stack,
        from: msg?.from,
        to: msg?.to,
        author: msg?.author,
        fromMe: msg?.fromMe,
        type: msg?.type,
        deviceType: msg?.deviceType,
        id: msg?.id?._serialized,
    });
  }
});

// Sem isso o Chromium morre no SIGKILL e deixa o perfil sujo, o que invalida a
// sessão e obriga a ler o QR de novo a cada restart.
for (const signal of ['SIGTERM', 'SIGINT']) {
    process.on(signal, async () => {
        if (shuttingDown) return;
        shuttingDown = true;
        console.log(`[${signal}] encerrando o cliente...`);
        try { await client.destroy(); } catch (e) { console.error('[shutdown]', e?.message || e); }
        process.exit(0);
    });
}

client.initialize();
