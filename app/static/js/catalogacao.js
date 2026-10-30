/* Catalogação explícita; nenhum vínculo é deduzido de nomes, categorias ou IDs. */
(() => {
    "use strict";
    const app = document.getElementById("catalogacao-app");
    if (!app) return;
    const byId = (id) => document.getElementById(id);
    const form = byId("catalogacao-form");
    const reverseForm = byId("catalogacao-reverter");
    const states = {sem_associacao: "Sem associação", pendente: "Pendente de revisão",
        possivel_inconsistencia: "Possível inconsistência — conferir", revisado: "Revisado pelo operador"};
    let current = null;
    let loading = false;

    function node(tag, value) {
        const element = document.createElement(tag);
        if (value !== undefined) element.textContent = value;
        return element;
    }
    function feedback(id, message, error = false) {
        const element = byId(id);
        element.hidden = false;
        element.className = "stock-form-feedback stock-feedback-" + (error ? "error" : "success");
        element.setAttribute("role", error ? "alert" : "status");
        element.setAttribute("aria-live", error ? "assertive" : "polite");
        element.textContent = message;
        element.focus({preventScroll: true});
        element.scrollIntoView({block: "nearest"});
    }
    function codeRow() {
        return current && current.codigos.find((row) => row.codigo_produto === form.elements.codigo_produto.value);
    }
    function product(id) {
        return current && current.produtos.find((row) => String(row.id) === String(id));
    }
    function options(select, values, previous, placeholder) {
        select.replaceChildren();
        const empty = node("option", placeholder);
        empty.value = "";
        select.append(empty);
        values.forEach(([value, label]) => {
            const option = node("option", label);
            option.value = String(value);
            select.append(option);
        });
        select.value = values.some(([value]) => String(value) === previous) ? previous : "";
    }
    function selectionChanged() {
        const selected = codeRow();
        const action = form.elements.acao.value;
        const targetNeeded = action === "associar" || action === "alterar";
        byId("catalogacao-alvo").hidden = !targetNeeded;
        form.elements.produto_id.disabled = !targetNeeded;
        form.elements.produto_id.required = targetNeeded;
        byId("catalogacao-novo").hidden = action !== "criar";
        byId("catalogacao-novo").disabled = action !== "criar";
        byId("catalogacao-selecao").textContent = selected
            ? "Código " + selected.codigo_produto + " · " + (states[selected.estado] || "Pendente")
                + " · Cadastro atual: " + (selected.produto_id == null ? "sem associação" : selected.nome + " (ID interno " + selected.produto_id + ")")
                + ". " + (selected.avisos || []).join(" ")
            : "Selecione um código histórico. A listagem preserva todos os zeros do código.";
        form.elements.confirmar.checked = false;
    }
    function render(data) {
        current = data;
        const rows = data.codigos.map((row) => {
            const tr = node("tr");
            const metadata = (row.grupos || []).join(", ") + " · " + (row.categorias || []).join(", ");
            const owner = row.produto_id == null ? "Sem nome cadastrado / sem associação" : row.nome + " (ID interno " + row.produto_id + ")";
            const action = node("td");
            const button = node("button", "Selecionar");
            button.type = "button";
            button.addEventListener("click", () => {
                form.elements.codigo_produto.value = row.codigo_produto;
                form.elements.acao.value = row.produto_id == null ? "associar" : "revisar";
                selectionChanged();
                form.elements.acao.focus();
                form.scrollIntoView({block: "start"});
            });
            action.append(button);
            tr.append(node("td", row.codigo_produto), node("td", metadata), node("td", owner),
                node("td", states[row.estado] || "Pendente"), node("td", (row.avisos || []).join(" ") || "—"), action);
            return tr;
        });
        if (!rows.length) {
            const tr = node("tr"), td = node("td", "Nenhum código histórico disponível. Importe o histórico aprovado ou carregue uma publicação válida.");
            td.colSpan = 6;
            tr.append(td);
            rows.push(tr);
        }
        byId("catalogacao-codigos").replaceChildren(...rows);
        options(form.elements.codigo_produto, data.codigos.map((row) => [row.codigo_produto, row.codigo_produto + " · " + (states[row.estado] || "Pendente")]),
            form.elements.codigo_produto.value, "Selecione o código textual");
        options(form.elements.produto_id, data.produtos.filter((row) => !row.demonstracao && row.codigo_externo == null)
            .map((row) => [row.id, "ID interno " + row.id + " · " + row.nome + " · " + (row.ativo ? "ativo" : "inativo")]),
            form.elements.produto_id.value, "Selecione um cadastro livre");
        const operations = data.operacoes.map((operation) => {
            const tr = node("tr");
            tr.append(node("td", operation.criado_em), node("td", operation.acao + " · " + operation.id),
                node("td", operation.codigos.join(", ")), node("td", operation.operador + ": " + operation.motivo),
                node("td", operation.reversivel ? "Disponível mediante confirmação" : "Estado alterado ou já revertido"));
            return tr;
        });
        byId("catalogacao-operacoes").replaceChildren(...operations);
        options(reverseForm.elements.reverter_operacao_id, data.operacoes.filter((row) => row.reversivel)
            .map((row) => [row.id, row.acao + " · " + row.codigos.join(", ") + " · " + row.id]),
            reverseForm.elements.reverter_operacao_id.value, "Selecione uma operação reversível");
        reverseForm.elements.confirmar.checked = false;
        selectionChanged();
    }
    async function read() {
        if (loading) return false;
        loading = true;
        byId("catalogacao-atualizar").disabled = true;
        byId("catalogacao-status").textContent = "Atualizando os códigos históricos…";
        byId("catalogacao-erro").hidden = true;
        try {
            const response = await fetch("/api/catalogacao", {method: "GET", cache: "no-store", credentials: "same-origin"});
            if (!response.ok) throw new Error("Não foi possível consultar a catalogação. Atualize a listagem antes de confirmar operações.");
            const data = await response.json();
            if (!data || !Array.isArray(data.codigos) || !Array.isArray(data.produtos) || !Array.isArray(data.operacoes)) {
                throw new Error("A listagem recebida é inválida. Tente atualizar novamente.");
            }
            render(data);
            byId("catalogacao-status").textContent = "Listagem atualizada. A aprovação exige confirmação explícita dos dados comerciais.";
            return true;
        } catch (error) {
            current = null;
            byId("catalogacao-status").textContent = "Consulta indisponível. Nenhuma nova operação pode ser confirmada.";
            feedback("catalogacao-erro", error.message || "Não foi possível consultar a catalogação.", true);
            return false;
        } finally {
            loading = false;
            byId("catalogacao-atualizar").disabled = false;
        }
    }
    function payload(target) {
        const common = {confirmar: target.elements.confirmar.checked,
            operador: target.elements.operador.value.trim(), motivo: target.elements.motivo.value.trim()};
        if (!current || loading) throw new Error("Atualize a listagem antes de registrar uma operação.");
        if (!common.confirmar) throw new Error("Confirme explicitamente a operação após conferir os dados.");
        if (target === reverseForm) {
            const operation = current.operacoes.find((row) => row.id === target.elements.reverter_operacao_id.value && row.reversivel);
            if (!operation) throw new Error("Selecione uma operação que ainda possa ser revertida.");
            const versions = {};
            operation.produtos.forEach((id) => {
                const row = product(id);
                if (!row) throw new Error("O cadastro mudou. Atualize a listagem.");
                versions[String(id)] = row.versao;
            });
            return {...common, acao: "reverter", reverter_operacao_id: operation.id, versoes: versions};
        }
        const selected = codeRow();
        if (!selected) throw new Error("Selecione um código histórico disponível.");
        const action = form.elements.acao.value;
        const result = {...common, acao: action, codigo_produto: selected.codigo_produto, versao: selected.versao};
        if (action === "criar") {
            result.produto = Object.fromEntries(["nome", "categoria", "sabor", "estoque_inicial", "estoque_minimo", "descricao"]
                .map((name) => [name, form.elements[name].value.trim()]));
            result.produto.preco = form.elements.preco.value.trim().replace(",", ".");
            result.produto.ativo = form.elements.situacao.value === "ativo";
        } else {
            const id = action === "revisar" || action === "desassociar" ? selected.produto_id : form.elements.produto_id.value;
            const row = product(id);
            if (!row) throw new Error("Selecione um cadastro operacional válido para esta operação.");
            result.produto_id = row.id;
            result.versao_produto = row.versao;
        }
        return result;
    }
    function bind(target, output) {
        target.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (target.dataset.pending === "true" || !target.reportValidity()) return;
            const buttons = Array.from(target.querySelectorAll('button[type="submit"]'));
            target.dataset.pending = "true";
            target.setAttribute("aria-busy", "true");
            buttons.forEach((button) => { button.disabled = true; });
            let saved = false;
            try {
                const data = payload(target);
                const signature = JSON.stringify(data);
                if (target.dataset.signature !== signature) {
                    target.dataset.signature = signature;
                    target.dataset.operation = crypto.randomUUID();
                }
                const response = await fetch("/api/catalogacao/operacoes", {method: "POST", cache: "no-store", credentials: "same-origin",
                    headers: {"Content-Type": "application/json"}, body: JSON.stringify({...data, operacao_id: target.dataset.operation})});
                const result = await response.json();
                if (!response.ok) throw new Error(result.erro || "Não foi possível registrar a operação.");
                saved = true;
                const updated = await read();
                target.elements.confirmar.checked = false;
                feedback(output, "Operação registrada com auditoria." + (updated ? " Listagem atualizada." : " Atualize a listagem para continuar."));
            } catch (error) {
                feedback(output, saved ? "A operação foi registrada, mas a listagem precisa ser atualizada."
                    : error.message || "Falha de comunicação. Confira a listagem ou repita o mesmo envio para recuperar o resultado com segurança.", !saved);
            } finally {
                target.dataset.pending = "false";
                target.setAttribute("aria-busy", "false");
                buttons.forEach((button) => { button.disabled = false; });
            }
        });
    }
    form.elements.codigo_produto.addEventListener("change", selectionChanged);
    form.elements.acao.addEventListener("change", selectionChanged);
    form.elements.produto_id.addEventListener("change", () => { form.elements.confirmar.checked = false; });
    for (const target of [form, reverseForm]) {
        target.addEventListener("input", (event) => {
            if (event.target.name !== "confirmar") target.elements.confirmar.checked = false;
        });
    }
    reverseForm.elements.reverter_operacao_id.addEventListener("change", () => { reverseForm.elements.confirmar.checked = false; });
    byId("catalogacao-atualizar").addEventListener("click", read);
    bind(form, "catalogacao-feedback");
    bind(reverseForm, "catalogacao-reverter-feedback");
    read();
})();
