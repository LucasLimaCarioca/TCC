"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../app/static/js/notificacoes_estoque.js"), "utf8");

class Element {
    constructor() {
        this.dataset = {};
        this.children = [];
        this.attributes = new Map();
        this.events = new Map();
        this._text = "";
        this.disabled = false;
        this.open = false;
        this.title = "";
    }
    addEventListener(type, handler) {
        const handlers = this.events.get(type) || [];
        handlers.push(handler);
        this.events.set(type, handlers);
    }
    fire(type) {
        for (const handler of this.events.get(type) || []) handler({target: this});
    }
    set textContent(value) {
        this._text = String(value);
        this.children = [];
    }
    get textContent() {
        return this._text + this.children.map((child) => child.textContent).join("");
    }
    replaceChildren(...children) {
        this._text = "";
        this.children = children;
    }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) || null; }
    querySelector(selector) { return selector === "summary" ? this.summary : null; }
}

function fixture(responses, {mounted = true} = {}) {
    const elements = Object.fromEntries([
        "estoque-notificacoes", "notificacoes-contagem", "notificacoes-status",
        "notificacoes-resumo", "notificacoes-atualizar"
    ].map((id) => [id, new Element()]));
    const panel = elements["estoque-notificacoes"];
    panel.dataset.api = "/api/estoque/alertas";
    panel.summary = new Element();
    const document = new Element();
    document.visibilityState = "visible";
    document.getElementById = (id) => !mounted ? null : elements[id] || null;
    document.createElement = () => new Element();
    const window = new Element();
    const queue = [...responses];
    const calls = [];
    const clock = {now: 0};
    let jsonCalls = 0;
    class FakeDate extends Date { static now() { return clock.now; } }
    const fetch = (url, options) => {
        calls.push({url, options});
        const result = queue.shift();
        if (result instanceof Error) return Promise.reject(result);
        if (typeof result === "function") return result();
        return Promise.resolve({
            ok: !result?.httpFailure,
            json: () => {
                jsonCalls += 1;
                if (result?.invalidJSON) return Promise.reject(new SyntaxError("RESPOSTA_FICTICIA_PRIVADA"));
                return Promise.resolve(result);
            }
        });
    };
    vm.runInNewContext(source, {document, window, fetch, Date: FakeDate});
    return {
        panel, document, window, clock, calls, elements,
        count: elements["notificacoes-contagem"],
        status: elements["notificacoes-status"],
        list: elements["notificacoes-resumo"],
        button: elements["notificacoes-atualizar"],
        get jsonCalls() { return jsonCalls; },
        text() {
            return [...Object.values(elements), panel.summary].map((element) =>
                element.textContent + " " + element.title + " " + [...element.attributes.values()].join(" ")
            ).join(" ");
        }
    };
}

function alert(tipo_alerta, ativo = true, avaliacao) {
    return {
        tipo_alerta, ativo, avaliacao,
        mensagem: "<img src=x onerror=alert('SALDO_FICTICIO_98765')>",
        quantidade_disponivel: 98765,
        nome: "IDENTIFICADOR_FICTICIO_PRIVADO"
    };
}

async function flush() {
    await new Promise((resolve) => setImmediate(resolve));
}

test("conta somente ativos, distingue tipos e nunca exibe mensagem ou saldo", async () => {
    const view = fixture([[
        alert("estoque_minimo"),
        alert("risco_futuro", true, {atual: true}),
        alert("risco_futuro", true, {atual: false, motivo: "saldo_alterado"}),
        alert("estoque_minimo", false),
        alert("risco_futuro", false)
    ]]);
    await flush();
    assert.equal(view.count.textContent, "3");
    assert.equal(view.list.children[0].textContent, "Alertas imediatos: 1.");
    assert.equal(view.list.children[1].textContent, "Alertas preventivos: 2.");
    assert.equal(view.list.children[2].textContent, "Preventivos com avaliação pendente ou desatualizada: 1.");
    assert.equal(view.count.dataset.stale, "false");
    assert.equal(view.panel.dataset.state, "ready");
    assert.match(view.panel.summary.getAttribute("aria-label"), /3 alertas ativos/);
    assert.doesNotMatch(view.text(), /SALDO_FICTICIO|98765|IDENTIFICADOR_FICTICIO|<img/);
    assert.equal(view.calls.length, 1);
    assert.equal(view.calls[0].url, "/api/estoque/alertas");
    assert.equal(view.calls[0].options.method, "GET");
    assert.equal(view.calls[0].options.cache, "no-store");
    assert.equal(view.calls[0].options.credentials, "same-origin");
    assert.equal(view.calls[0].options.body, undefined);
});

test("preventivos sem avaliação atual válida são pendentes, sem expor o motivo bruto", async () => {
    const view = fixture([[
        alert("risco_futuro"),
        alert("risco_futuro", true, {atual: "true", motivo: "MOTIVO_FICTICIO_PRIVADO"}),
        alert("risco_futuro", true, {atual: true}),
        alert("TIPO_FICTICIO_PRIVADO")
    ]]);
    await flush();
    assert.equal(view.count.textContent, "4");
    assert.match(view.list.children[0].textContent, /: 1\./);
    assert.match(view.list.children[1].textContent, /: 3\./);
    assert.match(view.list.children[2].textContent, /: 2\./);
    assert.doesNotMatch(view.text(), /MOTIVO_FICTICIO|TIPO_FICTICIO/);
});

