/* Operações em JSON. Decimais de matérias-primas permanecem como texto. */
(() => {
    "use strict";
    const messages = {
        "movimentacao-form": "Movimentação registrada com sucesso.",
        "minimo-form": "Estoque mínimo atualizado com sucesso.",
        "materia-form": "Matéria-prima cadastrada com sucesso.",
        "sincronizar-form": "Alertas de estoque mínimo sincronizados.",
        "preventivo-form": "Alerta preventivo avaliado com sucesso."
    };
    const feedbackIds = {
        "movimentacao-form": "movimentacao-feedback", "minimo-form": "minimo-feedback",
        "materia-form": "materia-feedback", "sincronizar-form": "sincronizar-feedback",
        "risco-form": "risco-feedback", "preventivo-form": "preventivo-feedback"
    };
    const flashKey = "estoque.sucesso";
    const scale = 1000000000n;
    function feedback(form, value, kind = "success", focus = true) {
        const output = document.getElementById(feedbackIds[form.id]);
        if (!output) return;
        output.hidden = false;
        output.setAttribute("role", kind === "error" ? "alert" : "status");
        output.setAttribute("aria-live", kind === "error" ? "assertive" : "polite");
        output.className = "stock-form-feedback stock-feedback-" + kind;
        output.textContent = value;
        if (focus) {
            output.focus({preventScroll: true});
            output.scrollIntoView({block: "nearest", behavior: "smooth"});
        }
    }
    function rememberSuccess(id) {
        // Somente um identificador fixo de formulário; nunca dados ou saldos.
        try {
            window.sessionStorage.setItem(flashKey, id);
            return true;
        } catch (_) { return false; }
    }
    function restoreSuccess() {
        try {
            const id = window.sessionStorage.getItem(flashKey);
            window.sessionStorage.removeItem(flashKey);
            const form = id && Object.hasOwn(messages, id) ? document.getElementById(id) : null;
            if (form) feedback(form, messages[id]);
        } catch (_) { /* A operação também funciona sem armazenamento. */ }
    }
    function decimalUnits(value) {
        const normalized = value.trim().replace(",", ".");
        if (!/^\d+(?:\.\d{1,9})?$/.test(normalized)) return null;
        const [integer, fraction = ""] = normalized.split(".");
        return BigInt(integer) * scale + BigInt(fraction.padEnd(9, "0"));
    }
    function decimalText(value) {
        const integer = value / scale;
        const fraction = (value % scale).toString().padStart(9, "0").replace(/0+$/, "");
        return integer.toString() + (fraction ? "." + fraction : "");
    }
    function validateDecimal(input) {
        const optionalBlank = !input.required && input.value.trim() === "";
        const integerOnly = input.dataset.integer === "true";
        const current = decimalUnits(input.value);
        const valid = optionalBlank || (integerOnly
            ? /^\d+$/.test(input.value.trim()) && current !== null && current / scale <= BigInt(Number.MAX_SAFE_INTEGER)
            : current !== null);
        input.setCustomValidity(valid ? "" : integerOnly
            ? "Informe uma quantidade inteira não negativa de até 9.007.199.254.740.991 unidades."
            : "Informe zero ou um valor positivo com até nove casas decimais, usando ponto ou vírgula.");
        return valid;
    }
    for (const input of document.querySelectorAll("[data-decimal]")) {
        input.addEventListener("input", () => validateDecimal(input));
        validateDecimal(input);
    }
    for (const control of document.querySelectorAll("[data-decimal-stepper]")) {
        const input = control.querySelector("input");
        for (const button of control.querySelectorAll("[data-step]")) {
            button.addEventListener("click", () => {
                const current = decimalUnits(input.value);
                if (!validateDecimal(input) || current === null) {
                    input.reportValidity();
                    return;
                }
                const next = current + BigInt(button.dataset.step) * scale;
                if (input.dataset.integer === "true" && next / scale > BigInt(Number.MAX_SAFE_INTEGER)) {
                    input.setCustomValidity("A quantidade excede o limite inteiro aceito pela interface.");
                    input.reportValidity();
                    return;
                }
                input.value = decimalText(next < 0n ? 0n : next);
                input.dispatchEvent(new Event("input", {bubbles: true}));
            });
        }
    }
    function itemData(form, field) {
        const [tipo_item, id] = form.elements.namedItem("item").value.split(":");
        const value = decimalUnits(form.elements[field].value);
        return {tipo_item, item_id: Number(id), [field]: tipo_item === "produto" ? Number(value / scale) : decimalText(value)};
    }
    async function send(url, method, data, key) {
        let response;
        try {
            response = await fetch(url, {
                method, cache: "no-store", credentials: "same-origin",
                headers: {"Content-Type": "application/json", ...(key ? {"Idempotency-Key": key} : {})},
                body: data ? JSON.stringify(data) : undefined
            });
        } catch (_) {
            throw new Error("Não foi possível conectar ao sistema. Verifique a conexão e tente novamente.");
        }
        let result;
        try { result = await response.json(); }
        catch (_) { throw new Error("O sistema retornou uma resposta inválida. Tente novamente."); }
        if (!response.ok) throw new Error(typeof result.erro === "string" ? result.erro : "Não foi possível concluir a operação.");
        return result;
    }
    function bind(id, handler) {
        const form = document.getElementById(id);
        if (!form) return;
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (form.dataset.pending === "true" || !form.reportValidity()) return;
            const buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
            form.dataset.pending = "true";
            form.setAttribute("aria-busy", "true");
            buttons.forEach((button) => { button.disabled = true; });
            feedback(form, "Processando…", "info", false);
            try {
                const result = await handler(form);
                if (result && result.reload === false) {
                    feedback(form, result.message, result.kind || "info");
                } else {
                    feedback(form, messages[id]);
                    if (rememberSuccess(id)) {
                        window.location.reload();
                    } else {
                        const output = document.getElementById(feedbackIds[id]);
                        const link = document.createElement("a");
                        link.href = "/estoque#" + id;
                        link.textContent = "Atualizar dados da tela";
                        output.append(document.createTextNode(" "), link);
                    }
                }
            } catch (error) {
                feedback(form, error.message || "Não foi possível concluir a operação.", "error");
            } finally {
                form.dataset.pending = "false";
                form.setAttribute("aria-busy", "false");
                buttons.forEach((button) => { button.disabled = false; });
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
    });
    bind("minimo-form", async (form) => {
        await send("/api/estoque/minimo", "PUT", itemData(form, "estoque_minimo"));
    });
    bind("materia-form", async (form) => {
        const data = Object.fromEntries(new FormData(form));
        data.estoque_minimo = decimalText(decimalUnits(form.elements.estoque_minimo.value));
        const initial = form.elements.quantidade_inicial.value.trim();
        if (initial) data.quantidade_inicial = decimalText(decimalUnits(initial));
        else delete data.quantidade_inicial;
        await send("/api/estoque/materias-primas", "POST", data);
    });
    bind("sincronizar-form", async () => {
        await send("/api/estoque/alertas/sincronizar", "POST");
    });
    bind("risco-form", async (form) => {
        const params = new URLSearchParams({horizonte_dias: form.elements.horizonte_dias.value});
        const result = await send("/api/estoque/risco/" + form.elements.produto_id.value + "?" + params, "GET");
        return {reload: false, message: result.mensagem || (result.risco === true
            ? "O saldo projetado fica abaixo do estoque mínimo." : result.risco === false
                ? "O saldo projetado atende ao estoque mínimo."
                : "Risco desconhecido: a previsão está indisponível. Isso não representa demanda zero.")};
    });
    bind("preventivo-form", async (form) => {
        // O backend escolhe o horizonte oficial; nenhum campo editável é enviado.
        const result = await send("/api/estoque/alertas/preventivos/" + form.elements.produto_id.value, "POST", {});
        if (result.risco !== true && result.risco !== false) {
            return {reload: false, kind: "warning", message: result.mensagem ||
                "Risco desconhecido: não foi possível obter uma previsão válida. Os alertas anteriores foram preservados."};
        }
        return null;
    });
    for (const id of ["movimentacao-form", "minimo-form"]) {
        const form = document.getElementById(id);
        if (!form) continue;
        const field = form.elements.quantidade || form.elements.estoque_minimo;
        const update = () => {
            const integerOnly = form.elements.namedItem("item").value.startsWith("produto:");
            field.dataset.integer = String(integerOnly);
            field.step = "1";
            field.inputMode = integerOnly ? "numeric" : "decimal";
            field.pattern = integerOnly ? "[0-9]+" : "[0-9]+([.,][0-9]{1,9})?";
            validateDecimal(field);
            if (form.elements.tipo_movimentacao) {
                const adjustment = form.elements.tipo_movimentacao.value === "ajuste";
                document.getElementById("quantidade-label").textContent = adjustment ? "Saldo final desejado" : "Quantidade da movimentação";
            }
        };
        form.addEventListener("change", update);
        update();
    }
    restoreSuccess();
})();
