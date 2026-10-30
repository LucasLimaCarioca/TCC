"use strict";

// Executa o script real com DOM/rede mínimos e dados inteiramente fictícios.
// Os testes verificam a apresentação resultante sem publicar funções internas.
const assert = require("node:assert/strict");
const {test} = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../app/static/js/previsoes.js"), "utf8");

class Element {
    constructor(tag = "div") {
        this.tagName = tag;
        this.children = [];
        this.attributes = {};
        this.dataset = {};
        this.style = {};
        this.listeners = {};
        this.hidden = false;
        this.disabled = false;
        this.value = "";
        this.ownText = "";
        this.clientWidth = 0;
        this.validationMessage = "";
    }
    get textContent() { return this.ownText + this.children.map((child) => child.textContent).join(""); }
    set textContent(value) { this.ownText = String(value); this.children = []; }
    append(...items) {
        items.forEach((item) => {
            if (item.tagName === "#fragment") this.append(...item.children);
            else { item.parentElement = this; this.children.push(item); }
        });
    }
    replaceChildren(...items) { this.ownText = ""; this.children = []; this.append(...items); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    addEventListener(name, listener) { this.listeners[name] = listener; }
    setCustomValidity(value) { this.validationMessage = value; }
    reportValidity() { return !this.validationMessage; }
}

function controls(values) {
    const collection = Object.entries(values).map(([name, value]) => {
        const element = new Element("input");
        element.value = value;
        element.initialValue = value;
        element.name = name;
        return element;
    });
    collection.forEach((element) => { collection[element.name] = element; });
    return collection;
}

function deepFreeze(value) {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
        Object.values(value).forEach(deepFreeze);
        Object.freeze(value);
    }
    return value;
}

function preciseSum(values) {
    let sum = 0n;
    values.forEach((value) => {
        const [integer, fraction = ""] = value.split(".");
        sum += BigInt(integer) * 1000000000n + BigInt(fraction.padEnd(9, "0"));
    });
    const integer = sum / 1000000000n;
    const fraction = (sum % 1000000000n).toString().padStart(9, "0").replace(/0+$/, "");
    return integer.toString() + (fraction ? "." + fraction : "");
}

function isoDay(first, offset) {
    const value = new Date(first + "T00:00:00Z");
    value.setUTCDate(value.getUTCDate() + offset);
    return value.toISOString().slice(0, 10);
}

function payload(values = ["1.4", "2.5"], options = {}) {
    const first = options.first || "2030-01-01";
    const last = isoDay(first, values.length - 1);
    const metrics = {mae: 0.125, rmse: 0.456, wape: 0.5, smape: 2.75};
    const products = [
        {codigo_produto: "00042", produto_id: 42, nome: "Produto Fictício", categoria: "Grupo fictício", codigo_grupo: "00005"},
        {codigo_produto: "00043", produto_id: null, nome: null, categoria: "Formato fictício", codigo_grupo: "00006"}
    ];
    return {
        execucao_id: "previsao-" + "a".repeat(32), versao: "forecast-v1", produtos: products,
        selecao: {codigo_produto: null, granularidade: options.frequency || "diaria", data_inicio: first, data_fim: last},
        cobertura: {inicio: first, fim: last, data_corte: "2029-12-31", gerada_em: "2030-01-01T12:00:00+00:00", desatualizada: false},
        total_previsto: preciseSum(values),
        periodos: values.map((quantity, index) => {
            const day = isoDay(first, index);
            return {periodo_inicio: day, periodo_fim: day, cobertura_inicio: day, cobertura_fim: day,
                parcial: false, quantidade_prevista: quantity};
        }),
        modelos: {naive: 2},
        avaliacoes: products.map((product) => ({
            codigo_produto: product.codigo_produto, modelo: "naive", fallback: false, motivo: "Seleção fictícia",
            treino_inicio: "2029-01-01", treino_fim: "2029-12-31", validacao: {...metrics},
            teste_inicio: "2029-12-18", teste_fim: "2029-12-31", teste: {...metrics},
            baselines: {naive: {...metrics}, seasonal_naive: {...metrics}}, melhor_que_baselines: false
        })),
        atualizacao: {disponivel: true, data_corte_maxima: "2029-12-31", data_corte_sugerida: "2029-12-31", horizonte_dias: values.length}
    };
}

