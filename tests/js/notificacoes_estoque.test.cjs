"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../app/static/js/notificacoes_estoque.js"), "utf8");
const cacheKey = "estoque.notificacoes.v1.ficticio";
const counts = (total = 0, preventive = 0, pending = 0) => ({total, immediate: total - preventive, preventive, pending});
const flush = () => new Promise((resolve) => setImmediate(resolve));
class Element {
    constructor() { this.dataset = {}; this.children = []; this.events = new Map(); this.attributes = new Map(); this._text = ""; }
    addEventListener(type, callback) { this.events.set(type, [...(this.events.get(type) || []), callback]); }
    fire(type, details = {}) { for (const callback of this.events.get(type) || []) callback({target: this, ...details}); }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
    replaceChildren(...children) { this._text = ""; this.children = children; }
    setAttribute(key, value) { this.attributes.set(key, String(value)); }
    getAttribute(key) { return this.attributes.get(key); }
    querySelector() { return this.summary; }
}
function storage() {
    const items = new Map();
    return {getItem: (key) => items.get(key) ?? null, setItem: (key, value) => items.set(key, String(value)), removeItem: (key) => items.delete(key)};
}
function fixture(responses, {session = storage(), local = storage(), clock = {now: Date.parse("2026-01-11T16:00:00Z")}, invalidate = false, mounted = true, scope = "ficticio"} = {}) {
    const elements = Object.fromEntries(["estoque-notificacoes", "notificacoes-contagem", "notificacoes-status", "notificacoes-resumo", "notificacoes-atualizar"].map((id) => [id, new Element()]));
    const panel = elements["estoque-notificacoes"];
    Object.assign(panel.dataset, {api: "/api/estoque/alertas/resumo", cacheScope: scope, invalidar: String(invalidate)});
    panel.summary = new Element();
    const document = new Element();
    document.visibilityState = "visible";
    document.getElementById = (id) => mounted ? elements[id] : null;
    document.createElement = () => new Element();
    const window = new Element();
    window.sessionStorage = session;
    window.localStorage = local;
    const timers = [];
    window.setInterval = (callback, milliseconds) => timers.push({callback, milliseconds});
    const calls = [], queue = [...responses];
    let jsonCalls = 0;
    class FakeDate extends Date { static now() { return clock.now; } }
    const fetch = (url, options) => {
        calls.push({url, options});
        const result = queue.shift();
        if (result instanceof Error) return Promise.reject(result);
        if (typeof result === "function") return result();
        return Promise.resolve({ok: !result?.httpFailure, json: () => {
            jsonCalls++;
            return result?.invalidJSON ? Promise.reject(new Error("ERRO_PRIVADO_FICTICIO")) : Promise.resolve(result);
        }});
    };
    vm.runInNewContext(source, {document, window, fetch, Date: FakeDate});
    return {window, document, panel, calls, clock, session, local, timers,
        count: elements["notificacoes-contagem"], status: elements["notificacoes-status"], list: elements["notificacoes-resumo"], button: elements["notificacoes-atualizar"],
        get jsonCalls() { return jsonCalls; }, text: () => Object.values(elements).map((item) => item.textContent + item.title).join(" ")};
}

