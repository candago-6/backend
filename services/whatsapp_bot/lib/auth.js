// Autenticação das rotas HTTP do bot. Isolada aqui para ter teste sem subir o
// Chromium: npm test
//
// Duas formas de apresentar o segredo, de propósito:
//   - header X-Bot-Secret ....... usado pelo service-manager em /send
//   - query ?secret=... ......... usado por gente abrindo /qr no navegador,
//                                 que não tem como mandar header
//
// A query aparece em log de proxy e no histórico do navegador. Como o segredo do
// bot é rotacionável e o QR só vale enquanto o pareamento está aberto, o
// compromisso vale a pena; o header continua sendo o caminho preferido.

const crypto = require('crypto');

// Comparação de tempo constante: um `===` vaza, pelo tempo de resposta, quantos
// caracteres iniciais o palpite acertou.
function secretsMatch(recebido, esperado) {
    if (typeof recebido !== 'string' || typeof esperado !== 'string') return false;
    if (recebido.length === 0 || esperado.length === 0) return false;

    const a = Buffer.from(recebido, 'utf8');
    const b = Buffer.from(esperado, 'utf8');
    // timingSafeEqual exige o mesmo tamanho; o comprimento vaza, o conteúdo não.
    if (a.length !== b.length) return false;
    return crypto.timingSafeEqual(a, b);
}

// `req` só precisa ter headers e query — é o que permite testar sem Express.
function isAuthorized(req, esperado) {
    const headers = (req && req.headers) || {};
    const query = (req && req.query) || {};
    return secretsMatch(headers['x-bot-secret'], esperado) || secretsMatch(query.secret, esperado);
}

module.exports = { secretsMatch, isAuthorized };