function descendants(element, tag = null) {
    return element.children.flatMap((child) =>
        [...(tag === null || child.tagName === tag ? [child] : []), ...descendants(child, tag)]);
}

function ticks(ui) {
    return descendants(ui.get("previsao-grafico"), "text")
        .filter((item) => (item.getAttribute("class") || "").split(" ").includes("forecast-chart-x-label"));
}

function valueLabels(ui) {
    return descendants(ui.get("previsao-grafico"), "text")
        .filter((item) => (item.getAttribute("class") || "").split(" ").includes("forecast-chart-value-label"));
}

async function settle() {
    await new Promise(setImmediate);
    await new Promise(setImmediate);
}

async function harness(value, {width = 900, response = null} = {}) {
    const elements = new Map();
    const get = (id) => {
        if (!elements.has(id)) elements.set(id, new Element());
        return elements.get(id);
    };
    get("previsoes-app").dataset = {api: "/api/previsoes", updateApi: "/api/previsoes/atualizar", jobApi: "/api/previsoes/tarefas/"};
    const filters = get("previsao-filtros");
    filters.elements = controls({codigo_produto: "", granularidade: "diaria", data_inicio: "", data_fim: ""});
    filters.reportValidity = () => filters.elements.every((input) => input.reportValidity());
    filters.reset = () => filters.elements.forEach((input) => { input.value = input.initialValue; });
    const update = get("previsao-atualizar");
    update.elements = controls({data_corte: "", horizonte_dias: "28"});
    update.reportValidity = () => update.elements.every((input) => input.reportValidity());
    const wrapper = new Element();
    wrapper.clientWidth = width;
    get("previsao-grafico").parentElement = wrapper;
    const calls = [];
    const windowEvents = {};
    const timers = new Map();
    let timerId = 0;
    const context = {
        document: {getElementById: get, createElement: (tag) => new Element(tag),
            createElementNS: (_, tag) => new Element(tag), createDocumentFragment: () => new Element("#fragment")},
        window: {location: {href: "http://localhost/previsoes"},
            addEventListener: (name, fn) => { windowEvents[name] = fn; },
            setTimeout: (fn) => { timers.set(++timerId, fn); return timerId; },
            clearTimeout: (id) => timers.delete(id)},
        navigator: {onLine: true}, URL, AbortController, Intl,
        fetch: async (url, options) => {
            calls.push({url: String(url), options});
            const result = response ? await response(new URL(url, "http://localhost/previsoes"), options, calls.length) : {status: 200, body: value};
            return {ok: result.status < 400, status: result.status, json: async () => result.body};
        }
    };
    vm.runInNewContext(source, context, {filename: "previsoes.js"});
    await settle();
    return {get, calls, wrapper, windowEvents, timers, settle,
        async fire(id, event) {
            await get(id).listeners[event]({preventDefault() {}});
            await settle();
        }};
}

test("HALF_UP por período conserva gráfico, tabela e total com frações de nove casas", async () => {
    const data = deepFreeze(payload(["0.4", "0.5", "1.499999999", "1.5", "2.500000001", "0", "10.999999999"]));
    const before = JSON.stringify(data);
    const ui = await harness(data);
    const rows = ui.get("previsao-periodos").children;
    assert.deepEqual(rows.map((row) => row.children[2].textContent), ["0", "1", "1", "2", "3", "0", "11"]);
    assert.equal(ui.get("previsao-total").textContent, "18"); // O total bruto seria arredondado para 17.
    const titles = descendants(ui.get("previsao-grafico"), "circle")
        .map((point) => point.children.find((item) => item.tagName === "title").textContent);
    assert.match(titles[1], /: 1 unidades estimadas/);
    assert.match(titles[3], /: 2 unidades estimadas/);
    assert.equal(JSON.stringify(data), before);
    assert.equal(ui.get("previsao-erro").hidden, true);
});

test("quantidades grandes permanecem exatas além da precisão de Number", async () => {
    const ui = await harness(payload(["12345678901234567890.499999999", "0.5"]));
    assert.equal(ui.get("previsao-periodos").children[0].children[2].textContent, "12.345.678.901.234.567.890");
    assert.equal(ui.get("previsao-total").textContent, "12.345.678.901.234.567.891");
    assert.match(descendants(ui.get("previsao-grafico"), "title")[0].textContent, /12\.345\.678\.901\.234\.567\.890 unidades/);
});

