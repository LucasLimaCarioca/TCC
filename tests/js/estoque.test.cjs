"use strict";

// DOM e rede mínimos; todos os itens, valores e respostas são fictícios.
const assert = require("node:assert/strict");
const {test} = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../app/static/js/estoque.js"), "utf8");

class Element {
    constructor(tag = "div", id = "") {
        this.tagName = tag;
        this.id = id;
        this.children = [];
        this.attributes = {};
        this.dataset = {};
        this.listeners = {};
        this.className = "";
        this.hidden = false;
        this.disabled = false;
        this.required = false;
        this.value = "";
        this.ownText = "";
        this.validationMessage = "";
        this.focused = false;
        this.validityReports = 0;
    }
    get textContent() { return this.ownText + this.children.map((child) => child.textContent).join(""); }
    set textContent(value) { this.ownText = String(value); this.children = []; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    append(...items) { this.children.push(...items); }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    dispatchEvent(event) {
        const handler = this.listeners[event.type];
        if (handler) handler(event);
        return true;
    }
    setCustomValidity(message) { this.validationMessage = message; }
    reportValidity() { this.validityReports += 1; return !this.validationMessage && (!this.required || this.value.trim() !== ""); }
    focus(options) { this.focused = true; this.focusOptions = options; }
    scrollIntoView(options) { this.scrollOptions = options; }
    querySelectorAll(selector) { return selector === 'button[type="submit"]' ? [this.submitButton] : []; }
}

class Storage {
    constructor(entries = []) {
        this.values = new Map(entries);
        this.writes = [];
        this.removals = [];
    }
    getItem(key) { return this.values.get(key) ?? null; }
    setItem(key, value) {
        this.writes.push([String(key), String(value)]);
        this.values.set(String(key), String(value));
    }
    removeItem(key) { this.removals.push(key); this.values.delete(key); }
}

function addControls(form, values) {
    const elements = Object.entries(values).map(([name, value]) => {
        const input = new Element("input");
        input.name = name;
        input.value = value;
        return input;
    });
    elements.forEach((input) => { elements[input.name] = input; });
    elements.namedItem = (name) => elements[name] || null;
    form.elements = elements;
    form.submitButton = new Element("button");
    form.submitButton.type = "submit";
    form.reportValidity = () => elements.every((input) => input.reportValidity());
}

function harness({storage = new Storage(), storageUnavailable = false, response = null} = {}) {
    const elements = new Map();
    const formFields = {
        "movimentacao-form": {item: "produto:42", tipo_movimentacao: "saida", quantidade: "2", motivo: "Cenário fictício"},
        "minimo-form": {item: "produto:42", estoque_minimo: "4"},
        "materia-form": {codigo: "MATERIA-FICTICIA", nome: "Matéria fictícia", unidade_medida: "kg", estoque_minimo: "0", quantidade_inicial: ""},
        "sincronizar-form": {},
        "risco-form": {produto_id: "42", horizonte_dias: "17"},
        "preventivo-form": {produto_id: "42"}
    };
    const feedbackIds = {
        "movimentacao-form": "movimentacao-feedback", "minimo-form": "minimo-feedback",
        "materia-form": "materia-feedback", "sincronizar-form": "sincronizar-feedback",
        "risco-form": "risco-feedback", "preventivo-form": "preventivo-feedback"
    };
    Object.entries(formFields).forEach(([id, values]) => {
        const form = new Element("form", id);
        addControls(form, values);
        elements.set(id, form);
        const output = new Element("p", feedbackIds[id]);
        output.hidden = true;
        output.setAttribute("role", "status");
        output.setAttribute("aria-live", "polite");
        output.setAttribute("aria-atomic", "true");
        output.setAttribute("tabindex", "-1");
        elements.set(output.id, output);
    });
    elements.set("quantidade-label", new Element("span", "quantidade-label"));
    const material = elements.get("materia-form");
    const minimum = material.elements.estoque_minimo;
    minimum.required = true;
    elements.set("materia-minimo", minimum);
    const initial = material.elements.quantidade_inicial;
    const steppers = new Map();
    for (const [id, input] of [["materia-form", minimum],
        ["movimentacao-form", elements.get("movimentacao-form").elements.quantidade],
        ["minimo-form", elements.get("minimo-form").elements.estoque_minimo]]) {
        input.required = true;
        const control = new Element();
        const minus = new Element("button");
        minus.dataset.step = "-1";
        const plus = new Element("button");
        plus.dataset.step = "1";
        control.querySelector = (selector) => selector === "input" ? input : null;
        control.querySelectorAll = (selector) => selector === "[data-step]" ? [minus, plus] : [];
        steppers.set(id, {control, input, minus, plus});
    }
    const {minus, plus} = steppers.get("materia-form");
    const calls = [];
    const requests = [];
    let reloads = 0;
    let identifiers = 0;
    const window = {location: {reload: () => { reloads += 1; }}};
    Object.defineProperty(window, "sessionStorage", {
        get() { if (storageUnavailable) throw new Error("Armazenamento indisponível na fixture."); return storage; }
    });
    class FormData {
        constructor(form) { this.items = form.elements.filter((input) => !input.disabled).map((input) => [input.name, input.value]); }
        [Symbol.iterator]() { return this.items[Symbol.iterator](); }
    }
    class Event {
        constructor(type, options = {}) { this.type = type; this.bubbles = options.bubbles; }
        preventDefault() {}
    }
    const context = {
        document: {
            getElementById: (id) => elements.get(id) || null,
            querySelectorAll: (selector) => selector === "[data-decimal]" ? [...steppers.values()].map((item) => item.input).concat(initial)
                : selector === "[data-decimal-stepper]" ? [...steppers.values()].map((item) => item.control) : [],
            createElement: (tag) => new Element(tag),
            createTextNode: (text) => { const node = new Element("#text"); node.textContent = text; return node; }
        },
        window, crypto: {randomUUID: () => "00000000-0000-4000-8000-" + String(++identifiers).padStart(12, "0")},
        FormData, Event, URLSearchParams,
        fetch: async (url, options) => {
            const request = {url: String(url), options};
            calls.push(request);
            const result = response ? await response(request, calls.length) : {status: 200, body: {}};
            requests.push(result);
            return {ok: result.status < 400, status: result.status, json: async () => result.body};
        }
    };
    vm.runInNewContext(source, context, {filename: "estoque.js"});
    return {
        form: (id) => elements.get(id), output: (id) => elements.get(feedbackIds[id]),
        get: (id) => elements.get(id), minimum, initial, minus, plus, steppers, calls, requests, storage,
        get reloads() { return reloads; },
        async submit(id) { await elements.get(id).listeners.submit(new Event("submit")); },
        setInput(input, value) { input.value = value; input.dispatchEvent(new Event("input", {bubbles: true})); },
        click(button) { button.listeners.click(new Event("click")); }
    };
}

test("HTTP 400 mostra erro próximo ao formulário, acessível, com foco e valores preservados", async () => {
    const ui = harness({response: async () => ({status: 400, body: {erro: "A quantidade solicitada excede o saldo disponível."}})});
    const form = ui.form("movimentacao-form");
    form.elements.quantidade.value = "99";
    form.elements.motivo.value = "Motivo fictício a preservar";
    const before = Object.fromEntries(form.elements.map((input) => [input.name, input.value]));
    await ui.submit(form.id);
    const output = ui.output(form.id);
    assert.equal(output.hidden, false);
    assert.equal(output.getAttribute("role"), "alert");
    assert.equal(output.getAttribute("aria-live"), "assertive");
    assert.equal(output.getAttribute("aria-atomic"), "true");
    assert.match(output.className, /stock-feedback-error/);
    assert.equal(output.focused, true);
    assert.equal(output.focusOptions.preventScroll, true);
    assert.equal(output.scrollOptions.block, "nearest");
    assert.equal(output.textContent, "A quantidade solicitada excede o saldo disponível.");
    assert.deepEqual(Object.fromEntries(form.elements.map((input) => [input.name, input.value])), before);
    assert.equal(form.submitButton.disabled, false);
    assert.equal(form.getAttribute("aria-busy"), "false");
    assert.equal(ui.reloads, 0);
    assert.deepEqual(ui.storage.writes, []);
    assert.equal(ui.output("minimo-form").hidden, true);
});

test("erro de rede conserva a mesma chave para repetir o mesmo conteúdo e muda a chave com os dados", async () => {
    const ui = harness({response: async () => { throw new Error("Erro fictício de transporte."); }});
    await ui.submit("movimentacao-form");
    assert.match(ui.output("movimentacao-form").textContent, /Não foi possível conectar/);
    await ui.submit("movimentacao-form");
    assert.equal(ui.calls[0].options.headers["Idempotency-Key"], ui.calls[1].options.headers["Idempotency-Key"]);
    assert.equal(ui.calls[0].options.body, ui.calls[1].options.body);
    ui.form("movimentacao-form").elements.quantidade.value = "3";
    await ui.submit("movimentacao-form");
    assert.notEqual(ui.calls[1].options.headers["Idempotency-Key"], ui.calls[2].options.headers["Idempotency-Key"]);
    assert.equal(ui.reloads, 0);
});

test("submissões simultâneas são bloqueadas enquanto uma requisição está pendente", async () => {
    let release;
    const ui = harness({response: async () => new Promise((resolve) => { release = resolve; })});
    const first = ui.submit("movimentacao-form");
    const form = ui.form("movimentacao-form");
    assert.equal(form.submitButton.disabled, true);
    assert.equal(form.dataset.pending, "true");
    assert.equal(form.getAttribute("aria-busy"), "true");
    const second = ui.submit("movimentacao-form");
    await second;
    assert.equal(ui.calls.length, 1);
    release({status: 400, body: {erro: "Rejeição fictícia."}});
    await first;
    assert.equal(form.submitButton.disabled, false);
    assert.equal(form.dataset.pending, "false");
});

test("sucesso guarda somente o ID fixo do formulário e consome o aviso após recarregar", async () => {
    const storage = new Storage();
    const first = harness({storage});
    first.form("movimentacao-form").elements.quantidade.value = "123";
    first.form("movimentacao-form").elements.motivo.value = "Texto fictício que não deve ser guardado";
    await first.submit("movimentacao-form");
    assert.equal(first.reloads, 1);
    assert.deepEqual(storage.writes, [["estoque.sucesso", "movimentacao-form"]]);
    assert.deepEqual([...storage.values], [["estoque.sucesso", "movimentacao-form"]]);
    assert.doesNotMatch(JSON.stringify(storage.writes), /123|Texto fictício|saldo/);
    const reloaded = harness({storage});
    const output = reloaded.output("movimentacao-form");
    assert.equal(output.hidden, false);
    assert.equal(output.getAttribute("role"), "status");
    assert.equal(output.getAttribute("aria-live"), "polite");
    assert.equal(output.textContent, "Movimentação registrada com sucesso.");
    assert.equal(output.focused, true);
    assert.equal(storage.values.size, 0);
    assert.equal(reloaded.form("movimentacao-form").elements.quantidade.value, "2");
    const nextReload = harness({storage});
    assert.equal(nextReload.output("movimentacao-form").hidden, true);
});

test("um identificador desconhecido no armazenamento não cria mensagem nem conteúdo arbitrário", () => {
    const storage = new Storage([["estoque.sucesso", "<script>texto fictício</script>"]]);
    const ui = harness({storage});
    assert.ok(["movimentacao-form", "minimo-form", "materia-form", "preventivo-form"]
        .every((id) => ui.output(id).hidden));
    assert.equal(storage.values.size, 0);
    assert.equal(ui.calls.length, 0);
});

test("sem sessionStorage, sucesso fica visível com link para atualizar os dados", async () => {
    const ui = harness({storageUnavailable: true});
    await ui.submit("movimentacao-form");
    const output = ui.output("movimentacao-form");
    assert.equal(output.hidden, false);
    assert.equal(output.getAttribute("role"), "status");
    assert.match(output.className, /stock-feedback-success/);
    assert.match(output.textContent, /Movimentação registrada com sucesso/);
    const link = output.children.find((child) => child.tagName === "a");
    assert.equal(link.href, "/estoque#movimentacao-form");
    assert.equal(link.textContent, "Atualizar dados da tela");
    assert.equal(ui.reloads, 0);
});

test("stepper altera uma unidade sem perder nove casas decimais", () => {
    const ui = harness();
    ui.setInput(ui.minimum, "1.123456789");
    ui.click(ui.plus);
    assert.equal(ui.minimum.value, "2.123456789");
    ui.click(ui.minus);
    assert.equal(ui.minimum.value, "1.123456789");
    ui.setInput(ui.minimum, "999999999999999.123456789");
    ui.click(ui.plus);
    assert.equal(ui.minimum.value, "1000000000000000.123456789");
    ui.click(ui.minus);
    assert.equal(ui.minimum.value, "999999999999999.123456789");
});

test("stepper aceita entrada manual com vírgula e limita a redução a zero", () => {
    const ui = harness();
    ui.setInput(ui.minimum, " 0,123456789 ");
    assert.equal(ui.minimum.validationMessage, "");
    ui.click(ui.plus);
    assert.equal(ui.minimum.value, "1.123456789");
    ui.click(ui.minus);
    assert.equal(ui.minimum.value, "0.123456789");
    ui.click(ui.minus);
    assert.equal(ui.minimum.value, "0");
    ui.click(ui.minus);
    assert.equal(ui.minimum.value, "0");
});

test("entradas inválidas não são alteradas pelo stepper e bloqueiam o cadastro", async () => {
    const ui = harness();
    for (const value of ["-1", "1.1234567890", "1,2,3", "1e2", "texto fictício", ""]) {
        ui.setInput(ui.minimum, value);
        const reportsBefore = ui.minimum.validityReports;
        ui.click(ui.plus);
        assert.equal(ui.minimum.value, value);
        assert.notEqual(ui.minimum.validationMessage, "");
        assert.ok(ui.minimum.validityReports > reportsBefore);
        await ui.submit("materia-form");
        assert.equal(ui.calls.length, 0);
    }
});

test("cadastro normaliza mínimo e quantidade inicial como strings exatas na unidade cadastrada", async () => {
    const ui = harness({response: async () => ({status: 400, body: {erro: "Falha fictícia para preservar o formulário."}})});
    ui.setInput(ui.minimum, " 1,123456789 ");
    ui.setInput(ui.initial, " 2,000000001 ");
    await ui.submit("materia-form");
    const request = ui.calls[0];
    assert.equal(request.url, "/api/estoque/materias-primas");
    assert.equal(request.options.method, "POST");
    const body = JSON.parse(request.options.body);
    assert.equal(body.estoque_minimo, "1.123456789");
    assert.equal(body.quantidade_inicial, "2.000000001");
    assert.equal(body.unidade_medida, "kg");
    assert.equal(ui.minimum.value, " 1,123456789 ");
    assert.equal(ui.initial.value, " 2,000000001 ");
    assert.equal(ui.output("materia-form").getAttribute("role"), "alert");
});

test("quantidade inicial vazia é omitida e zero explícito continua sendo uma string zero", async () => {
    const blank = harness();
    blank.setInput(blank.initial, " ");
    await blank.submit("materia-form");
    assert.equal(Object.hasOwn(JSON.parse(blank.calls[0].options.body), "quantidade_inicial"), false);
    const zero = harness();
    zero.setInput(zero.initial, "0,000000000");
    await zero.submit("materia-form");
    assert.equal(JSON.parse(zero.calls[0].options.body).quantidade_inicial, "0");
});

test("movimentações de matéria-prima enviam decimais como texto", async () => {
    const ui = harness();
    const form = ui.form("movimentacao-form");
    form.elements.item.value = "materia_prima:7";
    form.elements.quantidade.value = "1.123456789";
    form.listeners.change();
    assert.equal(form.elements.quantidade.step, "1");
    await ui.submit(form.id);
    const body = JSON.parse(ui.calls[0].options.body);
    assert.equal(body.tipo_item, "materia_prima");
    assert.equal(body.item_id, 7);
    assert.equal(body.quantidade, "1.123456789");
});

test("alerta persistente envia POST vazio e ignora horizonte adulterado no formulário", async () => {
    const ui = harness({response: async () => ({status: 200, body: {risco: true}})});
    const form = ui.form("preventivo-form");
    form.elements.horizonte_dias = {value: "1"};
    await ui.submit(form.id);
    const request = ui.calls[0];
    assert.equal(request.url, "/api/estoque/alertas/preventivos/42");
    assert.equal(request.options.method, "POST");
    assert.deepEqual(JSON.parse(request.options.body), {});
    assert.equal(ui.reloads, 1);
    assert.deepEqual(ui.storage.writes, [["estoque.sucesso", "preventivo-form"]]);
});

test("consulta de risco preserva horizonte personalizado sem disparar atualização persistente", async () => {
    const ui = harness({response: async () => ({status: 200, body: {risco: false}})});
    await ui.submit("risco-form");
    const request = ui.calls[0];
    assert.equal(request.url, "/api/estoque/risco/42?horizonte_dias=17");
    assert.equal(request.options.method, "GET");
    assert.equal(request.options.body, undefined);
    assert.equal(ui.reloads, 0);
    assert.deepEqual(ui.storage.writes, []);
    assert.match(ui.output("risco-form").textContent, /atende ao estoque mínimo/);
    assert.equal(ui.output("risco-form").getAttribute("role"), "status");
});

test("risco preventivo desconhecido mantém aviso local sem sinalizar sucesso persistente", async () => {
    const ui = harness({response: async () => ({status: 200, body: {risco: null,
        mensagem: "Previsão fictícia indisponível. Os alertas anteriores foram preservados."}})});
    await ui.submit("preventivo-form");
    const output = ui.output("preventivo-form");
    assert.match(output.className, /stock-feedback-warning/);
    assert.match(output.textContent, /alertas anteriores foram preservados/);
    assert.equal(output.getAttribute("role"), "status");
    assert.equal(ui.reloads, 0);
    assert.deepEqual(ui.storage.writes, []);
});

for (const [id, field] of [["movimentacao-form", "quantidade"], ["minimo-form", "estoque_minimo"]]) {
    test(id + ": passo de uma unidade mantém frações e normaliza vírgula na requisição", async () => {
        const ui = harness();
        const form = ui.form(id);
        form.elements.item.value = "materia_prima:7";
        form.listeners.change();
        const stepper = ui.steppers.get(id);
        assert.equal(stepper.input.step, "1");
        assert.equal(stepper.input.inputMode, "decimal");
        ui.setInput(stepper.input, "1,500000001");
        ui.click(stepper.plus);
        assert.equal(stepper.input.value, "2.500000001");
        ui.click(stepper.minus);
        assert.equal(stepper.input.value, "1.500000001");
        ui.setInput(stepper.input, "10,75");
        ui.click(stepper.plus);
        assert.equal(stepper.input.value, "11.75");
        ui.click(stepper.minus);
        assert.equal(stepper.input.value, "10.75");
        ui.setInput(stepper.input, "0,5");
        ui.click(stepper.minus);
        assert.equal(stepper.input.value, "0");
        ui.click(stepper.minus);
        assert.equal(stepper.input.value, "0");
        ui.setInput(stepper.input, "1,123456789");
        await ui.submit(id);
        assert.equal(JSON.parse(ui.calls[0].options.body)[field], "1.123456789");
        assert.equal(stepper.input.value, "1,123456789");
    });

    test(id + ": produtos aceitam somente inteiros exatos e troca de item revalida o campo", async () => {
        const ui = harness();
        const form = ui.form(id);
        const stepper = ui.steppers.get(id);
        assert.equal(stepper.input.inputMode, "numeric");
        for (const invalid of ["1.5", "1,5", "-1", "9007199254740992"]) {
            ui.setInput(stepper.input, invalid);
            ui.click(stepper.plus);
            assert.equal(stepper.input.value, invalid);
            assert.notEqual(stepper.input.validationMessage, "");
            await ui.submit(id);
        }
        assert.equal(ui.calls.length, 0);
        ui.setInput(stepper.input, "1");
        ui.click(stepper.plus);
        assert.equal(stepper.input.value, "2");
        ui.click(stepper.minus);
        assert.equal(stepper.input.value, "1");
        await ui.submit(id);
        assert.equal(JSON.parse(ui.calls[0].options.body)[field], 1);
        form.elements.item.value = "materia_prima:7";
        form.listeners.change();
        ui.setInput(stepper.input, "1.5");
        assert.equal(stepper.input.validationMessage, "");
        form.elements.item.value = "produto:42";
        form.listeners.change();
        assert.notEqual(stepper.input.validationMessage, "");
    });
}

test("stepper de produto não atravessa o limite de serialização inteira exata", () => {
    const ui = harness();
    const {input, plus, minus} = ui.steppers.get("movimentacao-form");
    ui.setInput(input, "9007199254740991");
    ui.click(plus);
    assert.equal(input.value, "9007199254740991");
    ui.click(minus);
    assert.equal(input.value, "9007199254740990");
});

