// Leitura de flags de ambiente. Isolada aqui para ter teste sem subir o
// Chromium: npm test

// Só liga com um valor afirmativo explícito. Qualquer outra coisa — vazio,
// ausente, "flase", "off", um espaço — deixa desligado.
//
// A direção do fail-safe é de propósito: estas flags guardam funcionalidade que
// o cliente pediu para não usar, então um valor mal digitado tem que resultar em
// "desligado", nunca em "ligado por acidente".
function flagAtivada(valor) {
    if (typeof valor !== 'string') return false;
    return ['true', '1', 'yes', 'on', 'sim'].includes(valor.trim().toLowerCase());
}

module.exports = { flagAtivada };