test("gráfico e tabela mantêm zero e inteiro sem casas decimais", async () => {
    const ui = await harness(payload(["0", "1", "2.000000001"]));
    assert.deepEqual(ui.get("previsao-periodos").children.map((row) => row.children[2].textContent), ["0", "1", "2"]);
    assert.equal(ui.get("previsao-total").textContent, "3");
    const axis = descendants(ui.get("previsao-grafico"), "text")
        .filter((item) => item.getAttribute("text-anchor") === "end" && !ticks(ui).includes(item));
    assert.ok(axis.every((item) => !item.ownText.includes(",")));
});

test("quantidade inválida permanece indisponível e não cria um total zero saudável", async () => {
    const data = payload(["1"]);
    data.periodos[0].quantidade_prevista = "inválida";
    const ui = await harness(data);
    assert.equal(ui.get("previsao-total").textContent, "Indisponível");
    assert.equal(ui.get("previsao-periodos").children[0].children[2].textContent, "Indisponível");
    assert.match(ui.get("previsao-grafico").textContent, /Consulte os valores na tabela/);
});

test("MAE, RMSE, WAPE e sMAPE preservam a precisão exibida das métricas", async () => {
    const data = deepFreeze(payload(["1.5"]));
    const before = JSON.stringify(data.avaliacoes);
    const ui = await harness(data);
    const cells = ui.get("previsao-metricas").children[0].children.map((cell) => cell.textContent);
    assert.equal(cells[2], "0,125");
    assert.equal(cells[3], "0,125");
    assert.equal(cells[4], "0,456");
    assert.equal(cells[5], "0,5%");
    assert.equal(cells[6], "2,75%");
    assert.equal(JSON.stringify(data.avaliacoes), before);
});

test("a apresentação explicita a diferença entre arredondar dias e um período agregado", async () => {
    const daily = await harness(payload(["0.4", "0.4"]));
    const weeklyData = payload(["0.8"], {frequency: "semanal"});
    weeklyData.selecao.data_fim = weeklyData.cobertura.fim = "2030-01-02";
    Object.assign(weeklyData.periodos[0], {periodo_inicio: "2029-12-31", periodo_fim: "2030-01-06",
        cobertura_fim: "2030-01-02", parcial: true});
    const weekly = await harness(weeklyData);
    assert.equal(daily.get("previsao-total").textContent, "0");
    assert.equal(weekly.get("previsao-total").textContent, "1");
    assert.equal(weekly.get("previsao-periodos").children[0].children[2].textContent, "1");
    assert.match(descendants(weekly.get("previsao-grafico"), "circle")[0].textContent, /: 1 unidades/);
});

test("todas as sete datas diárias aparecem com espaçamento e rótulo acessível completo", async () => {
    const ui = await harness(payload(["1", "2", "3", "4", "5", "6", "7"]));
    const labels = ticks(ui);
    assert.deepEqual(labels.map((item) => item.ownText), ["01/01", "02/01", "03/01", "04/01", "05/01", "06/01", "07/01"]);
    assert.deepEqual(labels.map((item) => item.getAttribute("aria-label")), [
        "01/01/2030", "02/01/2030", "03/01/2030", "04/01/2030", "05/01/2030", "06/01/2030", "07/01/2030"
    ]);
    const positions = labels.map((item) => Number(item.getAttribute("x")));
    assert.ok(positions.slice(1).every((value, index) => value - positions[index] >= 76));
    assert.equal(labels[0].getAttribute("text-anchor"), "start");
    assert.equal(labels.at(-1).getAttribute("text-anchor"), "end");
    assert.equal(ui.get("previsao-periodos").children.length, 7);
});

test("sete dias em tela estreita não sobrepõem datas e mantêm área horizontal navegável", async () => {
    const ui = await harness(payload(Array(7).fill("1")), {width: 320});
    assert.equal(ticks(ui).length, 7);
    assert.ok(parseInt(ui.get("previsao-grafico").style.minWidth, 10) > 320);
    assert.equal(ui.get("previsao-grafico").getAttribute("viewBox"), "0 0 571 290");
});

