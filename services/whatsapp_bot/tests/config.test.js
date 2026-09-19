// Leitura das flags de ambiente do bot.
//
//     cd services/whatsapp_bot && npm test

const test = require('node:test');
const assert = require('node:assert/strict');

const { flagAtivada } = require('../lib/config');

test('liga com os valores afirmativos aceitos', () => {
    for (const valor of ['true', 'TRUE', 'True', '1', 'yes', 'on', 'sim', '  true  ']) {
        assert.equal(flagAtivada(valor), true, valor);
    }
});

test('fica desligada quando a variável não existe', () => {
    // É o caso de rodar o bot sem o docker-compose: o padrão tem que ser o
    // comportamento que o cliente pediu, não o contrário.
    assert.equal(flagAtivada(undefined), false);
    assert.equal(flagAtivada(null), false);
    assert.equal(flagAtivada(''), false);
});

test('fica desligada com valores negativos explícitos', () => {
    for (const valor of ['false', 'FALSE', '0', 'no', 'off', 'nao', 'não']) {
        assert.equal(flagAtivada(valor), false, valor);
    }
});

test('valor mal digitado deixa desligado, nunca ligado', () => {
    // O fail-safe aponta para "desligado": um typo não pode reativar sozinho
    // uma funcionalidade que o cliente pediu para não usar.
    for (const valor of ['ture', 'tru', 'ativado', 'enabled', 'y', 'ok', '2', '-1']) {
        assert.equal(flagAtivada(valor), false, valor);
    }
});

test('não aceita tipos que não sejam string', () => {
    for (const valor of [true, 1, {}, [], ['true']]) {
        assert.equal(flagAtivada(valor), false, String(valor));
    }
});
