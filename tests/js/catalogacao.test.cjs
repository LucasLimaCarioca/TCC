"use strict";

// DOM/rede sintéticos: códigos e dados comerciais inventados para os testes.
const assert = require("node:assert/strict");
const {test} = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../app/static/js/catalogacao.js"), "utf8");
const previousOperation = "00000000-0000-4000-8000-000000000100";

class Element {
    constructor(tag = "div", id = "") {
        this.tagName = tag;
        this.id = id;
        this.children = [];
        this.dataset = {};
        this.attributes = {};
        this.listeners = {};
        this.ownText = "";
        this.value = "";
        this.hidden = false;
        this.disabled = false;
        this.required = false;
        this.checked = false;
        this.type = "text";
    }
    get textContent() { return this.ownText + this.children.map((child) => child.textContent).join(""); }
    set textContent(value) { this.ownText = String(value); this.children = []; }
    set innerHTML(_) { throw new Error("O teste não permite HTML derivado dos dados."); }
    append(...items) { items.forEach((item) => { item.parentElement = this; this.children.push(item); }); }
    replaceChildren(...items) { this.children = []; this.ownText = ""; this.append(...items); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    addEventListener(name, listener) { this.listeners[name] = listener; }
    focus(options) { this.focused = true; this.focusOptions = options; }
    scrollIntoView(options) { this.scrollOptions = options; }
    querySelectorAll(selector) { return selector === 'button[type="submit"]' ? [this.submitButton] : []; }
}

function payload() {
    return {
        codigos: [
            {codigo_produto: "00017", produto_id: null, nome: null, versao: "codigo-ficticio-17-v1",
                grupos: ["Grupo fictício"], categorias: ["Embalagem fictícia"], estado: "sem_associacao", avisos: []},
            {codigo_produto: "00042", produto_id: 42, nome: "Produto operacional fictício", versao: "codigo-ficticio-42-v3",
                grupos: ["Outro grupo fictício"], categorias: ["Outra embalagem fictícia"], estado: "possivel_inconsistencia",
                avisos: ["Diferença fictícia para conferência."]},
            {codigo_produto: "00043", produto_id: null, nome: null, versao: "codigo-ficticio-43-v1",
                grupos: [], categorias: [], estado: "pendente", avisos: []}
        ],
        produtos: [
            {id: 19, nome: "Cadastro livre fictício", codigo_externo: null, demonstracao: false, ativo: false, versao: "produto-19-v2", elegivel_experimento: false},
            {id: 42, nome: "Produto operacional fictício", codigo_externo: "00042", demonstracao: false, ativo: true, versao: "produto-42-v3"},
            {id: 17, nome: "Demonstração fictícia", codigo_externo: null, demonstracao: true, ativo: true, versao: "produto-17-v1", elegivel_experimento: true}
        ],
        operacoes: [
            {id: previousOperation, acao: "alterar", criado_em: "2030-01-01T12:00:00+00:00", codigos: ["00042"],
                produtos: [42, 19], operador: "Operador fictício", motivo: "Revisão fictícia", reversivel: true},
            {id: "00000000-0000-4000-8000-000000000200", acao: "associar", criado_em: "2030-01-01T11:00:00+00:00", codigos: ["00043"],
                produtos: [], operador: "Operador fictício", motivo: "Operação fictícia antiga", reversivel: false}
        ]
    };
}

function addControls(form, values) {
    const fields = Object.entries(values).map(([name, value]) => {
        const input = new Element("input");
        input.name = name;
        input.value = value;
        return input;
    });
    fields.forEach((input) => { fields[input.name] = input; });
    form.elements = fields;
    form.submitButton = new Element("button");
    form.elements.confirmar.type = "checkbox";
    form.elements.confirmar.required = true;
    form.elements.operador.required = form.elements.motivo.required = true;
}

async function settle() {
    await new Promise(setImmediate);
    await new Promise(setImmediate);
}

async function harness({data = payload(), response = null, absent = false, mode = "operacional"} = {}) {
    const elements = new Map();
    const get = (id) => {
        if (absent && id === "catalogacao-app") return null;
        if (!elements.has(id)) elements.set(id, new Element("div", id));
        return elements.get(id);
    };
    const form = get("catalogacao-form");
    const reverse = get("catalogacao-reverter");
    addControls(form, {tipo_vinculo: mode, codigo_produto: "", acao: "associar", produto_id: "", nome: "", categoria: "", sabor: "", preco: "",
        estoque_inicial: "", estoque_minimo: "", descricao: "", situacao: "", operador: "", motivo: "", confirmar: "",
        descricao_historica: "", embalagem_historica: "", unidade_historica: "", unidade_simulada: "",
        identidade_confirmada: "", embalagem_confirmada: "", unidade_confirmada: "", produto_ficticio_confirmado: "", selecionado_previsao: ""});
    addControls(reverse, {reverter_operacao_id: "", operador: "", motivo: "", confirmar: ""});
    form.elements.codigo_produto.required = true;
    reverse.elements.reverter_operacao_id.required = true;
    const commercial = ["nome", "categoria", "sabor", "preco", "estoque_inicial", "estoque_minimo", "situacao"];
    commercial.forEach((name) => { form.elements[name].required = true; });
    const compatibility = ["descricao_historica", "embalagem_historica", "unidade_historica", "unidade_simulada",
        "identidade_confirmada", "embalagem_confirmada", "unidade_confirmada", "produto_ficticio_confirmado"];
    compatibility.forEach((name) => { form.elements[name].required = true; });
    [...compatibility.slice(4), "selecionado_previsao"].forEach((name) => { form.elements[name].type = "checkbox"; });
    for (const target of [form, reverse]) {
        target.reportValidity = () => target.forceValidity || target.elements.every((field) => {
            if (field.disabled || target === form && commercial.includes(field.name) && get("catalogacao-novo").disabled) return true;
            if (target === form && compatibility.includes(field.name) && get("catalogacao-experimental").disabled) return true;
            return !field.required || (field.type === "checkbox" ? field.checked : field.value.trim() !== "");
        });
    }
    const calls = [];
    let identifiers = 0;
    const context = {
        document: {getElementById: get, createElement: (tag) => new Element(tag)},
        crypto: {randomUUID: () => "00000000-0000-4000-8000-" + String(++identifiers).padStart(12, "0")},
        fetch: async (url, options) => {
            const call = {url: String(url), options};
            calls.push(call);
            const result = response ? await response(call, calls.length) : {status: 200, body: options.method === "GET" ? data : {id: "operacao-ficticia"}};
            return {ok: result.status < 400, status: result.status, json: async () => result.body};
        }
    };
    vm.runInNewContext(source, context, {filename: "catalogacao.js"});
    await settle();
    return {get, form, reverse, calls, settle,
        get posts() { return calls.filter((call) => call.options.method === "POST"); },
        async submit(target = form) { await target.listeners.submit({preventDefault() {}}); await settle(); },
        async refresh() { await get("catalogacao-atualizar").listeners.click(); await settle(); },
        change(field, value) { field.value = value; field.listeners.change?.(); },
        choose(code = "00017", action = "associar", identifier = "19") {
            this.change(form.elements.codigo_produto, code);
            this.change(form.elements.acao, action);
            this.change(form.elements.produto_id, identifier);
            form.elements.operador.value = "  Operador fictício  ";
            form.elements.motivo.value = "  Conferência comercial fictícia  ";
        },
        confirm(target = form) { target.elements.confirmar.checked = true; }
    };
}

function descendants(element) {
    return element.children.flatMap((child) => [child, ...descendants(child)]);
}

function experimentalEvidence(ui, {selected = true} = {}) {
    Object.entries({descricao_historica: " Produto histórico fictício sabor A ", embalagem_historica: " Caixa fictícia de 10L ",
        unidade_historica: " caixa ", unidade_simulada: " caixa "}).forEach(([name, value]) => { ui.form.elements[name].value = value; });
    for (const name of ["identidade_confirmada", "embalagem_confirmada", "unidade_confirmada", "produto_ficticio_confirmado"]) {
        ui.form.elements[name].checked = true;
    }
    ui.form.elements.selecionado_previsao.checked = selected;
}

test("a interface oferece finalidade experimental por padrão e identifica a origem dos dados", () => {
    const template = fs.readFileSync(path.join(__dirname, "../../app/templates/catalogacao.html"), "utf8");
    assert.match(template, /value="experimental" selected/);
    assert.match(template, /Histórico real · estoque e vendas operacionais simulados/);
    assert.match(template, /11 produtos fictícios já definidos/);
    assert.match(template, /name="selecionado_previsao" type="checkbox"/);
});

test("modo experimental oferece somente os produtos fictícios elegíveis sem criar cadastros", async () => {
    const ui = await harness({mode: "experimental"});
    assert.deepEqual(ui.form.elements.produto_id.children.map((option) => option.value), ["", "17"]);
    assert.equal(ui.get("catalogacao-acao-criar").disabled, true);
    assert.equal(ui.get("catalogacao-acao-criar").hidden, true);
    assert.equal(ui.get("catalogacao-novo").disabled, true);
    assert.equal(ui.get("catalogacao-experimental").disabled, false);
    for (const name of ["descricao_historica", "embalagem_historica", "unidade_historica", "unidade_simulada"]) {
        assert.equal(ui.form.elements[name].value, "");
    }
    for (const name of ["identidade_confirmada", "embalagem_confirmada", "unidade_confirmada", "produto_ficticio_confirmado", "selecionado_previsao"]) {
        assert.equal(ui.form.elements[name].checked, false);
    }
    assert.match(ui.get("catalogacao-codigos").textContent, /Operacional \/ legado · fora da seleção experimental/);
});

test("associação experimental selecionada envia compatibilidade e conserva código textual e ID interno", async () => {
    const ui = await harness({mode: "experimental"});
    ui.choose("00043", "associar", "17");
    experimentalEvidence(ui);
    ui.confirm();
    await ui.submit();
    const body = JSON.parse(ui.posts[0].options.body);
    assert.equal(body.codigo_produto, "00043");
    assert.equal(body.produto_id, 17);
    assert.equal(body.tipo_vinculo, "experimental");
    assert.equal(body.selecionado_previsao, true);
    assert.equal(body.produto_ficticio_confirmado, true);
    assert.deepEqual(body.compatibilidade, {identidade_confirmada: true, embalagem_confirmada: true, unidade_confirmada: true,
        descricao_historica: "Produto histórico fictício sabor A", embalagem_historica: "Caixa fictícia de 10L",
        unidade_historica: "caixa", unidade_simulada: "caixa"});
    assert.equal(Object.hasOwn(body, "produto"), false);
});

test("confirmações comerciais ausentes ou negativas impedem o envio mesmo sem validação nativa", async () => {
    const ui = await harness({mode: "experimental"});
    ui.choose("00017", "associar", "17");
    ui.form.forceValidity = true;
    ui.confirm();
    await ui.submit();
    assert.equal(ui.posts.length, 0);
    assert.match(ui.get("catalogacao-feedback").textContent, /Confirme a identidade comercial/);
    for (const name of ["identidade_confirmada", "embalagem_confirmada", "unidade_confirmada", "produto_ficticio_confirmado"]) {
        experimentalEvidence(ui);
        ui.form.elements[name].checked = false;
        await ui.submit();
        assert.equal(ui.posts.length, 0);
    }
    experimentalEvidence(ui);
    ui.form.elements.descricao_historica.value = " ";
    await ui.submit();
    assert.equal(ui.posts.length, 0);
    assert.match(ui.get("catalogacao-feedback").textContent, /referência comercial/);
});

test("adulterar a ação ou o produto não permite criação experimental nem destino não elegível", async () => {
    const ui = await harness({mode: "experimental"});
    ui.choose("00017", "associar", "17");
    experimentalEvidence(ui);
    ui.confirm();
    ui.form.forceValidity = true;
    ui.form.elements.acao.value = "criar";
    await ui.submit();
    assert.equal(ui.posts.length, 0);
    assert.match(ui.get("catalogacao-feedback").textContent, /não permite criar produtos/);
    ui.form.elements.acao.value = "associar";
    ui.form.elements.produto_id.value = "19";
    await ui.submit();
    assert.equal(ui.posts.length, 0);
    assert.match(ui.get("catalogacao-feedback").textContent, /11 produtos fictícios elegíveis/);
});

test("revisão restaura evidências e seleção atual sem aprovar caixas de compatibilidade", async () => {
    const data = payload();
    const mapped = data.codigos[1];
    mapped.tipo_vinculo = "experimental";
    mapped.selecionado_previsao = true;
    mapped.compatibilidade = {identidade_confirmada: true, embalagem_confirmada: true, unidade_confirmada: true,
        descricao_historica: "Descrição histórica fictícia conferida", embalagem_historica: "Embalagem fictícia conferida",
        unidade_historica: "caixa", unidade_simulada: "caixa", produto_ficticio_confirmado: true};
    data.produtos[1].elegivel_experimento = true;
    const ui = await harness({data});
    ui.choose("00042", "revisar");
    assert.equal(ui.form.elements.tipo_vinculo.value, "experimental");
    assert.equal(ui.form.elements.descricao_historica.value, mapped.compatibilidade.descricao_historica);
    assert.equal(ui.form.elements.selecionado_previsao.checked, true);
    assert.equal(ui.form.elements.identidade_confirmada.checked, false);
    assert.equal(ui.form.elements.produto_ficticio_confirmado.checked, false);
    assert.match(ui.get("catalogacao-codigos").textContent, /Experimental · histórico real, estoque e vendas simulados · selecionado/);
    for (const name of ["identidade_confirmada", "embalagem_confirmada", "unidade_confirmada", "produto_ficticio_confirmado"]) ui.form.elements[name].checked = true;
    ui.form.elements.selecionado_previsao.checked = false;
    ui.confirm();
    await ui.submit();
    const body = JSON.parse(ui.posts[0].options.body);
    assert.equal(body.acao, "revisar");
    assert.equal(body.tipo_vinculo, "experimental");
    assert.equal(body.selecionado_previsao, false);
    assert.equal(body.produto_id, 42);
    assert.equal(body.compatibilidade.descricao_historica, mapped.compatibilidade.descricao_historica);
});

test("editar evidências ou inclusão na previsão exige nova confirmação da operação", async () => {
    const ui = await harness({mode: "experimental"});
    ui.choose("00017", "associar", "17");
    experimentalEvidence(ui);
    for (const name of ["descricao_historica", "embalagem_historica", "unidade_historica", "unidade_simulada", "selecionado_previsao",
        "identidade_confirmada", "embalagem_confirmada", "unidade_confirmada", "produto_ficticio_confirmado"]) {
        ui.confirm();
        ui.form.listeners.input({target: ui.form.elements[name]});
        assert.equal(ui.form.elements.confirmar.checked, false);
    }
    ui.confirm();
    ui.change(ui.form.elements.tipo_vinculo, "operacional");
    assert.equal(ui.form.elements.confirmar.checked, false);
    assert.equal(ui.form.elements.identidade_confirmada.checked, false);
    assert.equal(ui.get("catalogacao-experimental").disabled, true);
});

test("renderiza códigos textuais, ausência de nome e avisos usando apenas texto", async () => {
    const data = payload();
    data.codigos[0].grupos = ["<script>grupo fictício</script>"];
    data.codigos[0].categorias = ["<img src=x onerror=alert(1)>"];
    data.codigos[1].nome = "<b>nome operacional fictício</b>";
    const ui = await harness({data});
    assert.equal(ui.calls.length, 1);
    assert.equal(ui.calls[0].url, "/api/catalogacao");
    assert.equal(ui.calls[0].options.method, "GET");
    assert.equal(ui.calls[0].options.cache, "no-store");
    const rows = ui.get("catalogacao-codigos").children;
    assert.equal(rows[0].children[0].textContent, "00017");
    assert.match(rows[0].textContent, /Sem nome cadastrado \/ sem associação/);
    assert.match(rows[0].textContent, /<script>grupo fictício<\/script>/);
    assert.match(rows[0].textContent, /<img src=x/);
    assert.match(rows[1].textContent, /<b>nome operacional fictício<\/b>/);
    assert.match(rows[1].textContent, /Possível inconsistência — conferir/);
    assert.ok(descendants(ui.get("catalogacao-codigos")).every((item) => !["script", "img", "b"].includes(item.tagName)));
    assert.deepEqual(ui.form.elements.produto_id.children.map((option) => option.value), ["", "19"]);
    rows[0].children[5].children[0].listeners.click();
    assert.equal(ui.form.elements.codigo_produto.value, "00017");
    assert.equal(ui.form.elements.acao.value, "associar");
    assert.match(ui.get("catalogacao-selecao").textContent, /Código 00017/);
});

test("confirmação explícita é obrigatória e mudanças de código, ação ou destino a invalidam", async () => {
    const ui = await harness();
    ui.choose();
    await ui.submit();
    assert.equal(ui.posts.length, 0); // Validação nativa do checkbox.
    ui.form.forceValidity = true; // A montagem do payload também protege a confirmação.
    await ui.submit();
    assert.match(ui.get("catalogacao-feedback").textContent, /Confirme explicitamente/);
    assert.equal(ui.posts.length, 0);
    for (const field of [ui.form.elements.codigo_produto, ui.form.elements.acao, ui.form.elements.produto_id]) {
        ui.confirm();
        ui.change(field, field.value);
        assert.equal(ui.form.elements.confirmar.checked, false);
    }
});

test("associação envia código com zeros e versões distintas de código e produto", async () => {
    const ui = await harness();
    ui.choose();
    ui.confirm();
    await ui.submit();
    assert.equal(ui.posts.length, 1);
    const request = ui.posts[0];
    const body = JSON.parse(request.options.body);
    assert.equal(request.url, "/api/catalogacao/operacoes");
    assert.equal(request.options.method, "POST");
    assert.equal(body.acao, "associar");
    assert.equal(body.codigo_produto, "00017");
    assert.equal(body.produto_id, 19); // ID interno não é o código comercial.
    assert.equal(body.versao, "codigo-ficticio-17-v1");
    assert.equal(body.versao_produto, "produto-19-v2");
    assert.equal(body.confirmar, true);
    assert.equal(body.operador, "Operador fictício");
    assert.equal(body.motivo, "Conferência comercial fictícia");
    assert.match(body.operacao_id, /^[0-9a-f-]{36}$/);
    assert.equal(ui.form.elements.confirmar.checked, false);
    assert.match(ui.get("catalogacao-feedback").textContent, /Operação registrada com auditoria/);
});

test("criação exige dados comerciais explícitos e envia situação inativa sem inferir valores", async () => {
    const ui = await harness();
    ui.choose("00017", "criar");
    assert.equal(ui.get("catalogacao-novo").disabled, false);
    assert.equal(ui.get("catalogacao-novo").hidden, false);
    assert.equal(ui.form.elements.produto_id.disabled, true);
    for (const name of ["nome", "categoria", "sabor", "preco", "estoque_inicial", "estoque_minimo", "situacao"]) {
        assert.equal(ui.form.elements[name].value, "");
    }
    ui.confirm();
    await ui.submit();
    assert.equal(ui.posts.length, 0);
    const input = {nome: " Produto fictício completo ", categoria: " Categoria fictícia ", sabor: " Variação fictícia ",
        preco: "12,50", estoque_inicial: "3", estoque_minimo: "1", descricao: " Descrição fictícia ", situacao: "inativo"};
    Object.entries(input).forEach(([key, value]) => { ui.form.elements[key].value = value; });
    await ui.submit();
    const body = JSON.parse(ui.posts[0].options.body);
    assert.equal(body.acao, "criar");
    assert.equal(body.codigo_produto, "00017");
    assert.equal(body.versao, "codigo-ficticio-17-v1");
    assert.deepEqual(body.produto, {nome: "Produto fictício completo", categoria: "Categoria fictícia", sabor: "Variação fictícia",
        preco: "12.50", estoque_inicial: "3", estoque_minimo: "1", descricao: "Descrição fictícia", ativo: false});
    assert.equal(Object.hasOwn(body, "produto_id"), false);
});

test("editar qualquer campo do novo cadastro exige confirmar novamente", async () => {
    const ui = await harness();
    ui.choose("00017", "criar");
    for (const name of ["nome", "categoria", "sabor", "preco", "estoque_inicial", "estoque_minimo", "situacao", "descricao", "operador", "motivo"]) {
        ui.confirm();
        const field = ui.form.elements[name];
        field.value = name === "situacao" ? "inativo" : "Conteúdo fictício editado";
        ui.form.listeners.input({target: field});
        assert.equal(ui.form.elements.confirmar.checked, false, "Reconfirmar após editar " + name);
    }
    ui.confirm();
    ui.form.listeners.input({target: ui.form.elements.confirmar});
    assert.equal(ui.form.elements.confirmar.checked, true);
});

test("revisar e desassociar usam o vínculo atual e sua versão, sem trocar pelo destino livre", async () => {
    for (const action of ["revisar", "desassociar"]) {
        const ui = await harness();
        ui.choose("00042", action, "19");
        ui.confirm();
        await ui.submit();
        const body = JSON.parse(ui.posts[0].options.body);
        assert.equal(body.produto_id, 42);
        assert.equal(body.versao_produto, "produto-42-v3");
        assert.equal(body.versao, "codigo-ficticio-42-v3");
        assert.equal(body.codigo_produto, "00042");
    }
});

test("reversão envia UUID alvo e versões atuais de todos os produtos afetados", async () => {
    const ui = await harness();
    assert.deepEqual(ui.reverse.elements.reverter_operacao_id.children.map((option) => option.value), ["", previousOperation]);
    ui.change(ui.reverse.elements.reverter_operacao_id, previousOperation);
    ui.reverse.elements.operador.value = "Operador fictício";
    ui.reverse.elements.motivo.value = "Reversão fictícia confirmada";
    ui.confirm(ui.reverse);
    await ui.submit(ui.reverse);
    const body = JSON.parse(ui.posts[0].options.body);
    assert.equal(body.acao, "reverter");
    assert.equal(body.reverter_operacao_id, previousOperation);
    assert.notEqual(body.operacao_id, previousOperation);
    assert.deepEqual(body.versoes, {19: "produto-19-v2", 42: "produto-42-v3"});
    assert.equal(body.confirmar, true);
    assert.equal(ui.reverse.elements.confirmar.checked, false);
});

test("erro HTTP fica visível e acessível, preservando valores e confirmação para repetição", async () => {
    const data = payload();
    const ui = await harness({response: async (call) => call.options.method === "GET" ? {status: 200, body: data}
        : {status: 409, body: {erro: "Conflito fictício: atualize e confira o vínculo."}}});
    ui.choose();
    ui.confirm();
    const before = ui.form.elements.map((field) => [field.name, field.value, field.checked]);
    await ui.submit();
    const output = ui.get("catalogacao-feedback");
    assert.equal(output.hidden, false);
    assert.equal(output.getAttribute("role"), "alert");
    assert.equal(output.getAttribute("aria-live"), "assertive");
    assert.equal(output.focused, true);
    assert.match(output.className, /stock-feedback-error/);
    assert.match(output.textContent, /Conflito fictício/);
    assert.deepEqual(ui.form.elements.map((field) => [field.name, field.value, field.checked]), before);
    assert.equal(ui.form.submitButton.disabled, false);
    assert.equal(ui.form.getAttribute("aria-busy"), "false");
});

test("falha de rede conserva UUID e conteúdo ao repetir o mesmo envio", async () => {
    const data = payload();
    let attempts = 0;
    const ui = await harness({response: async (call) => {
        if (call.options.method === "GET") return {status: 200, body: data};
        if (++attempts === 1) throw new Error("Falha fictícia da rede.");
        return {status: 200, body: {id: "operacao-ficticia"}};
    }});
    ui.choose();
    ui.confirm();
    await ui.submit();
    assert.equal(ui.form.elements.confirmar.checked, true);
    assert.match(ui.get("catalogacao-feedback").textContent, /Falha fictícia da rede/);
    await ui.submit();
    assert.equal(ui.posts.length, 2);
    assert.equal(ui.posts[0].options.body, ui.posts[1].options.body);
    assert.equal(ui.get("catalogacao-feedback").getAttribute("role"), "status");
});

test("submissão dupla do mesmo formulário faz somente uma chamada enquanto pendente", async () => {
    const data = payload();
    let release;
    const ui = await harness({response: async (call) => call.options.method === "GET" ? {status: 200, body: data}
        : new Promise((resolve) => { release = resolve; })});
    ui.choose();
    ui.confirm();
    const first = ui.submit();
    assert.equal(ui.form.submitButton.disabled, true);
    assert.equal(ui.form.getAttribute("aria-busy"), "true");
    await ui.submit();
    assert.equal(ui.posts.length, 1);
    release({status: 200, body: {id: "operacao-ficticia"}});
    await first;
    assert.equal(ui.form.submitButton.disabled, false);
    assert.equal(ui.form.dataset.pending, "false");
});

test("falha no GET invalida o snapshot e impede mutações mesmo com seleções antigas", async () => {
    const data = payload();
    let unavailable = false;
    const ui = await harness({response: async () => unavailable ? {status: 503, body: {}} : {status: 200, body: data}});
    ui.choose();
    ui.confirm();
    unavailable = true;
    await ui.refresh();
    assert.equal(ui.get("catalogacao-erro").hidden, false);
    assert.equal(ui.get("catalogacao-erro").getAttribute("role"), "alert");
    assert.match(ui.get("catalogacao-status").textContent, /Nenhuma nova operação pode ser confirmada/);
    await ui.submit();
    assert.equal(ui.posts.length, 0);
    assert.match(ui.get("catalogacao-feedback").textContent, /Atualize a listagem/);
    unavailable = false;
    await ui.refresh();
    ui.confirm();
    await ui.submit();
    assert.equal(ui.posts.length, 1);
});

test("mutação registrada seguida de falha do GET informa sucesso e bloqueia novos envios", async () => {
    const data = payload();
    let reads = 0;
    const ui = await harness({response: async (call) => call.options.method === "POST" ? {status: 200, body: {id: "operacao-ficticia"}}
        : ++reads === 1 ? {status: 200, body: data} : {status: 503, body: {}}});
    ui.choose();
    ui.confirm();
    await ui.submit();
    const output = ui.get("catalogacao-feedback");
    assert.equal(output.getAttribute("role"), "status");
    assert.match(output.textContent, /Operação registrada com auditoria.*Atualize a listagem para continuar/);
    ui.confirm();
    await ui.submit();
    assert.equal(ui.posts.length, 1);
    assert.match(output.textContent, /Atualize a listagem/);
});

test("listagem inválida e seleção inexistente não habilitam uma operação", async () => {
    const malformed = await harness({data: {codigos: [], produtos: null, operacoes: []}});
    assert.match(malformed.get("catalogacao-erro").textContent, /listagem recebida é inválida/);
    const ui = await harness();
    ui.choose("00999");
    ui.confirm();
    await ui.submit();
    assert.equal(ui.posts.length, 0);
    assert.match(ui.get("catalogacao-feedback").textContent, /Selecione um código histórico disponível/);
});

test("componente ausente não consulta nem registra operações", async () => {
    const ui = await harness({absent: true});
    assert.equal(ui.calls.length, 0);
});