test("consulta somente o resumo por GET e exibe contagens acessíveis", async () => {
    const view = fixture([counts(3, 2, 1)]);
    await flush();
    assert.equal(view.count.textContent, "3");
    assert.match(view.list.textContent, /Alertas imediatos: 1\./);
    assert.match(view.list.textContent, /Alertas preventivos: 2\./);
    assert.match(view.list.textContent, /desatualizada: 1\./);
    assert.match(view.panel.summary.getAttribute("aria-label"), /3 alertas ativos/);
    assert.equal(view.calls[0].url, "/api/estoque/alertas/resumo");
    assert.equal(view.calls[0].options.method, "GET");
    assert.equal(view.calls[0].options.cache, "no-store");
    assert.equal(view.calls[0].options.credentials, "same-origin");
    assert.equal(view.calls[0].options.body, undefined);
});
test("zero confirmado é diferente de falha de consulta", async () => {
    const view = fixture([counts()]);
    await flush();
    assert.equal(view.count.textContent, "0");
    assert.equal(view.status.textContent, "Não há alertas ativos.");
    assert.equal(view.count.dataset.stale, "false");
});
test("navegação restaura contagem imediatamente sem nova requisição", async () => {
    const first = fixture([counts(3, 1)]);
    await flush();
    const next = fixture([], first);
    assert.equal(next.count.textContent, "3");
    await flush();
    assert.equal(next.calls.length, 0);
    next.panel.open = true;
    next.panel.fire("toggle");
    next.window.fire("focus");
    next.document.fire("visibilitychange");
    await flush();
    assert.equal(next.calls.length, 0);
    assert.deepEqual(Object.keys(JSON.parse(next.session.getItem(cacheKey)).counts).sort(), ["immediate", "pending", "preventive", "total"]);
});
test("cache expirado preserva contagem enquanto revalida", async () => {
    const first = fixture([counts(2)]);
    await flush();
    first.clock.now += 30000;
    let release;
    const next = fixture([() => new Promise((resolve) => { release = resolve; })], first);
    assert.equal(next.count.textContent, "2");
    assert.equal(next.count.dataset.stale, "true");
    release({ok: true, json: async () => counts(1)});
    await flush();
    assert.equal(next.count.textContent, "1");
    assert.equal(next.count.dataset.stale, "false");
});
test("mudança do dia comercial em Manaus invalida mesmo cache de poucos segundos", async () => {
    const clock = {now: Date.parse("2026-01-12T03:59:55Z")};
    const first = fixture([counts(1, 1)], {clock});
    await flush();
    clock.now += 10000;
    const next = fixture([counts(1, 1, 1)], first);
    await flush();
    assert.equal(next.calls.length, 1);
    assert.match(next.list.textContent, /desatualizada: 1\./);
});
test("operação confirmada invalida cache antes de recarregar a página", async () => {
    const first = fixture([counts(1)]);
    await flush();
    first.window.estoqueNotificacoes.invalidar({recarregar: false});
    assert.equal(first.session.getItem(cacheKey), null);
    assert.equal(first.calls.length, 1);
    const next = fixture([counts(2)], first);
    await flush();
    assert.equal(next.count.textContent, "2");
    assert.equal(next.calls.length, 1);
});
test("mudança de dia durante GET descarta o resumo do dia anterior", async () => {
    const clock = {now: Date.parse("2026-01-12T03:59:55Z")};
    let release;
    const view = fixture([() => new Promise((resolve) => { release = resolve; }), counts(1, 1, 1)], {clock});
    clock.now += 10000;
    release({ok: true, json: async () => counts(1, 1, 0)});
    await flush();
    assert.equal(view.calls.length, 2);
    assert.match(view.list.textContent, /desatualizada: 1\./);
});
test("retorno de formulário POST ignora contagem em cache", async () => {
    const first = fixture([counts(1)]);
    await flush();
    const next = fixture([counts(2)], {...first, invalidate: true});
    await flush();
    assert.equal(next.count.textContent, "2");
    assert.equal(next.calls.length, 1);
});
test("outra aba invalida a contagem e atualiza sem retransmitir evento", async () => {
    const view = fixture([counts(1), counts(2)]);
    await flush();
    view.local.setItem(cacheKey + ".invalidacao", "ficticio-novo");
    view.window.fire("storage", {key: cacheKey + ".invalidacao"});
    await flush();
    assert.equal(view.count.textContent, "2");
    assert.equal(view.local.getItem(cacheKey + ".invalidacao"), "ficticio-novo");
    view.window.fire("storage", {key: "outro-componente"});
    await flush();
    assert.equal(view.calls.length, 2);
});
test("invalidação em aba oculta aguarda visibilidade", async () => {
    const view = fixture([counts(1), counts(2)]);
    await flush();
    view.document.visibilityState = "hidden";
    view.window.fire("storage", {key: cacheKey + ".invalidacao"});
    assert.equal(view.calls.length, 1);
    view.document.visibilityState = "visible";
    view.document.fire("visibilitychange");
    await flush();
    assert.equal(view.count.textContent, "2");
});
test("voltar pelo histórico do navegador detecta invalidação perdida enquanto a página estava suspensa", async () => {
    const view = fixture([counts(1), counts(2)]);
    await flush();
    view.local.setItem(cacheKey + ".invalidacao", "revisao-mais-recente-ficticia");
    view.window.fire("pageshow");
    await flush();
    assert.equal(view.count.textContent, "2");
    assert.equal(view.calls.length, 2);
});
test("invalidação durante GET descarta resposta anterior e reconsulta", async () => {
    let release;
    const view = fixture([() => new Promise((resolve) => { release = resolve; }), counts(2)]);
    view.window.estoqueNotificacoes.invalidar();
    release({ok: true, json: async () => counts(9)});
    await flush();
    assert.equal(view.calls.length, 2);
    assert.equal(view.count.textContent, "2");
    assert.equal(JSON.parse(view.session.getItem(cacheKey)).counts.total, 2);
});
test("revalidação periódica respeita cooldown, aba oculta e agrupa requisições", async () => {
    const view = fixture([counts(), counts(1), counts(2)]);
    await flush();
    assert.equal(view.timers[0].milliseconds, 30000);
    view.timers[0].callback();
    assert.equal(view.calls.length, 1);
    view.clock.now += 30000;
    view.timers[0].callback();
    view.window.fire("focus");
    await flush();
    assert.equal(view.calls.length, 2);
    view.clock.now += 30000;
    view.document.visibilityState = "hidden";
    view.timers[0].callback();
    assert.equal(view.calls.length, 2);
    view.document.visibilityState = "visible";
    view.document.fire("visibilitychange");
    await flush();
    assert.equal(view.calls.length, 3);
});
test("falha preserva contagem anterior, inclusive zero, sem expor erro privado", async () => {
    for (const initial of [counts(), counts(2)]) {
        const view = fixture([initial, new Error("ERRO_PRIVADO_FICTICIO")]);
        await flush();
        view.button.fire("click");
        await flush();
        assert.equal(view.count.textContent, String(initial.total));
        assert.equal(view.count.dataset.stale, "true");
        assert.match(view.status.textContent, /contagem anterior/);
        assert.doesNotMatch(view.text(), /ERRO_PRIVADO/);
    }
});
test("falha inicial não vira zero e botão permite nova tentativa", async () => {
    const view = fixture([new Error("ERRO_PRIVADO_FICTICIO"), counts(1)]);
    await flush();
    assert.equal(view.count.textContent, "?");
    view.button.fire("click");
    await flush();
    assert.equal(view.count.textContent, "1");
});
test("navegar após falha não apresenta a contagem antiga como confirmação recente", async () => {
    const first = fixture([counts(2), new Error("Falha fictícia")]);
    await flush();
    first.button.fire("click");
    await flush();
    let release;
    const next = fixture([() => new Promise((resolve) => { release = resolve; })], first);
    assert.equal(next.count.textContent, "2");
    assert.equal(next.count.dataset.stale, "true");
    assert.equal(next.calls.length, 1);
    release({ok: true, json: async () => counts(1)});
    await flush();
    assert.equal(next.count.dataset.stale, "false");
});
test("corpos HTTP de erro e resumos inválidos não são armazenados", async () => {
    for (const payload of [{httpFailure: true}, {invalidJSON: true}, null, [], {}, counts(-1), counts(1, 2), counts(1, 1, 2), {...counts(), total: "0"}, {...counts(), mensagem: "SALDO_PRIVADO_FICTICIO"}]) {
        const view = fixture([payload]);
        await flush();
        assert.equal(view.count.textContent, "?");
        assert.equal(view.session.getItem(cacheKey), null);
        assert.doesNotMatch(view.text(), /PRIVADO_FICTICIO/);
        if (payload?.httpFailure) assert.equal(view.jsonCalls, 0);
    }
});
test("armazenamento bloqueado não impede consultas nem atualizações", async () => {
    const blocked = {getItem() { throw new Error(); }, setItem() { throw new Error(); }, removeItem() { throw new Error(); }};
    const view = fixture([counts(1), counts(2)], {session: blocked, local: blocked});
    await flush();
    assert.equal(view.count.textContent, "1");
    view.window.estoqueNotificacoes.invalidar();
    await flush();
    assert.equal(view.count.textContent, "2");
});
test("cache corrompido, futuro, outra revisão ou outro processo não é reutilizado", async () => {
    for (const value of ["quebrado", JSON.stringify({at: Date.now() + 999999999999, counts: counts(9)}), JSON.stringify({at: 0, day: "2026-01-11", revision: "outra", counts: counts(9)})]) {
        const session = storage();
        session.setItem(cacheKey, value);
        const view = fixture([counts(1)], {session});
        await flush();
        assert.equal(view.count.textContent, "1");
        assert.equal(view.calls.length, 1);
    }
    const first = fixture([counts(1)]);
    await flush();
    const next = fixture([counts(2)], {...first, scope: "outro-processo"});
    await flush();
    assert.equal(next.calls.length, 1);
    assert.equal(next.count.textContent, "2");
});
test("componente ausente não inicia consulta nem timer", async () => {
    const view = fixture([], {mounted: false});
    await flush();
    assert.equal(view.calls.length, 0);
    assert.equal(view.timers.length, 0);
});
