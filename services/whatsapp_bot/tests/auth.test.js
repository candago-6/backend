// Autenticação das rotas HTTP do bot (/send e /qr).
//
//     cd services/whatsapp_bot && npm test
//     docker compose run --rm --no-deps --entrypoint npm whatsapp-bot test

const test = require('node:test');
const assert = require('node:assert/strict');

const { secretsMatch, isAuthorized } = require('../lib/auth');

const SEGREDO = 'um-segredo-de-producao-qualquer';

test('secretsMatch aceita o segredo exato', () => {
    assert.equal(secretsMatch(SEGREDO, SEGREDO), true);
});

test('secretsMatch recusa segredo errado do mesmo tamanho', () => {
    const errado = SEGREDO.slice(0, -1) + 'X';
    assert.equal(errado.length, SEGREDO.length);
    assert.equal(secretsMatch(errado, SEGREDO), false);
});

test('secretsMatch recusa prefixo correto', () => {
    // Sem comparação de tempo constante, este é o caso que vaza o segredo
    // caractere a caractere.
    assert.equal(secretsMatch(SEGREDO.slice(0, 10), SEGREDO), false);
});

test('secretsMatch recusa vazio, nulo e tipo errado', () => {
    for (const valor of ['', null, undefined, 0, {}, [], true]) {
        assert.equal(secretsMatch(valor, SEGREDO), false, String(valor));
    }
});

test('secretsMatch recusa quando o segredo esperado está vazio', () => {
    // Guarda contra BOT_SECRET não configurado virar "qualquer um entra".
    assert.equal(secretsMatch('', ''), false);
    assert.equal(secretsMatch('qualquer coisa', ''), false);
});

test('isAuthorized aceita o header X-Bot-Secret', () => {
    // É o caminho que o service-manager usa ao chamar /send.
    assert.equal(isAuthorized({ headers: { 'x-bot-secret': SEGREDO } }, SEGREDO), true);
});

test('isAuthorized aceita ?secret= na query', () => {
    // É o caminho de quem abre /qr no navegador, que não manda header.
    assert.equal(isAuthorized({ query: { secret: SEGREDO } }, SEGREDO), true);
});

test('isAuthorized recusa requisição sem credencial', () => {
    assert.equal(isAuthorized({ headers: {}, query: {} }, SEGREDO), false);
    assert.equal(isAuthorized({}, SEGREDO), false);
    assert.equal(isAuthorized(undefined, SEGREDO), false);
});

test('isAuthorized recusa credencial errada no header e na query', () => {
    assert.equal(isAuthorized({ headers: { 'x-bot-secret': 'errado' } }, SEGREDO), false);
    assert.equal(isAuthorized({ query: { secret: 'errado' } }, SEGREDO), false);
});

test('isAuthorized não confunde outro header com o segredo', () => {
    assert.equal(isAuthorized({ headers: { authorization: SEGREDO } }, SEGREDO), false);
    assert.equal(isAuthorized({ headers: { 'x-bot-token': SEGREDO } }, SEGREDO), false);
});

test('isAuthorized aceita se qualquer um dos dois caminhos estiver correto', () => {
    assert.equal(
        isAuthorized({ headers: { 'x-bot-secret': 'errado' }, query: { secret: SEGREDO } }, SEGREDO),
        true
    );
    assert.equal(
        isAuthorized({ headers: { 'x-bot-secret': SEGREDO }, query: { secret: 'errado' } }, SEGREDO),
        true
    );
});

test('isAuthorized recusa array vindo de query repetida', () => {
    // ?secret=a&secret=b faz o Express entregar um array; não pode virar `true`.
    assert.equal(isAuthorized({ query: { secret: [SEGREDO, 'outro'] } }, SEGREDO), false);
});