test("sete dias mostram sete valores inteiros iguais à tabela, inclusive zero e empates", async () => {
    const data = deepFreeze(payload(["0", "0.4", "0.5", "1.5", "1.5", "10.75", "10.75"]));
    const before = JSON.stringify(data);
    const ui = await harness(data, {width: 320});
    const labels = valueLabels(ui);
    const rows = ui.get("previsao-periodos").children;
    assert.equal(ticks(ui).length, 7);
    assert.equal(labels.length, 7);
    assert.deepEqual(labels.map((item) => item.ownText), rows.map((row) => row.children[2].textContent));
    assert.deepEqual(labels.map((item) => item.ownText), ["0", "0", "1", "2", "2", "11", "11"]);
    const dateY = Number(ticks(ui)[0].getAttribute("y"));
    assert.ok(labels.every((item) => Number(item.getAttribute("y")) <= dateY - 30));
    assert.equal(JSON.stringify(data), before);
});

test("série nula mantém valores visíveis acima do eixo e pontos focalizáveis", async () => {
    const ui = await harness(payload(Array(7).fill("0")), {width: 320});
    const points = descendants(ui.get("previsao-grafico"), "circle");
    assert.equal(points.length, 7);
    assert.equal(valueLabels(ui).length, 7);
    assert.ok(valueLabels(ui).every((label) => label.ownText === "0" && Number(label.getAttribute("y")) > 0));
    assert.ok(points.every((point) => point.getAttribute("tabindex") === "0" && point.getAttribute("aria-label").includes(": 0 unidades")));
    points[3].listeners.focus();
    assert.match(ui.get("previsao-ponto-detalhe").textContent, /04\/01\/2030: 0 unidades/);
    points[3].listeners.blur();
    assert.match(ui.get("previsao-ponto-detalhe").textContent, /Tab/);
    points[4].listeners.click(); // Toque mantém o valor consultado após retirar o dedo.
    points[4].listeners.pointerleave();
    assert.match(ui.get("previsao-ponto-detalhe").textContent, /05\/01\/2030: 0 unidades/);
    let prevented = false;
    points[5].listeners.keydown({key: "Enter", preventDefault() { prevented = true; }});
    assert.equal(prevented, true);
    assert.match(ui.get("previsao-ponto-detalhe").textContent, /06\/01\/2030: 0 unidades/);
});

test("valores grandes têm espaço horizontal para os sete rótulos sem truncamento", async () => {
    const ui = await harness(payload(Array(7).fill("12345678901234567890.5")), {width: 320});
    const labels = valueLabels(ui);
    assert.equal(labels.length, 7);
    const canvas = Number(ui.get("previsao-grafico").getAttribute("viewBox").split(" ")[2]);
    const boxes = labels.map((item) => {
        const half = item.ownText.length * 9 / 2;
        const x = Number(item.getAttribute("x"));
        return {left: x - half, right: x + half};
    });
    assert.ok(boxes[0].left >= 12 && boxes.at(-1).right <= canvas - 12);
    assert.ok(boxes.slice(1).every((box, index) => box.left >= boxes[index].right + 24));
});

test("intervalos maiores espaçam datas conforme largura e preservam todas as linhas", async () => {
    const ui = await harness(payload(Array(31).fill("0.5")), {width: 1000});
    const desktopCount = ticks(ui).length;
    assert.ok(desktopCount > 3 && desktopCount < 31);
    assert.equal(ui.get("previsao-periodos").children.length, 31);
    assert.equal(ui.get("previsao-total").textContent, "31");
    ui.wrapper.clientWidth = 320;
    ui.windowEvents.resize();
    const mobile = ticks(ui);
    assert.ok(mobile.length < desktopCount);
    assert.equal(mobile[0].ownText, "01/01");
    assert.equal(mobile.at(-1).ownText, "31/01");
    assert.equal(ui.get("previsao-periodos").children.length, 31);
    assert.equal(ui.calls.length, 1); // Redesenhar não consulta nem altera previsões.
});

