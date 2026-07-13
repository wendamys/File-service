// Vanilla JS для двух страниц веб-интерфейса. Без сборки, без внешних CDN.

/** Показать сетевую/серверную ошибку пользователю (не проглатывать молча). */
function showGlobalError(message) {
    let box = document.getElementById("global-error");
    if (!box) {
        box = document.createElement("div");
        box.id = "global-error";
        document.querySelector("main.container").prepend(box);
    }
    box.textContent = message;
    box.classList.remove("hidden");
}

function clearGlobalError() {
    const box = document.getElementById("global-error");
    if (box) {
        box.classList.add("hidden");
    }
}

/** fetch с JSON-парсингом и понятной ошибкой при сетевом сбое/не-2xx ответе. */
async function apiFetch(url, options) {
    let response;
    try {
        response = await fetch(url, options);
    } catch (e) {
        throw new Error(`Не удалось связаться с сервером: ${e.message}`);
    }

    let data = null;
    const text = await response.text();
    if (text) {
        try {
            data = JSON.parse(text);
        } catch (e) {
            data = null;
        }
    }

    if (!response.ok) {
        const detail = (data && data.detail) ? data.detail : `HTTP ${response.status}`;
        const error = new Error(detail);
        error.status = response.status;
        error.data = data;
        throw error;
    }
    return data;
}

// ---------------------------------------------------------------------------
// Страница "/" — запуск скачивания и опрос статуса.
// ---------------------------------------------------------------------------

function startStatusPolling() {
    const startBtn = document.getElementById("start-btn");
    const stopBtn = document.getElementById("stop-btn");

    startBtn.addEventListener("click", async () => {
        try {
            await apiFetch("/api/download/start", { method: "POST" });
            clearGlobalError();
        } catch (e) {
            if (e.status === 409) {
                showGlobalError("Скачивание уже запущено.");
            } else {
                showGlobalError(e.message);
            }
        }
        updateStatus();
    });

    stopBtn.addEventListener("click", async () => {
        try {
            await apiFetch("/api/download/stop", { method: "POST" });
            clearGlobalError();
        } catch (e) {
            showGlobalError(e.message);
        }
        updateStatus();
    });

    updateStatus();
    setInterval(updateStatus, 1000);
}

async function updateStatus() {
    let state;
    try {
        state = await apiFetch("/api/download/status");
        clearGlobalError();
    } catch (e) {
        showGlobalError(e.message);
        return;
    }

    document.getElementById("status-text").textContent = state.status;
    document.getElementById("started-at").textContent = state.started_at_nsk || "-";
    document.getElementById("names-received").textContent = state.names_received;
    document.getElementById("downloaded").textContent = state.downloaded;
    document.getElementById("total-in-batch").textContent = state.names_received;

    const percent = state.names_received > 0
        ? Math.min(100, Math.round((state.downloaded / state.names_received) * 100))
        : 0;
    document.getElementById("progress-fill").style.width = `${percent}%`;

    const blockedLine = document.getElementById("blocked-line");
    if (state.status === "blocked" && state.unblock_at) {
        blockedLine.classList.remove("hidden");
        document.getElementById("unblock-at").textContent = state.unblock_at_nsk || "-";
        const remainingMs = new Date(state.unblock_at) - new Date();
        document.getElementById("unblock-countdown").textContent = formatCountdown(remainingMs);
    } else {
        blockedLine.classList.add("hidden");
    }

    const errorLine = document.getElementById("error-line");
    if (state.last_error) {
        errorLine.classList.remove("hidden");
        document.getElementById("error-text").textContent = state.last_error;
    } else {
        errorLine.classList.add("hidden");
    }

    document.getElementById("log-box").textContent = state.log.join("\n");
}