test("lista vazia e alertas inativos representam zero confirmado", async () => {
    for (const payload of [[], [alert("estoque_minimo", false), alert("risco_futuro", false)]]) {
        const view = fixture([payload]);
        await flush();
        assert.equal(view.count.textContent, "0");
        assert.equal(view.count.dataset.stale, "false");
        assert.equal(view.status.textContent, "Não há alertas ativos.");
        assert.equal(view.panel.dataset.state, "ready");
    }
});

test("erro inicial não aparece como zero e permite tentar novamente", async () => {
    const view = fixture([new Error("ERRO_FICTICIO_SALDO_98765"), [alert("estoque_minimo")]]);
    await flush();
    assert.equal(view.count.textContent, "?");
    assert.equal(view.panel.dataset.state, "error");
    assert.equal(view.count.dataset.stale, "true");
    assert.equal(view.button.disabled, false);
    assert.match(view.status.textContent, /Não foi possível consultar/);
    assert.match(view.panel.summary.getAttribute("aria-label"), /indisponível/);
    assert.doesNotMatch(view.text(), /ERRO_FICTICIO|98765/);
    view.button.fire("click");
    await flush();
    assert.equal(view.calls.length, 2);
    assert.equal(view.count.textContent, "1");
    assert.equal(view.panel.dataset.state, "ready");
    assert.equal(view.count.dataset.stale, "false");
});

test("falha posterior preserva contagem anterior, inclusive zero", async () => {
    for (const initial of [[], [alert("estoque_minimo"), alert("risco_futuro")]]) {
        const view = fixture([initial, new Error("ERRO_FICTICIO_PRIVADO")]);
        await flush();
        const previous = String(initial.length);
        const previousList = view.list.textContent;
        view.button.fire("click");
        await flush();
        assert.equal(view.count.textContent, previous);
        assert.equal(view.list.textContent, previousList);
        assert.equal(view.count.dataset.stale, "true");
        assert.match(view.status.textContent, new RegExp("contagem anterior: " + previous));
        assert.match(view.panel.summary.getAttribute("aria-label"), /Atualização não confirmada/);
        assert.doesNotMatch(view.text(), /ERRO_FICTICIO/);
    }
});

test("erro HTTP não renderiza nem interpreta o corpo de erro", async () => {
    const view = fixture([{httpFailure: true, erro: "Saldo privado 98765."}]);
    await flush();
    assert.equal(view.count.textContent, "?");
    assert.equal(view.jsonCalls, 0);
    assert.doesNotMatch(view.text(), /98765|Saldo privado/);
});

test("resposta JSON malformada ou fora do contrato não vira ausência de alertas", async () => {
    for (const payload of [
        {invalidJSON: true}, null, {}, "texto",
        [null], [{}], [{ativo: "true", tipo_alerta: "estoque_minimo"}],
        [{ativo: true}], [{ativo: true, tipo_alerta: ""}]
    ]) {
        const view = fixture([payload]);
        await flush();
        assert.equal(view.count.textContent, "?");
        assert.equal(view.panel.dataset.state, "error");
        assert.equal(view.status.textContent.includes("Não há alertas"), false);
    }
});

test("abrir detalhes e botão atualizam por ação explícita sem depender do cooldown", async () => {
    const view = fixture([[], [alert("estoque_minimo")], []]);
    await flush();
    view.panel.open = false;
    view.panel.fire("toggle");
    await flush();
    assert.equal(view.calls.length, 1);
    view.panel.open = true;
    view.panel.fire("toggle");
    await flush();
    assert.equal(view.calls.length, 2);
    assert.equal(view.count.textContent, "1");
    view.button.fire("click");
    await flush();
    assert.equal(view.calls.length, 3);
    assert.equal(view.count.textContent, "0");
    assert.ok(view.calls.every(({options}) => options.method === "GET" && options.body === undefined));
});

test("foco e visibilidade visível atualizam sem polling ou requisições agressivas", async () => {
    const view = fixture([[], [], []]);
    await flush();
    view.window.fire("focus");
    view.document.fire("visibilitychange");
    await flush();
    assert.equal(view.calls.length, 1);
    view.clock.now += 30000;
    view.window.fire("focus");
    await flush();
    assert.equal(view.calls.length, 2);
    view.document.fire("visibilitychange");
    await flush();
    assert.equal(view.calls.length, 2);
    view.clock.now += 30000;
    view.document.visibilityState = "hidden";
    view.window.fire("focus");
    view.document.fire("visibilitychange");
    await flush();
    assert.equal(view.calls.length, 2);
    view.document.visibilityState = "visible";
    view.document.fire("visibilitychange");
    await flush();
    assert.equal(view.calls.length, 3);
});

test("requisições simultâneas são agrupadas e botão fica ocupado durante a consulta", async () => {
    let release;
    const view = fixture([() => new Promise((resolve) => { release = resolve; })]);
    assert.equal(view.button.disabled, true);
    assert.equal(view.panel.getAttribute("aria-busy"), "true");
    view.panel.open = true;
    view.panel.fire("toggle");
    view.button.fire("click");
    view.clock.now += 30000;
    view.window.fire("focus");
    view.document.fire("visibilitychange");
    assert.equal(view.calls.length, 1);
    release({ok: true, json: async () => [alert("estoque_minimo")]});
    await flush();
    assert.equal(view.count.textContent, "1");
    assert.equal(view.button.disabled, false);
    assert.equal(view.panel.getAttribute("aria-busy"), "false");
});

test("componente ausente não inicia consulta", async () => {
    const view = fixture([], {mounted: false});
    await flush();
    assert.equal(view.calls.length, 0);
});
