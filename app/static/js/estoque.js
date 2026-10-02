/* Operações usam JSON; decimais de matérias-primas permanecem como texto. */
const feedback = document.getElementById("estoque-feedback");

function itemData(form, field) {
    const [tipo_item, id] = form.elements.namedItem("item").value.split(":");
    const value = form.elements[field].value;
    return {tipo_item, item_id: Number(id), [field]: tipo_item === "produto" ? Number(value) : value};
}

async function send(url, method, data, key) {
    const response = await fetch(url, {
        method,
        headers: {"Content-Type": "application/json", ...(key ? {"Idempotency-Key": key} : {})},
        body: data ? JSON.stringify(data) : undefined
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.erro || "Não foi possível concluir a operação.");
    return result;
}

function bind(id, handler) {
    const form = document.getElementById(id);
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const button = form.querySelector("button");
        button.disabled = true;
        feedback.textContent = "Processando…";
        try {
            await handler(form);
        } catch (error) {
            feedback.textContent = error.message;
        } finally {
            button.disabled = false;
        }
    });
}

bind("movimentacao-form", async (form) => {
    const data = {...itemData(form, "quantidade"), tipo_movimentacao: form.elements.tipo_movimentacao.value, motivo: form.elements.motivo.value};
    const signature = JSON.stringify(data);
    if (form.dataset.signature !== signature) {
        form.dataset.signature = signature;
        form.dataset.operation = crypto.randomUUID();
    }
    await send("/api/estoque/movimentacoes", "POST", data, form.dataset.operation);
    window.location.reload();
});
bind("minimo-form", async (form) => {
    await send("/api/estoque/minimo", "PUT", itemData(form, "estoque_minimo"));
    window.location.reload();
});
bind("materia-form", async (form) => {
    await send("/api/estoque/materias-primas", "POST", Object.fromEntries(new FormData(form)));
    window.location.reload();
});
bind("risco-form", async (form) => {
    const result = await send(`/api/estoque/risco/${form.elements.produto_id.value}`, "GET");
    document.getElementById("risco-feedback").textContent = result.mensagem || (result.risco ? "O saldo projetado fica abaixo do estoque mínimo." : "O saldo projetado atende ao estoque mínimo.");
    feedback.textContent = "Consulta concluída.";
});

for (const id of ["movimentacao-form", "minimo-form"]) {
    const form = document.getElementById(id);
    const field = form.elements.quantidade || form.elements.estoque_minimo;
    const update = () => {
        field.step = form.elements.namedItem("item").value.startsWith("produto:") ? "1" : "0.000000001";
        if (form.elements.tipo_movimentacao) {
            const ajuste = form.elements.tipo_movimentacao.value === "ajuste";
            document.getElementById("quantidade-label").textContent = ajuste ? "Saldo final desejado" : "Quantidade da movimentação";
        }
    };
    form.addEventListener("change", update);
    update();
}
