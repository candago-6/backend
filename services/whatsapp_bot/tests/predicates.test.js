// Testes dos predicados que decidem o rumo da conversa no bot.
// Rodam sem Chromium, sem WhatsApp e sem rede:
//
//     cd services/whatsapp_bot && npm test
//     docker compose run --rm --no-deps --entrypoint npm whatsapp-bot test

const test = require('node:test');
const assert = require('node:assert/strict');

const {
    semAcento,
    ragSemResposta,
    dizSim,
    dizNao,
    PERGUNTA_VERIFICACAO,
    isIgnorableChat,
    isValidCpf,
} = require('../lib/predicates');

test('semAcento normaliza acento e caixa', () => {
    assert.equal(semAcento('Não'), 'nao');
    assert.equal(semAcento('NÃO ENCONTREI'), 'nao encontrei');
    assert.equal(semAcento('Informação'), 'informacao');
    assert.equal(semAcento('Sim'), 'sim');
});

test('semAcento aceita valores ausentes sem quebrar', () => {
    // msg.body pode vir undefined em mensagem de mídia.
    assert.equal(semAcento(undefined), '');
    assert.equal(semAcento(null), '');
    assert.equal(semAcento(123), '123');
});

test('ragSemResposta pega as duas formas de "não encontrei" do pipeline', () => {
    // Regressão: o teste antigo era includes("não encontrei") e não pegava
    // nenhuma das duas — toda resposta vazia do RAG virava resposta boa.
    assert.equal(ragSemResposta('Nao encontrei essa informacao no documento.'), true);
    assert.equal(
        ragSemResposta(
            'Não encontrei essa informação em minha base dados. Redirecionando para atendimento humano...'
        ),
        true
    );
});

test('ragSemResposta não descarta resposta válida', () => {
    assert.equal(ragSemResposta('O prazo é de 7 dias corridos.'), false);
    assert.equal(ragSemResposta('Você encontrou o produto com defeito?'), false);
    assert.equal(ragSemResposta(''), false);
    assert.equal(ragSemResposta(undefined), false);
});

test('dizSim reconhece as formas que o cliente realmente manda', () => {
    for (const texto of ['Sim', 'sim', 'SIM', 'sim!', 'sim, resolveu', 'acho que sim']) {
        assert.equal(dizSim(texto), true, texto);
    }
});

test('dizSim não casa com palavra que só contém "sim"', () => {
    // A borda \b existe justamente por causa destes.
    for (const texto of ['assim', 'simples', 'simulação', 'simpatico']) {
        assert.equal(dizSim(texto), false, texto);
    }
});

test('dizNao reconhece com e sem acento', () => {
    for (const texto of ['Não', 'nao', 'NÃO', 'não, ainda não resolveu', 'nao mesmo']) {
        assert.equal(dizNao(texto), true, texto);
    }
});

test('dizNao não casa com palavra que só contém "nao"', () => {
    for (const texto of ['naopode', 'naoquero123']) {
        assert.equal(dizNao(texto), false, texto);
    }
});

test('despedida não é lida como sim nem como não', () => {
    // É o que mantém a conversa em confirming_closure e faz o bot repetir a
    // pergunta, em vez de mandar "Obrigado" para os modelos como pergunta nova.
    for (const texto of ['Obrigado', 'valeu', 'ok', 'blz', 'tá bom']) {
        assert.equal(dizSim(texto), false, texto);
        assert.equal(dizNao(texto), false, texto);
    }
});

test('resposta ambígua contendo sim e não é tratada como sim (ordem do handler)', () => {
    // O handler testa dizSim antes de dizNao; este teste congela essa precedência.
    assert.equal(dizSim('sim e não'), true);
    assert.equal(dizNao('sim e não'), true);
});

test('isIgnorableChat barra grupo, canal e status', () => {
    assert.equal(isIgnorableChat('120363000000000000@g.us'), true);
    assert.equal(isIgnorableChat('120363000000000000@newsletter'), true);
    assert.equal(isIgnorableChat('status@broadcast'), true);
});

test('isIgnorableChat deixa passar conversa 1:1', () => {
    assert.equal(isIgnorableChat('5512991234567@c.us'), false);
    assert.equal(isIgnorableChat('123456789012345@lid'), false);
});

test('isIgnorableChat barra id ausente ou de tipo errado', () => {
    assert.equal(isIgnorableChat(undefined), true);
    assert.equal(isIgnorableChat(null), true);
    assert.equal(isIgnorableChat(12345), true);
});

test('isValidCpf aceita 11 dígitos em qualquer formatação', () => {
    assert.equal(isValidCpf('52998224725'), true);
    assert.equal(isValidCpf('529.982.247-25'), true);
    assert.equal(isValidCpf('529 982 247 25'), true);
});

test('isValidCpf recusa quantidade errada de dígitos', () => {
    assert.equal(isValidCpf('1234567890'), false);
    assert.equal(isValidCpf('123456789012'), false);
    assert.equal(isValidCpf(''), false);
    assert.equal(isValidCpf('meu cpf'), false);
});

test('isValidCpf NÃO valida dígito verificador (limitação conhecida)', () => {
    // Documentado de propósito: qualquer sequência de 11 dígitos entra no
    // cadastro. Ver relatório: item "CPF sem dígito verificador".
    assert.equal(isValidCpf('00000000000'), true);
    assert.equal(isValidCpf('11111111111'), true);
    assert.equal(isValidCpf('12345678900'), true);
});

test('pergunta de verificação pede Sim ou Não e é reconhecida pelos predicados', () => {
    assert.match(PERGUNTA_VERIFICACAO, /Resolvi o seu problema/);
    assert.match(PERGUNTA_VERIFICACAO, /\*Sim\*/);
    assert.match(PERGUNTA_VERIFICACAO, /\*Não\*/);
});

test('a resposta que o cliente dá à pergunta de verificação é entendida', () => {
    // Fecha o ciclo: o texto que o bot manda e a leitura da resposta combinam.
    assert.equal(dizSim('Sim'), true);
    assert.equal(dizNao('Não'), true);
});