test("séries longas reduzem rótulos conforme largura e mantêm cada ponto acessível", async () => {
    const ui = await harness(payload(Array.from({length: 90}, (_, index) => index % 3 ? "10.5" : "0")), {width: 1000});
    const desktop = valueLabels(ui).length;
    assert.ok(desktop > 3 && desktop < 90);
    ui.wrapper.clientWidth = 320;
    ui.windowEvents.resize();
    const labels = valueLabels(ui);
    assert.ok(labels.length > 0 && labels.length < desktop);
    const boxes = labels.map((item) => {
        const x = Number(item.getAttribute("x")), half = item.ownText.length * 9 / 2;
        return {left: x - half, right: x + half};
    });
    assert.ok(boxes.slice(1).every((box, index) => box.left >= boxes[index].right + 24));
    const points = descendants(ui.get("previsao-grafico"), "circle");
    const curve = descendants(ui.get("previsao-grafico"), "polyline")[0];
    assert.equal(points.length, 90);
    assert.equal(curve.getAttribute("points").split(" ").length, 90);
    assert.equal(ui.get("previsao-periodos").children.length, 90);
    points[88].listeners.pointerenter();
    assert.match(ui.get("previsao-ponto-detalhe").textContent, /30\/03\/2030: 11 unidades/);
    assert.match(points[88].getAttribute("aria-label"), /30\/03\/2030: 11 unidades/);
    assert.ok(labels.every((item) => Number(item.getAttribute("y")) < Number(ticks(ui)[0].getAttribute("y")) - 30));
    assert.equal(ui.calls.length, 1);
});

test("intervalo que cruza o ano mantém ano completo nas datas do gráfico", async () => {
    const ui = await harness(payload(Array(7).fill("1"), {first: "2030-12-29"}), {width: 320});
    assert.equal(ticks(ui).length, 7);
    assert.equal(ticks(ui)[0].ownText, "29/12/2030");
    assert.equal(ticks(ui).at(-1).ownText, "04/01/2031");
    assert.ok(parseInt(ui.get("previsao-grafico").style.minWidth, 10) >= 871);
});

test("produto associado usa nome explícito e código, sem associação usa categoria identificada", async () => {
    const ui = await harness(payload());
    const options = ui.get("previsao-filtros").elements.codigo_produto.children;
    assert.equal(options[1].textContent, "Produto Fictício · Código 00042 · Categoria: Grupo fictício");
    assert.equal(options[2].textContent, "Código 00043 · sem descrição cadastrada · Categoria: Formato fictício");
    assert.equal(options[1].value, "00042");
    assert.equal(options[2].value, "00043");
});

test("nomes e categorias são apresentados como texto, inclusive conteúdo com aparência de HTML", async () => {
    const data = payload();
    data.produtos[0].nome = "<img src=x onerror=alert(1)>";
    data.produtos[1].categoria = "<script>conteúdo fictício</script>";
    const ui = await harness(data);
    const options = ui.get("previsao-filtros").elements.codigo_produto.children;
    assert.match(options[1].textContent, /<img src=x/);
    assert.match(options[2].textContent, /Categoria: <script>/);
    assert.equal(options[1].children.length, 0);
    assert.equal(options[2].children.length, 0);
});

test("resposta atrasada não substitui a seleção mais recente", async () => {
    let release;
    const first = payload(["1.4"]);
    const second = payload(["4.5"]);
    const third = payload(["8.5"]);
    const ui = await harness(first, {response: async (_url, _options, number) => {
        if (number === 1) return {status: 200, body: first};
        if (number === 2) return new Promise((resolve) => { release = () => resolve({status: 200, body: second}); });
        return {status: 200, body: third};
    }});
    await ui.fire("previsao-filtros", "change");
    await ui.fire("previsao-filtros", "change");
    assert.equal(ui.get("previsao-total").textContent, "9");
    release();
    await ui.settle();
    assert.equal(ui.get("previsao-total").textContent, "9");
});

test("falha do recálculo mantém a publicação e os valores arredondados anteriores", async () => {
    const data = payload(["1.5", "2.5"]);
    const jobId = "00000000-0000-4000-8000-000000000001";
    const ui = await harness(data, {response: async (url) => {
        if (url.pathname === "/api/previsoes/atualizar") return {status: 202, body: {job_id: jobId, status: "em_execucao"}};
        if (url.pathname.startsWith("/api/previsoes/tarefas/")) return {status: 200, body: {
            job_id: jobId, status: "falhou", execucao_id: null,
            erro: {code: "UPDATE_FAILED", message: "Falha fictícia do recálculo."}
        }};
        return {status: 200, body: data};
    }});
    await ui.fire("previsao-atualizar", "submit");
    assert.equal(ui.get("previsao-total").textContent, "5");
    assert.match(ui.get("previsao-tarefa-status").textContent, /publicação anterior foi preservada/);
    assert.equal(ui.get("previsao-tarefa-erro").textContent, "Falha fictícia do recálculo.");
});

