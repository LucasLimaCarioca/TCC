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
    const scope = panel.dataset.cacheScope;
    const cacheKey = "estoque.notificacoes.v1." + scope;
    const revisionKey = cacheKey + ".invalidacao";
    const day = () => new Intl.DateTimeFormat("en-CA", {
        timeZone: "America/Manaus", year: "numeric", month: "2-digit", day: "2-digit"
    }).format(new Date(Date.now()));
    let lastAttempt = null;
    let lastAttemptDay = null;
    let lastAttemptRevision = null;
    let previous = null;
    let confirmedAt = null;
    let inFlight = false;
    let generation = 0;
    let refreshAfterFlight = false;

    function revision() {
        try { return window.localStorage.getItem(revisionKey) || ""; }
        catch (_) { return ""; }
    }

    function countsFrom(payload) {
        const keys = ["total", "immediate", "preventive", "pending"];
        if (!payload || typeof payload !== "object" || Array.isArray(payload)
            || Object.keys(payload).length !== keys.length
            || keys.some((key) => !Number.isSafeInteger(payload[key]) || payload[key] < 0)
            || payload.total !== payload.immediate + payload.preventive || payload.pending > payload.preventive) {
            throw new Error("Resposta de alertas inválida.");
        }
        return Object.fromEntries(keys.map((key) => [key, payload[key]]));
    }

    function restore() {
        if (!scope) return;
        try {
            const cached = JSON.parse(window.sessionStorage.getItem(cacheKey));
            if (!cached || cached.revision !== revision() || cached.day !== day()
                || !Number.isSafeInteger(cached.at) || cached.at < 0 || cached.at > Date.now()) return;
            previous = countsFrom(cached.counts);
            confirmedAt = cached.at;
            lastAttempt = cached.stale ? null : cached.at;
            lastAttemptDay = cached.day;
            lastAttemptRevision = cached.revision;
            render(previous);
        } catch (_) { /* Sem armazenamento, consultar normalmente. */ }
    }

    function save(counts, expectedRevision, stale = false) {
        if (!scope) return;
        try {
            window.sessionStorage.setItem(cacheKey, JSON.stringify({
                counts, at: confirmedAt, day: day(), revision: expectedRevision, stale
            }));
        } catch (_) { /* O sino funciona também com armazenamento bloqueado. */ }
    }

    function invalidate({recarregar = true} = {}) {
        generation += 1;
        lastAttempt = null;
        try { window.sessionStorage.removeItem(cacheKey); } catch (_) { /* indisponível */ }
        try { window.localStorage.setItem(revisionKey, Date.now() + ":" + Math.random()); } catch (_) { /* indisponível */ }
        count.dataset.stale = "true";
        if (recarregar && document.visibilityState !== "hidden") {
            if (inFlight) refreshAfterFlight = true;
            else refresh(true);
        }
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
        const currentRevision = revision();
        if (!force && lastAttemptDay === day() && lastAttemptRevision === currentRevision
            && lastAttempt !== null && now - lastAttempt < automaticCooldown) return;
        lastAttempt = now;
        lastAttemptDay = day();
        lastAttemptRevision = currentRevision;
        const expectedGeneration = generation;
        const expectedRevision = currentRevision;
        const expectedDay = lastAttemptDay;
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
            // Uma operação ocorrida durante o GET invalida também sua resposta.
            if (generation !== expectedGeneration || revision() !== expectedRevision || day() !== expectedDay) {
                refreshAfterFlight = true;
                return;
            }
            previous = counts;
            confirmedAt = Date.now();
            render(counts);
            save(counts, expectedRevision);
        } catch (_) {
            panel.dataset.state = "error";
            count.dataset.stale = "true";
            if (previous === null) {
                count.textContent = "?";
                count.title = "Contagem de alertas indisponível.";
                summary.setAttribute("aria-label", "Consultar alertas de estoque: contagem indisponível.");
                status.textContent = "Não foi possível consultar os alertas. Tente atualizar a contagem.";
            } else {
                if (generation === expectedGeneration && revision() === expectedRevision) {
                    save(previous, expectedRevision, true);
                }
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
            if (refreshAfterFlight) {
                refreshAfterFlight = false;
                refresh(true);
            }
        }
    }

    window.estoqueNotificacoes = {invalidar: invalidate};
    panel.addEventListener("toggle", () => { if (panel.open) refresh(); });
    button.addEventListener("click", () => refresh(true));
    window.addEventListener("focus", () => { if (document.visibilityState !== "hidden") refresh(); });
    window.addEventListener("pageshow", () => { if (document.visibilityState !== "hidden") refresh(); });
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") refresh();
    });
    window.addEventListener("storage", (event) => {
        if (event.key !== revisionKey && event.key !== null) return;
        // Não retransmitir o evento para evitar um ciclo entre abas.
        generation += 1;
        lastAttempt = null;
        try { window.sessionStorage.removeItem(cacheKey); } catch (_) { /* indisponível */ }
        count.dataset.stale = "true";
        if (document.visibilityState !== "hidden") {
            if (inFlight) refreshAfterFlight = true;
            else refresh(true);
        }
    });
    window.setInterval(() => {
        if (document.visibilityState !== "hidden") refresh();
    }, automaticCooldown);
    restore();
    if (panel.dataset.invalidar === "true") invalidate();
    else refresh();
})();