function formatCountdown(remainingMs) {
    if (remainingMs <= 0) {
        return "00:00";
    }
    const totalSeconds = Math.floor(remainingMs / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// Страница "/files" — список, сортировка, пагинация, выбор и расчёты.
// ---------------------------------------------------------------------------

function initFilesPage() {
    const state = {
        page: 1,
        perPage: 20,
        sort: "desc",
        total: 0,
        // "ids" — точечный выбор (selectedNames), "page" — вся текущая страница,
        // "all" — вообще все файлы (резолвится на бэкенде).
        selectionMode: "ids",
        selectedNames: new Set(),
    };

    document.getElementById("sort-toggle").addEventListener("click", () => {
        state.sort = state.sort === "desc" ? "asc" : "desc";
        state.page = 1;
        loadFiles(state);
    });

    document.getElementById("select-page-checkbox").addEventListener("change", (e) => {
        const checkboxes = document.querySelectorAll("#files-tbody input[type=checkbox]");
        if (e.target.checked) {
            state.selectionMode = "page";
            state.selectedNames = new Set(Array.from(checkboxes).map((cb) => cb.dataset.name));
            checkboxes.forEach((cb) => { cb.checked = true; });
        } else {
            state.selectionMode = "ids";
            state.selectedNames.clear();
            checkboxes.forEach((cb) => { cb.checked = false; });
        }
        updateSelectionSummary(state);
    });

    document.getElementById("select-all-everywhere").addEventListener("click", () => {
        state.selectionMode = "all";
        const checkboxes = document.querySelectorAll("#files-tbody input[type=checkbox]");
        checkboxes.forEach((cb) => { cb.checked = true; });
        document.getElementById("select-page-checkbox").checked = true;
        updateSelectionSummary(state);
    });

    document.getElementById("clear-selection").addEventListener("click", () => {
        state.selectionMode = "ids";
        state.selectedNames.clear();
        document.querySelectorAll("#files-tbody input[type=checkbox]").forEach((cb) => { cb.checked = false; });
        document.getElementById("select-page-checkbox").checked = false;
        updateSelectionSummary(state);
    });

    document.getElementById("compute-btn").addEventListener("click", () => computeStats(state));

    loadFiles(state);
}

async function loadFiles(state) {
    let data;
    try {
        data = await apiFetch(`/api/files?page=${state.page}&per_page=${state.perPage}&sort=${state.sort}`);
        clearGlobalError();
    } catch (e) {
        showGlobalError(e.message);
        return;
    }

    state.total = data.total;

    // Смена страницы/сортировки сбрасывает точечный выбор с предыдущей страницы —
    // это осознанное упрощение для тестового задания (не накапливаем выбор
    // между страницами в постоянном хранилище на фронте).
    if (state.selectionMode !== "all") {
        state.selectionMode = "ids";
        state.selectedNames.clear();
    }
    document.getElementById("select-page-checkbox").checked = state.selectionMode === "all";

    document.getElementById("sort-arrow").innerHTML = state.sort === "desc" ? "&#9660;" : "&#9650;";

    const tbody = document.getElementById("files-tbody");
    tbody.innerHTML = "";
    for (const item of data.items) {
        const tr = document.createElement("tr");

        const cbTd = document.createElement("td");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.dataset.name = item.name;
        cb.checked = state.selectionMode === "all";
        cb.addEventListener("change", () => {
            state.selectionMode = "ids";
            if (cb.checked) {
                state.selectedNames.add(item.name);
            } else {
                state.selectedNames.delete(item.name);
            }
            document.getElementById("select-page-checkbox").checked = false;
            updateSelectionSummary(state);
        });
        cbTd.appendChild(cb);
        tr.appendChild(cbTd);

        const nameTd = document.createElement("td");
        nameTd.textContent = item.name;
        tr.appendChild(nameTd);

        const dateTd = document.createElement("td");
        dateTd.textContent = item.downloaded_at_nsk;
        tr.appendChild(dateTd);

        tbody.appendChild(tr);
    }

    renderPagination(state);
    updateSelectionSummary(state);
}

function renderPagination(state) {
    const container = document.getElementById("pagination");
    container.innerHTML = "";

    const totalPages = Math.max(1, Math.ceil(state.total / state.perPage));

    const prevBtn = document.createElement("button");
    prevBtn.textContent = "< Назад";
    prevBtn.disabled = state.page <= 1;
    prevBtn.addEventListener("click", () => {
        state.page -= 1;
        loadFiles(state);
    });
    container.appendChild(prevBtn);

    const info = document.createElement("span");
    info.textContent = ` Страница ${state.page} из ${totalPages} (всего файлов: ${state.total}) `;
    container.appendChild(info);

    const nextBtn = document.createElement("button");
    nextBtn.textContent = "Вперёд >";
    nextBtn.disabled = state.page >= totalPages;
    nextBtn.addEventListener("click", () => {
        state.page += 1;
        loadFiles(state);
    });
    container.appendChild(nextBtn);
}

function updateSelectionSummary(state) {
    const el = document.getElementById("selection-summary");
    if (state.selectionMode === "all") {
        el.textContent = "Выбрано: вообще все скачанные файлы";
    } else if (state.selectionMode === "page") {
        el.textContent = `Выбрано: все файлы этой страницы (${state.selectedNames.size})`;
    } else {
        el.textContent = `Выбрано файлов: ${state.selectedNames.size}`;
    }
}

async function computeStats(state) {
    let body;
    if (state.selectionMode === "all") {
        body = { mode: "all" };
    } else if (state.selectionMode === "page") {
        // sort обязателен: без него бэкенд соберёт страницу в другом порядке,
        // чем видит пользователь, и посчитает статистику не по тем файлам.
        body = { mode: "page", page: state.page, per_page: state.perPage, sort: state.sort };
    } else {
        body = { mode: "ids", names: Array.from(state.selectedNames) };
    }

    let result;
    try {
        result = await apiFetch("/api/stats", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        clearGlobalError();
    } catch (e) {
        showGlobalError(e.message);
        return;
    }

    renderStats(result);
}

function renderStats(result) {
    document.getElementById("stats-section").classList.remove("hidden");

    const skippedBox = document.getElementById("stats-skipped");
    const skippedList = document.getElementById("skipped-list");
    skippedList.innerHTML = "";
    if (result.skipped && result.skipped.length > 0) {
        skippedBox.classList.remove("hidden");
        for (const s of result.skipped) {
            const li = document.createElement("li");
            li.textContent = `${s.name} — ${s.reason}`;
            skippedList.appendChild(li);
        }
    } else {
        skippedBox.classList.add("hidden");
    }

    const totalBody = document.getElementById("total-stats-body");
    totalBody.innerHTML = "";
    const totalChars = result.total_chars || 0;
    for (const digit of "0123456789") {
        const count = result.total_counts[digit] || 0;
        const percent = totalChars > 0 ? ((count / totalChars) * 100).toFixed(2) : "0.00";
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${digit}</td><td>${count}</td><td>${percent}%</td>`;
        totalBody.appendChild(tr);
    }

    const perFileBody = document.getElementById("per-file-stats-body");
    perFileBody.innerHTML = "";
    for (const file of result.files) {
        const tr = document.createElement("tr");
        let cells = `<td>${file.name}</td>`;
        for (const digit of "0123456789") {
            cells += `<td>${file.counts[digit] || 0}</td>`;
        }
        cells += `<td>${file.total}</td>`;
        tr.innerHTML = cells;
        perFileBody.appendChild(tr);
    }
}
