/* Dados de previsão permanecem em memória; nenhuma gravação no navegador. */
(() => {
    "use strict";
    const app = document.getElementById("previsoes-app");
    if (!app) return;
    const byId = (id) => document.getElementById(id);
    const filters = byId("previsao-filtros");
    const updateForm = byId("previsao-atualizar");
    const svgNS = "http://www.w3.org/2000/svg";
    const modelNames = {
        naive: "Último valor (referência)",
        seasonal_naive: "Repetição semanal (referência)",
        random_forest: "Floresta aleatória",
        hist_gradient_boosting: "HistGradientBoosting"
    };
    const numberFormat = new Intl.NumberFormat("pt-BR", {maximumFractionDigits: 3});
    let queryVersion = 0;
    let queryController = null;
    let currentData = null;
    let updateMetadataLoaded = false;
    let activeJob = null;
    let jobVersion = 0;
    let jobController = null;
    let jobTimer = null;
    let pendingUpdate = false;

    function text(id, value) { byId(id).textContent = value; }
    function notice(id, value) {
        const element = byId(id);
        element.textContent = value || "";
        element.hidden = !value;
    }
    function node(tag, value, className) {
        const element = document.createElement(tag);
        if (value !== undefined) element.textContent = value;
        if (className) element.className = className;
        return element;
    }
    // HALF_UP para quantidades não negativas: a fração 0,5 arredonda para cima.
    // BigInt conserva a precisão textual; Number serve somente ao desenho do SVG.
    function roundedUnits(value) {
        if (typeof value !== "string" || !/^(0|[1-9]\d*)(?:\.\d+)?$/.test(value)) return null;
        const [integer, decimal = ""] = value.split(".");
        return BigInt(integer) + (decimal && decimal[0] >= "5" ? 1n : 0n);
    }
    function quantity(value) {
        return typeof value === "bigint" ? value.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".") : "Indisponível";
    }
    function date(value) {
        if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return "—";
        return value.slice(8, 10) + "/" + value.slice(5, 7) + "/" + value.slice(0, 4);
    }
    function range(start, end) { return start === end ? date(start) : date(start) + " a " + date(end); }
    function timestamp(value) {
        const parsed = new Date(value);
        if (!Number.isFinite(parsed.getTime())) return "—";
        return new Intl.DateTimeFormat("pt-BR", {
            dateStyle: "short", timeStyle: "short", timeZone: "America/Manaus"
        }).format(parsed) + " (Manaus)";
    }
    function productLabel(product) {
        const code = "Código " + (product ? product.codigo_produto : "");
        const name = product && product.produto_id != null && product.nome
            ? product.nome + " · " + code : code + " · sem descrição cadastrada";
        return name + (product && product.categoria ? " · Categoria: " + product.categoria : "");
    }
    function metric(value, percent = false) {
        return typeof value === "number" && Number.isFinite(value)
            ? numberFormat.format(value) + (percent ? "%" : "") : "Não definida";
    }
    function selection() {
        return {
            codigo_produto: filters.elements.codigo_produto.value,
            granularidade: filters.elements.granularidade.value,
            data_inicio: filters.elements.data_inicio.value,
            data_fim: filters.elements.data_fim.value
        };
    }
    function queryURL(values) {
        const url = new URL(app.dataset.api, window.location.href);
        Object.entries(values).forEach(([key, value]) => { if (value) url.searchParams.set(key, value); });
        return url;
    }
    async function request(url, options = {}) {
        let response;
        try {
            response = await fetch(url, {cache: "no-store", credentials: "same-origin", ...options});
        } catch (error) {
            if (error.name === "AbortError") throw error;
            throw new Error(navigator.onLine === false ? "Sem conexão. Verifique a rede e tente novamente." : "Não foi possível conectar ao sistema. Tente novamente.");
        }
        let payload;
        try { payload = await response.json(); }
        catch (_) { throw new Error("O sistema retornou uma resposta inválida. Tente novamente."); }
        if (!response.ok) {
            const error = new Error(typeof payload.erro === "string" ? payload.erro : "Não foi possível concluir a solicitação.");
            error.code = payload.code;
            error.status = response.status;
            throw error;
        }
        return payload;
    }
    function fillProducts(products, selected) {
        const select = filters.elements.codigo_produto;
        const options = [node("option", "Todos os produtos — visão geral")];
        options[0].value = "";
        products.forEach((product) => {
            const option = node("option", productLabel(product));
            option.value = product.codigo_produto;
            options.push(option);
        });
        select.replaceChildren(...options);
        select.value = selected || "";
    }
    function updateControls(metadata) {
        if (!metadata) return;
        if (metadata.data_corte_maxima) updateForm.elements.data_corte.max = metadata.data_corte_maxima;
        if (!updateForm.elements.data_corte.value && metadata.data_corte_sugerida) {
            updateForm.elements.data_corte.value = metadata.data_corte_sugerida;
        }
        if (!updateMetadataLoaded && Number.isInteger(metadata.horizonte_dias)) {
            updateForm.elements.horizonte_dias.value = metadata.horizonte_dias;
        }
        updateMetadataLoaded = true;
        updateForm.dataset.available = metadata.disponivel === false ? "false" : "true";
        text("previsao-atualizacao-info", metadata.disponivel === false
            ? "A atualização não está disponível nesta configuração. A previsão publicada pode continuar sendo consultada."
            : "Informe uma data de corte com o dia já encerrado. A atualização utiliza o histórico revisado e publica uma nova previsão somente após sua conclusão.");
        lockUpdate();
    }
    function lockUpdate() {
        const busy = pendingUpdate || activeJob !== null;
        Array.from(updateForm.elements).forEach((element) => { element.disabled = busy || updateForm.dataset.available === "false"; });
        updateForm.setAttribute("aria-busy", String(busy));
    }
    function svgElement(tag, attributes = {}, label) {
        const element = document.createElementNS(svgNS, tag);
        Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
        if (label !== undefined) element.textContent = label;
        return element;
    }
    function drawChart(periods, frequency, units) {
        const svg = byId("previsao-grafico");
        svg.replaceChildren();
        const pointHelp = "Passe sobre um ponto ou navegue pelos pontos com Tab para consultar a data e a estimativa arredondada.";
        text("previsao-ponto-detalhe", pointHelp);
        if (!periods.length) return;
        const sameYear = periods.every((period) => period.cobertura_inicio.slice(0, 4) === periods[0].cobertura_inicio.slice(0, 4));
        // Margem conservadora por caractere: também acomoda inteiros grandes.
        const valueWidth = (value) => quantity(value).length * 9;
        const maximumValueWidth = Math.max(...units.map(valueWidth));
        const numericSpacing = maximumValueWidth + 24;
        const labelSpacing = Math.max(sameYear ? 76 : 126, numericSpacing);
        const allDailyDates = frequency === "diaria" && periods.length <= 7;
        const left = Math.max(85, Math.ceil(maximumValueWidth / 2) + 12);
        const right = Math.max(30, Math.ceil(maximumValueWidth / 2) + 12);
        const top = 25, height = 205;
        const minimumWidth = Math.max(360, left + right + (allDailyDates ? (periods.length - 1) * labelSpacing : 76));
        const availableWidth = svg.parentElement && svg.parentElement.clientWidth || svg.clientWidth || 900;
        const canvasWidth = Math.max(availableWidth, minimumWidth);
        svg.style.minWidth = minimumWidth + "px";
        svg.setAttribute("viewBox", "0 0 " + canvasWidth + " 290");
        const width = canvasWidth - left - right;
        // A aproximação em ponto flutuante serve somente às coordenadas do gráfico.
        const values = units.map((value) => value === null ? NaN : Number(value));
        if (values.some((value) => !Number.isFinite(value) || value < 0)) {
            svg.append(svgElement("text", {x: 40, y: 120, class: "forecast-chart-label"}, "Consulte os valores na tabela abaixo."));
            return;
        }
        const maximum = Math.max(...values) || 1;
        const x = (index) => left + (values.length === 1 ? width / 2 : index * width / (values.length - 1));
        const y = (value) => top + height - (value / maximum) * height;
        const group = svgElement("g");
        const gridValues = [...new Set([0, 1, 2, 3, 4].map((step) => Math.round(maximum * (step / 4))))];
        gridValues.forEach((value) => {
            group.append(svgElement("line", {x1: left, x2: left + width, y1: y(value), y2: y(value), class: "forecast-chart-grid"}));
            group.append(svgElement("text", {x: left - 12, y: y(value) + 5, "text-anchor": "end", class: "forecast-chart-label"}, new Intl.NumberFormat("pt-BR", {notation: "compact", maximumFractionDigits: 1}).format(value)));
        });
        const points = values.map((value, index) => x(index) + "," + y(value)).join(" ");
        group.append(svgElement("polyline", {points, class: "forecast-chart-line"}));
        let selectedPoint = null;
        values.forEach((value, index) => {
            const description = range(periods[index].cobertura_inicio, periods[index].cobertura_fim)
                + ": " + quantity(units[index]) + " unidades estimadas (arredondadas)";
            const circle = svgElement("circle", {cx: x(index), cy: y(value), r: 3.5,
                class: "forecast-chart-point", tabindex: 0, role: "button", "aria-label": description});
            circle.append(svgElement("title", {}, description));
            const select = () => { selectedPoint = description; text("previsao-ponto-detalhe", description); };
            ["focus", "pointerenter"].forEach((event) => circle.addEventListener(event, () => text("previsao-ponto-detalhe", description)));
            ["blur", "pointerleave"].forEach((event) => circle.addEventListener(event, () => text("previsao-ponto-detalhe", selectedPoint || pointHelp)));
            circle.addEventListener("click", select);
            circle.addEventListener("keydown", (event) => {
                if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(); }
            });
            group.append(circle);
        });
        let previousRight = -Infinity;
        values.forEach((value, index) => {
            const anchor = "middle";
            const labelWidth = valueWidth(units[index]);
            const start = x(index) - labelWidth / 2;
            // Em séries longas, desenhar somente rótulos que caibam. A curva e
            // os pontos focalizáveis continuam contendo todos os períodos.
            if (!allDailyDates && (start < previousRight + 24 || start + labelWidth > canvasWidth - 12)) return;
            const label = svgElement("text", {x: x(index), y: y(value) - 12, "text-anchor": anchor,
                class: "forecast-chart-label forecast-chart-value-label", "aria-hidden": "true"}, quantity(units[index]));
            group.append(label);
            previousRight = start + labelWidth;
        });
        const capacity = Math.max(2, Math.floor(width / labelSpacing) + 1);
        const tickCount = Math.min(values.length, allDailyDates ? values.length : capacity);
        const tickIndices = tickCount === 1 ? [0] : Array.from({length: tickCount}, (_, index) =>
            Math.round(index * (values.length - 1) / (tickCount - 1)));
        tickIndices.forEach((index) => {
            const fullDate = date(periods[index].cobertura_inicio);
            const anchor = values.length === 1 ? "middle" : index === 0 ? "start" : index === values.length - 1 ? "end" : "middle";
            const label = svgElement("text", {x: x(index), y: top + height + 30, "text-anchor": anchor,
                class: "forecast-chart-label forecast-chart-x-label", "aria-label": fullDate}, sameYear ? fullDate.slice(0, 5) : fullDate);
            label.append(svgElement("title", {}, fullDate));
            group.append(label);
        });
        svg.append(group);
    }
    function renderMetrics(data) {
        const fragment = document.createDocumentFragment();
        data.avaliacoes.forEach((evaluation) => {
            const product = data.produtos.find((item) => item.codigo_produto === evaluation.codigo_produto);
            const heading = productLabel(product || {codigo_produto: evaluation.codigo_produto});
            const rows = [
                {name: evaluation.modelo, values: evaluation.teste, selected: true},
                {name: "naive", values: evaluation.baselines.naive, selected: false},
                {name: "seasonal_naive", values: evaluation.baselines.seasonal_naive, selected: false}
            ];
            rows.forEach((entry, index) => {
                const row = node("tr");
                const productCell = node("th");
                productCell.scope = "row";
                productCell.append(node("span", heading));
                if (index === 0) {
                    productCell.append(node("small", "Histórico do modelo publicado: " + range(evaluation.treino_inicio, evaluation.treino_fim)));
                    productCell.append(node("small", evaluation.teste_inicio ? "Teste: " + range(evaluation.teste_inicio, evaluation.teste_fim) : "Sem janela de teste suficiente"));
                }
                row.append(productCell, node("td", (modelNames[entry.name] || "Modelo de previsão") + (entry.selected ? " · selecionado" : "")));
                row.append(node("td", entry.selected && evaluation.validacao ? metric(evaluation.validacao.mae) : "—"));
                ["mae", "rmse", "wape", "smape"].forEach((key) => {
                    row.append(node("td", entry.values ? metric(entry.values[key], key === "wape" || key === "smape") : "Não avaliado"));
                });
                let comparison = "Referência para comparação";
                if (entry.selected) {
                    comparison = evaluation.fallback
                        ? "Histórico insuficiente para a avaliação completa; previsão de referência."
                        : evaluation.melhor_que_baselines === true
                            ? "Menor MAE no teste que as duas referências."
                            : evaluation.melhor_que_baselines === false
                                ? "Não superou as duas referências no teste."
                                : "Comparação não disponível.";
                }
                row.append(node("td", comparison));
                fragment.append(row);
            });
        });
        byId("previsao-metricas").replaceChildren(fragment);
        byId("previsao-avaliacao").hidden = data.avaliacoes.length === 0;
        byId("previsao-sem-avaliacao").hidden = data.avaliacoes.length !== 0;
        text("previsao-sem-avaliacao", "Não há avaliação temporal disponível para esta seleção.");
        const models = Object.entries(data.modelos).map(([name, count]) => (modelNames[name] || "Modelo de previsão") + ": " + count + (count === 1 ? " produto" : " produtos"));
        text("previsao-modelos", "Modelos selecionados: " + models.join("; ") + ".");
    }
    function render(data) {
        const provenance = byId("previsao-natureza");
        if (provenance) {
            provenance.hidden = false;
            provenance.textContent = data.experimento
                ? "Experimento: previsão baseada em histórico real preparado; estoque e vendas operacionais simulados. Seleção " + data.experimento.assinatura + "."
                : "Publicação anterior sem seleção experimental. Preserve-a como referência; novas atualizações usam somente associações experimentais selecionadas.";
        }
        const selected = data.selecao.codigo_produto || "";
        const product = data.produtos.find((item) => item.codigo_produto === selected);
        fillProducts(data.produtos, selected);
        filters.elements.granularidade.value = data.selecao.granularidade;
        filters.elements.data_inicio.value = data.selecao.data_inicio;
        filters.elements.data_fim.value = data.selecao.data_fim;
        [filters.elements.data_inicio, filters.elements.data_fim].forEach((input) => {
            input.min = data.cobertura.inicio;
            input.max = data.cobertura.fim;
        });
        text("previsao-selecao", selected ? productLabel(product || {codigo_produto: selected}) : "Visão geral dos produtos com previsão elegível");
        const units = data.periodos.map((period) => roundedUnits(period.quantidade_prevista));
        const total = units.some((value) => value === null) ? null : units.reduce((sum, value) => sum + value, 0n);
        text("previsao-total", quantity(total));
        text("previsao-cobertura", range(data.selecao.data_inicio, data.selecao.data_fim));
        text("previsao-cobertura-completa", "Cobertura completa da publicação: " + range(data.cobertura.inicio, data.cobertura.fim) + ".");
        text("previsao-corte", date(data.cobertura.data_corte));
        text("previsao-publicacao", timestamp(data.cobertura.gerada_em));
        const rows = data.periodos.map((period, index) => {
            const row = node("tr");
            row.append(node("td", range(period.periodo_inicio, period.periodo_fim)), node("td", range(period.cobertura_inicio, period.cobertura_fim) + (period.parcial ? " · parcial" : "")), node("td", quantity(units[index])));
            return row;
        });
        byId("previsao-periodos").replaceChildren(...rows);
        drawChart(data.periodos, data.selecao.granularidade, units);
        renderMetrics(data);
        updateControls(data.atualizacao);
        byId("previsao-painel").hidden = data.periodos.length === 0;
        byId("previsao-vazia").hidden = data.periodos.length !== 0;
        byId("previsao-desatualizada").hidden = data.cobertura.desatualizada !== true;
        currentData = data;
    }
    async function load({freshPublication = false, clear = false} = {}) {
        const version = ++queryVersion;
        if (queryController) queryController.abort();
        queryController = new AbortController();
        const signal = queryController.signal;
        notice("previsao-erro", "");
        text("previsao-status", "Carregando a previsão publicada…");
        byId("previsao-anterior").hidden = currentData === null;
        byId("previsao-painel").setAttribute("aria-busy", "true");
        try {
            let values = clear ? {granularidade: "diaria"} : selection();
            if (freshPublication) {
                const publication = await request(queryURL({}), {signal});
                if (version !== queryVersion) return;
                if (!publication.produtos.some((product) => product.codigo_produto === values.codigo_produto)) values.codigo_produto = "";
                const start = publication.cobertura.inicio, end = publication.cobertura.fim;
                if (!values.data_inicio || values.data_inicio > end || values.data_fim < start) {
                    values.data_inicio = start;
                    values.data_fim = end;
                } else {
                    values.data_inicio = values.data_inicio < start ? start : values.data_inicio;
                    values.data_fim = !values.data_fim || values.data_fim > end ? end : values.data_fim;
                }
            }
            const data = await request(queryURL(values), {signal});
            if (version !== queryVersion) return;
            render(data);
            text("previsao-status", data.periodos.length ? "Previsão publicada carregada." : "Consulta concluída sem períodos disponíveis.");
        } catch (error) {
            if (version !== queryVersion || error.name === "AbortError") return;
            if (["SELECTION_CHANGED", "EXPERIMENTAL_RUN_REQUIRED", "PRODUCT_NOT_SELECTED"].includes(error.code)) {
                currentData = null;
                byId("previsao-painel").hidden = true;
            }
            notice("previsao-erro", error.message);
            text("previsao-status", currentData ? "A consulta anterior continua exibida. Os filtros novos ainda não foram aplicados." : "Não foi possível carregar uma previsão.");
            if (error.code === "MODEL_UNAVAILABLE" && !currentData) {
                text("previsao-status", "Ainda não há previsão publicada. Você pode solicitar a primeira atualização abaixo.");
            }
        } finally {
            if (version === queryVersion) {
                byId("previsao-anterior").hidden = true;
                byId("previsao-painel").setAttribute("aria-busy", "false");
            }
        }
    }
    function validateDates() {
        const start = filters.elements.data_inicio, end = filters.elements.data_fim;
        end.setCustomValidity(start.value && end.value && start.value > end.value ? "A data final deve ser igual ou posterior à inicial." : "");
        return filters.reportValidity();
    }
    function stopJobPolling() {
        if (jobTimer !== null) window.clearTimeout(jobTimer);
        jobTimer = null;
        if (jobController) jobController.abort();
        jobController = null;
    }
    async function pollJob(version) {
        if (version !== jobVersion || activeJob === null) return;
        jobController = new AbortController();
        try {
            const result = await request(app.dataset.jobApi + encodeURIComponent(activeJob), {signal: jobController.signal});
            if (version !== jobVersion) return;
            if (result.status === "em_execucao") {
                text("previsao-tarefa-status", "Atualização em andamento. Você pode continuar consultando a publicação atual.");
                jobTimer = window.setTimeout(() => pollJob(version), 1000);
            } else if (result.status === "concluido") {
                activeJob = null;
                lockUpdate();
                text("previsao-tarefa-status", "Atualização concluída. Carregando a nova publicação…");
                await load({freshPublication: true});
                if (version === jobVersion) text("previsao-tarefa-status", "Atualização concluída. Consulte o estado do painel acima para confirmar o carregamento da nova publicação.");
            } else if (result.status === "falhou") {
                activeJob = null;
                lockUpdate();
                text("previsao-tarefa-status", "A atualização não foi concluída. A publicação anterior foi preservada.");
                notice("previsao-tarefa-erro", result.erro && typeof result.erro.message === "string" ? result.erro.message : "Não foi possível atualizar a previsão. Tente novamente.");
            } else {
                throw new Error("Não foi possível confirmar o estado da atualização.");
            }
        } catch (error) {
            if (version !== jobVersion || error.name === "AbortError") return;
            if (error.code === "JOB_NOT_FOUND" || error.status === 404) {
                activeJob = null;
                lockUpdate();
                text("previsao-tarefa-status", "O sistema não possui mais o acompanhamento desta atualização. Consulte novamente o painel antes de solicitar outro recálculo.");
                notice("previsao-tarefa-erro", error.message);
                byId("previsao-retomar").hidden = true;
                return;
            }
            text("previsao-tarefa-status", "O acompanhamento foi interrompido. A atualização pode continuar em andamento; a publicação anterior foi preservada.");
            notice("previsao-tarefa-erro", error.message);
            byId("previsao-retomar").hidden = false;
        }
    }
    filters.addEventListener("submit", (event) => {
        event.preventDefault();
        if (validateDates()) load();
    });
    filters.addEventListener("change", () => {
        // Uma resposta anterior também não pode sobrescrever um formulário inválido.
        queryVersion += 1;
        if (queryController) queryController.abort();
        if (validateDates()) load();
        else {
            byId("previsao-anterior").hidden = true;
            byId("previsao-painel").setAttribute("aria-busy", "false");
            text("previsao-status", "Corrija o intervalo de datas para aplicar os filtros. A consulta anterior continua exibida.");
        }
    });
    byId("previsao-limpar").addEventListener("click", () => {
        filters.reset();
        filters.elements.data_fim.setCustomValidity("");
        load({clear: true});
    });
    updateForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (pendingUpdate || activeJob !== null || !updateForm.reportValidity() || updateForm.dataset.available === "false") return;
        const payload = {data_corte: updateForm.elements.data_corte.value, horizonte_dias: Number(updateForm.elements.horizonte_dias.value)};
        pendingUpdate = true;
        lockUpdate();
        notice("previsao-tarefa-erro", "");
        text("previsao-tarefa-status", "Solicitando atualização…");
        const version = ++jobVersion;
        stopJobPolling();
        byId("previsao-retomar").hidden = true;
        jobController = new AbortController();
        try {
            const result = await request(app.dataset.updateApi, {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload), signal: jobController.signal
            });
            if (version !== jobVersion) return;
            if (result.status !== "em_execucao" || typeof result.job_id !== "string" || !/^[0-9a-f-]{36}$/i.test(result.job_id)) {
                throw new Error("Não foi possível confirmar o início da atualização.");
            }
            activeJob = result.job_id;
            text("previsao-tarefa-status", "Atualização em andamento. A publicação atual permanece disponível.");
            pollJob(version);
        } catch (error) {
            if (version !== jobVersion || error.name === "AbortError") return;
            text("previsao-tarefa-status", "Não foi possível confirmar o início da atualização. A publicação atual foi preservada.");
            notice("previsao-tarefa-erro", error.message);
        } finally {
            pendingUpdate = false;
            lockUpdate();
        }
    });
    byId("previsao-retomar").addEventListener("click", () => {
        byId("previsao-retomar").hidden = true;
        notice("previsao-tarefa-erro", "");
        stopJobPolling();
        pollJob(++jobVersion);
    });
    window.addEventListener("offline", () => notice("previsao-erro", "Sem conexão. Os valores já exibidos pertencem à última consulta carregada."));
    window.addEventListener("resize", () => {
        if (currentData) drawChart(currentData.periodos, currentData.selecao.granularidade,
            currentData.periodos.map((period) => roundedUnits(period.quantidade_prevista)));
    });
    window.addEventListener("pagehide", () => {
        queryVersion += 1;
        jobVersion += 1;
        if (queryController) queryController.abort();
        stopJobPolling();
    });
    load({clear: true});
})();
