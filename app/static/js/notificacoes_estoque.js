/* Navegação expõe somente contagens; mensagens e saldos ficam na tela de Estoque. */
(() => {
    "use strict";
    const panel = document.getElementById("estoque-notificacoes");
    if (!panel) return;
    const count = document.getElementById("notificacoes-contagem");
    const status = document.getElementById("notificacoes-status");
    const list = document.getElementById("notificacoes-resumo");
    const button = document.getElementById("notificacoes-atualizar");
    const summary = panel.querySelector("summary");
    const automaticCooldown = 30000;
    let lastAttempt = null;
    let previous = null;
    let inFlight = false;

    function countsFrom(payload) {
        if (!Array.isArray(payload) || payload.some((item) => item === null || typeof item !== "object"
            || Array.isArray(item) || typeof item.ativo !== "boolean"
            || typeof item.tipo_alerta !== "string" || !item.tipo_alerta)) {
            throw new Error("Resposta de alertas inválida.");
        }
        const counts = {total: 0, immediate: 0, preventive: 0, pending: 0};
        payload.forEach((item) => {
            if (!item.ativo) return;
            counts.total += 1;
            if (item.tipo_alerta === "risco_futuro") {
                counts.preventive += 1;
                if (!item.avaliacao || item.avaliacao.atual !== true) counts.pending += 1;
            } else {
                counts.immediate += 1;
            }
        });
        return counts;
    }

    function item(text) {
        const element = document.createElement("li");
        element.textContent = text;
        return element;
    }

    function render(counts) {
        count.textContent = String(counts.total);
        count.dataset.stale = "false";
        count.title = counts.total === 1 ? "1 alerta ativo" : counts.total + " alertas ativos";
        summary.setAttribute("aria-label", "Consultar alertas de estoque: " + count.title + ".");
        list.replaceChildren(
            item("Alertas imediatos: " + counts.immediate + "."),
            item("Alertas preventivos: " + counts.preventive + "."),
            item("Preventivos com avaliação pendente ou desatualizada: " + counts.pending + ".")
        );
        status.textContent = counts.total ? "Contagem de alertas atualizada." : "Não há alertas ativos.";
        panel.dataset.state = "ready";
    }

    async function refresh(force = false) {
        if (inFlight) return;
        const now = Date.now();
        if (!force && lastAttempt !== null && now - lastAttempt < automaticCooldown) return;
        lastAttempt = now;
        inFlight = true;
        button.disabled = true;
        panel.dataset.state = "loading";
        panel.setAttribute("aria-busy", "true");
        if (previous !== null) {
            count.dataset.stale = "true";
            count.title = "Última contagem disponível; consulta em andamento.";
            summary.setAttribute("aria-label", "Consultando alertas de estoque: contagem anterior de "
                + previous.total + " alertas.");
            status.textContent = "Atualizando alertas. A contagem anterior continua exibida.";
        } else {
            count.textContent = "…";
            status.textContent = "Consultando alertas…";
        }
        try {
            const response = await fetch(panel.dataset.api, {
                method: "GET", credentials: "same-origin", cache: "no-store",
                headers: {"Accept": "application/json"}
            });
            if (!response.ok) throw new Error("Consulta indisponível.");
            const counts = countsFrom(await response.json());
            previous = counts;
            render(counts);
        } catch (_) {
            panel.dataset.state = "error";
            count.dataset.stale = "true";
            if (previous === null) {
                count.textContent = "?";
                count.title = "Contagem de alertas indisponível.";
                summary.setAttribute("aria-label", "Consultar alertas de estoque: contagem indisponível.");
                status.textContent = "Não foi possível consultar os alertas. Tente atualizar a contagem.";
            } else {
                count.title = "Contagem anterior; atualização não confirmada.";
                summary.setAttribute("aria-label", "Consultar alertas de estoque: contagem anterior de "
                    + previous.total + " alertas. Atualização não confirmada.");
                status.textContent = "Não foi possível atualizar. Exibindo a contagem anterior: "
                    + previous.total + " alertas. Tente novamente.";
            }
        } finally {
            inFlight = false;
            button.disabled = false;
            panel.setAttribute("aria-busy", "false");
        }
    }

    panel.addEventListener("toggle", () => { if (panel.open) refresh(true); });
    button.addEventListener("click", () => refresh(true));
    window.addEventListener("focus", () => { if (document.visibilityState !== "hidden") refresh(); });
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") refresh();
    });
    refresh();
})();
