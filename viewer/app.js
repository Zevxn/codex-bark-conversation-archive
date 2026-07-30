(function () {
  "use strict";

  const MONTHLY_FILE_PATTERN = /^Codex对话记录_(\d{4})-(\d{2})\.json$/u;
  const INDEX_FILE_NAME = "Codex对话索引.json";
  const DB_NAME = "codex-conversation-archive-viewer";
  const DB_VERSION = 1;
  const SOURCE_STORE = "sources";
  const PREF_STORE = "preferences";
  const PROJECT_COLORS = ["#2563eb", "#7c3aed", "#0891b2", "#0f9f6e", "#d97706", "#dc5a5a", "#db2777", "#4f67d8"];

  const state = {
    records: [],
    projects: new Map(),
    sessionIndex: new Map(),
    recentSources: [],
    currentSource: null,
    selectedProject: "all",
    selectedYear: new Date().getFullYear(),
    selectedDate: null,
    search: "",
    invalidFiles: [],
    sourceFiles: [],
    sidebarCollapsed: false
  };

  const dom = {};
  const customSelects = new Map();
  let databasePromise;
  let noticeTimer;
  let toastTimer;
  let searchTimer;

  document.addEventListener("DOMContentLoaded", initialize);

  async function initialize() {
    for (const id of [
      "appShell", "sidebarToggle", "sourceSummary", "refreshButton", "openFilesButton", "openDirectoryButton", "fileInput",
      "directoryInput", "notice", "recentSources", "forgetSourceButton", "projectCount",
      "allProjectsButton", "projectList", "welcomeView", "dashboardView", "welcomeDirectoryButton",
      "welcomeFilesButton", "directorySupportHint", "dashboardTitle", "searchInput",
      "yearSelect", "turnStat", "turnStatHint", "sessionStat", "activeDayStat", "activeDayHint",
      "durationStat", "calendarHeading", "monthLabels", "heatmap", "dateScopeLabel", "selectedDateHeading",
      "selectedDateSummary", "expandAllButton", "collapseAllButton", "recordGroups", "tooltip", "toast"
    ]) {
      dom[id] = document.getElementById(id);
    }

    restoreSidebarState();
    initializeCustomSelects();
    bindEvents();
    updateSupportHint();
    await refreshRecentSources();
    await tryLoadConfiguredArchive();
  }

  function bindEvents() {
    dom.sidebarToggle.addEventListener("click", toggleSidebar);
    dom.openDirectoryButton.addEventListener("click", chooseDirectory);
    dom.welcomeDirectoryButton.addEventListener("click", chooseDirectory);
    dom.openFilesButton.addEventListener("click", chooseFiles);
    dom.welcomeFilesButton.addEventListener("click", chooseFiles);
    dom.refreshButton.addEventListener("click", () => {
      if (state.currentSource) loadSource(state.currentSource, true);
    });
    dom.fileInput.addEventListener("change", handleFallbackFiles);
    dom.directoryInput.addEventListener("change", handleFallbackDirectory);
    dom.recentSources.addEventListener("change", handleRecentSourceChange);
    dom.forgetSourceButton.addEventListener("click", forgetSelectedSource);
    dom.allProjectsButton.addEventListener("click", () => selectProject("all"));
    dom.yearSelect.addEventListener("change", () => {
      state.selectedYear = Number(dom.yearSelect.value);
      if (state.selectedDate !== "all" && !state.selectedDate?.startsWith(`${state.selectedYear}-`)) {
        state.selectedDate = mostRecentActiveDate(getFilteredRecords(), state.selectedYear);
      }
      renderDashboard();
    });
    dom.searchInput.addEventListener("input", () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        state.search = dom.searchInput.value.trim().toLocaleLowerCase("zh-CN");
        state.selectedDate = mostRecentActiveDate(getFilteredRecords(), state.selectedYear);
        renderDashboard();
      }, 180);
    });
    dom.expandAllButton.addEventListener("click", () => {
      for (const details of dom.recordGroups.querySelectorAll("details.conversation-card")) {
        details.open = true;
        renderConversationIfNeeded(details);
      }
    });
    dom.collapseAllButton.addEventListener("click", () => {
      for (const details of dom.recordGroups.querySelectorAll("details.conversation-card")) details.open = false;
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "")) {
        event.preventDefault();
        dom.searchInput.focus();
      }
    });
  }

  function restoreSidebarState() {
    try {
      state.sidebarCollapsed = localStorage.getItem("codex-archive-sidebar-collapsed") === "1";
    } catch {
      state.sidebarCollapsed = false;
    }
    applySidebarState();
  }

  function toggleSidebar() {
    state.sidebarCollapsed = !state.sidebarCollapsed;
    try {
      localStorage.setItem("codex-archive-sidebar-collapsed", state.sidebarCollapsed ? "1" : "0");
    } catch {
      // The layout still works when browser storage is unavailable.
    }
    applySidebarState();
  }

  function applySidebarState() {
    dom.appShell.classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
    dom.sidebarToggle.setAttribute("aria-expanded", String(!state.sidebarCollapsed));
    dom.sidebarToggle.setAttribute("aria-label", state.sidebarCollapsed ? "展开项目侧边栏" : "收起项目侧边栏");
    dom.sidebarToggle.title = state.sidebarCollapsed ? "展开侧边栏" : "收起侧边栏";
  }

  function initializeCustomSelects() {
    for (const root of document.querySelectorAll(".custom-select[data-select-id]")) {
      const select = document.getElementById(root.dataset.selectId);
      const trigger = root.querySelector(".custom-select-trigger");
      const value = root.querySelector(".custom-select-value");
      const menu = root.querySelector(".custom-select-menu");
      if (!select || !trigger || !value || !menu) continue;

      const component = { root, select, trigger, value, menu };
      customSelects.set(select.id, component);
      const menuId = `${select.id}-custom-menu`;
      menu.id = menuId;
      trigger.setAttribute("aria-controls", menuId);

      trigger.addEventListener("click", () => {
        if (root.classList.contains("open")) closeCustomSelect(component, true);
        else openCustomSelect(component);
      });
      trigger.addEventListener("keydown", (event) => {
        if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
          event.preventDefault();
          openCustomSelect(component, event.key === "ArrowUp" ? -1 : 1);
        }
        if (event.key === "Escape") closeCustomSelect(component, true);
      });
      menu.addEventListener("keydown", (event) => handleCustomSelectMenuKeydown(event, component));
      root.addEventListener("focusout", () => {
        window.setTimeout(() => {
          if (!root.contains(document.activeElement)) closeCustomSelect(component, false);
        }, 0);
      });
      refreshCustomSelect(select.id);
    }

    document.addEventListener("pointerdown", (event) => {
      for (const component of customSelects.values()) {
        if (!component.root.contains(event.target)) closeCustomSelect(component, false);
      }
    });
  }

  function refreshCustomSelect(selectId) {
    const component = customSelects.get(selectId);
    if (!component) return;
    const { select, value, menu } = component;
    const selectedOption = select.selectedOptions[0] || select.options[0];
    value.textContent = selectedOption?.textContent || "—";
    value.title = selectedOption?.textContent || "";
    menu.replaceChildren();

    for (const option of select.options) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "custom-select-option";
      button.dataset.value = option.value;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", String(option.value === select.value));
      button.classList.toggle("selected", option.value === select.value);
      button.disabled = option.disabled;
      const label = document.createElement("span");
      label.className = "custom-select-option-label";
      label.textContent = option.textContent;
      button.append(label);
      button.addEventListener("click", () => selectCustomOption(component, option.value));
      menu.append(button);
    }
  }

  function openCustomSelect(component, direction = 1) {
    for (const other of customSelects.values()) {
      if (other !== component) closeCustomSelect(other, false);
    }
    refreshCustomSelect(component.select.id);
    component.root.classList.add("open");
    component.trigger.setAttribute("aria-expanded", "true");
    component.menu.hidden = false;
    const options = enabledCustomOptions(component);
    if (!options.length) return;
    const selectedIndex = options.findIndex((option) => option.classList.contains("selected"));
    const fallbackIndex = direction < 0 ? options.length - 1 : 0;
    options[selectedIndex >= 0 ? selectedIndex : fallbackIndex].focus();
  }

  function closeCustomSelect(component, restoreFocus) {
    if (!component.root.classList.contains("open")) return;
    component.root.classList.remove("open");
    component.trigger.setAttribute("aria-expanded", "false");
    component.menu.hidden = true;
    if (restoreFocus) component.trigger.focus();
  }

  function selectCustomOption(component, nextValue) {
    const changed = component.select.value !== nextValue;
    component.select.value = nextValue;
    refreshCustomSelect(component.select.id);
    closeCustomSelect(component, true);
    if (changed) component.select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function enabledCustomOptions(component) {
    return Array.from(component.menu.querySelectorAll(".custom-select-option:not(:disabled)"));
  }

  function handleCustomSelectMenuKeydown(event, component) {
    const options = enabledCustomOptions(component);
    const currentIndex = options.indexOf(document.activeElement);
    let nextIndex = currentIndex;
    if (event.key === "ArrowDown") nextIndex = Math.min(options.length - 1, currentIndex + 1);
    else if (event.key === "ArrowUp") nextIndex = Math.max(0, currentIndex - 1);
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = options.length - 1;
    else if (event.key === "Escape") {
      event.preventDefault();
      closeCustomSelect(component, true);
      return;
    } else return;
    event.preventDefault();
    options[nextIndex]?.focus();
  }

  function updateSupportHint() {
    if ("showDirectoryPicker" in window) {
      dom.directorySupportHint.textContent = "Chrome / Edge 会在首次读取时请求目录权限，并可记住最近选择。";
      return;
    }
    dom.directorySupportHint.textContent = "当前浏览器不支持持久目录句柄；仍可选择目录，但重启后需要重新选择。";
  }

  function openDatabase() {
    if (databasePromise) return databasePromise;
    databasePromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(SOURCE_STORE)) db.createObjectStore(SOURCE_STORE, { keyPath: "id" });
        if (!db.objectStoreNames.contains(PREF_STORE)) db.createObjectStore(PREF_STORE, { keyPath: "key" });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    return databasePromise;
  }

  async function dbGetAll(storeName) {
    const db = await openDatabase();
    return new Promise((resolve, reject) => {
      const request = db.transaction(storeName, "readonly").objectStore(storeName).getAll();
      request.onsuccess = () => resolve(request.result || []);
      request.onerror = () => reject(request.error);
    });
  }

  async function dbGet(storeName, key) {
    const db = await openDatabase();
    return new Promise((resolve, reject) => {
      const request = db.transaction(storeName, "readonly").objectStore(storeName).get(key);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async function dbPut(storeName, value) {
    const db = await openDatabase();
    return new Promise((resolve, reject) => {
      const request = db.transaction(storeName, "readwrite").objectStore(storeName).put(value);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });
  }

  async function dbDelete(storeName, key) {
    const db = await openDatabase();
    return new Promise((resolve, reject) => {
      const request = db.transaction(storeName, "readwrite").objectStore(storeName).delete(key);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });
  }

  async function refreshRecentSources() {
    try {
      state.recentSources = (await dbGetAll(SOURCE_STORE)).sort((a, b) => b.lastUsed - a.lastUsed);
    } catch (error) {
      console.warn("无法读取最近数据源", error);
      state.recentSources = [];
    }
    renderRecentSources();
  }

  function renderRecentSources() {
    dom.recentSources.replaceChildren();
    if (!state.recentSources.length) {
      dom.recentSources.append(new Option("暂无最近数据源", ""));
      dom.forgetSourceButton.disabled = true;
      refreshCustomSelect("recentSources");
      return;
    }
    dom.recentSources.append(new Option("选择最近的数据源…", ""));
    for (const source of state.recentSources) {
      dom.recentSources.append(new Option(source.name, source.id));
    }
    if (state.currentSource?.id && state.recentSources.some((source) => source.id === state.currentSource.id)) {
      dom.recentSources.value = state.currentSource.id;
    }
    dom.forgetSourceButton.disabled = !dom.recentSources.value;
    refreshCustomSelect("recentSources");
  }

  async function tryLoadConfiguredArchive() {
    try {
      const payload = await requestConfiguredArchive();
      if (!payload?.files?.length) return false;
      const source = {
        id: "configured-archive",
        kind: "server-configured",
        name: payload.source_name || "配置的对话归档",
        prefetchedFiles: payload.files
      };
      state.currentSource = source;
      return await loadSource(source, false);
    } catch (error) {
      console.info("未自动加载配置的对话归档", error);
      return false;
    }
  }

  async function requestConfiguredArchive() {
    const response = await fetch("/api/configured-archive", {
      cache: "no-store",
      headers: { Accept: "application/json" }
    });
    if (response.status === 204 || response.status === 404) return null;
    if (!response.ok) throw new Error(`自动归档接口返回 ${response.status}`);
    const payload = await response.json();
    return payload && Array.isArray(payload.files) ? payload : null;
  }

  async function chooseDirectory() {
    if (!("showDirectoryPicker" in window)) {
      dom.directoryInput.value = "";
      dom.directoryInput.click();
      return;
    }
    try {
      const handle = await window.showDirectoryPicker({ id: "codex-conversation-archive", mode: "read" });
      const source = await rememberHandleSource({
        kind: "directory",
        name: handle.name,
        handle
      });
      await loadSource(source, true);
    } catch (error) {
      if (error?.name !== "AbortError") showNotice(`无法打开目录：${error.message || error}`, true);
    }
  }

  async function chooseFiles() {
    if (!("showOpenFilePicker" in window)) {
      dom.fileInput.value = "";
      dom.fileInput.click();
      return;
    }
    try {
      const handles = await window.showOpenFilePicker({
        id: "codex-conversation-json",
        multiple: true,
        types: [{ description: "JSON 对话记录", accept: { "application/json": [".json"] } }]
      });
      const source = await rememberHandleSource({
        kind: "files",
        name: summarizeFileNames(handles.map((handle) => handle.name)),
        handles
      });
      await loadSource(source, true);
    } catch (error) {
      if (error?.name !== "AbortError") showNotice(`无法打开 JSON：${error.message || error}`, true);
    }
  }

  async function rememberHandleSource(candidate) {
    let source;
    if (candidate.kind === "directory") {
      for (const existing of state.recentSources.filter((item) => item.kind === "directory")) {
        try {
          if (await existing.handle.isSameEntry(candidate.handle)) {
            source = { ...existing, ...candidate, lastUsed: Date.now() };
            break;
          }
        } catch {
          // Ignore stale handles and create a fresh recent-source entry.
        }
      }
    }
    source ||= {
      ...candidate,
      id: crypto.randomUUID?.() || `source-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      lastUsed: Date.now()
    };
    await dbPut(SOURCE_STORE, source);
    await dbPut(PREF_STORE, { key: "currentSourceId", value: source.id });
    await pruneRecentSources(source.id);
    await refreshRecentSources();
    state.currentSource = source;
    renderRecentSources();
    return source;
  }

  async function pruneRecentSources(keepId) {
    const sources = (await dbGetAll(SOURCE_STORE)).sort((a, b) => b.lastUsed - a.lastUsed);
    for (const source of sources.slice(8)) {
      if (source.id !== keepId) await dbDelete(SOURCE_STORE, source.id);
    }
  }

  async function handleFallbackFiles(event) {
    const files = Array.from(event.target.files || []);
    if (!files.length) return;
    const source = {
      id: `temporary-${Date.now()}`,
      kind: "temporary-files",
      name: summarizeFileNames(files.map((file) => file.name)),
      files
    };
    state.currentSource = source;
    await loadSource(source, true);
  }

  async function handleFallbackDirectory(event) {
    const files = Array.from(event.target.files || []);
    if (!files.length) return;
    const rootName = files[0].webkitRelativePath?.split("/")[0] || "所选目录";
    const source = {
      id: `temporary-${Date.now()}`,
      kind: "temporary-directory",
      name: rootName,
      files
    };
    state.currentSource = source;
    await loadSource(source, true);
  }

  async function handleRecentSourceChange() {
    const source = state.recentSources.find((item) => item.id === dom.recentSources.value);
    dom.forgetSourceButton.disabled = !source;
    if (!source) return;
    state.currentSource = source;
    await loadSource(source, true);
  }

  async function forgetSelectedSource() {
    const id = dom.recentSources.value;
    if (!id) return;
    await dbDelete(SOURCE_STORE, id);
    const preference = await dbGet(PREF_STORE, "currentSourceId");
    if (preference?.value === id) await dbDelete(PREF_STORE, "currentSourceId");
    await refreshRecentSources();
    showToast("已从最近数据源中移除");
  }

  async function ensureSourcePermission(source, interactive) {
    const handles = source.kind === "directory" ? [source.handle] : source.handles || [];
    for (const handle of handles) {
      if (!handle?.queryPermission) continue;
      let permission = await handle.queryPermission({ mode: "read" });
      if (permission !== "granted" && interactive && handle.requestPermission) {
        permission = await handle.requestPermission({ mode: "read" });
      }
      if (permission !== "granted") return false;
    }
    return true;
  }

  async function loadSource(source, interactive) {
    setBusy(true, `正在读取 ${source.name}…`);
    try {
      if (["directory", "files"].includes(source.kind)) {
        if (!(await ensureSourcePermission(source, interactive))) {
          dom.sourceSummary.textContent = `等待授权：${source.name}`;
          if (interactive) showNotice("未获得数据源读取权限。你可以重试，或选择新的目录。", true);
          return false;
        }
      }
      const archiveFiles = await readSourceFiles(source);
      const parsed = parseArchiveFiles(archiveFiles);
      if (!parsed.records.length) {
        throw new Error("没有找到可用的月度对话记录。请选择包含 Codex对话记录_YYYY-MM.json 的目录或文件。");
      }

      state.currentSource = source;
      state.sourceFiles = archiveFiles;
      state.invalidFiles = parsed.invalidFiles;
      state.sessionIndex = parsed.sessionIndex;
      state.records = normalizeRecords(parsed.records, parsed.sessionIndex);
      state.projects = buildProjects(state.records);
      state.selectedProject = "all";
      state.search = "";
      dom.searchInput.value = "";
      state.selectedYear = latestYear(state.records);
      state.selectedDate = mostRecentActiveDate(state.records, state.selectedYear);

      if (["directory", "files"].includes(source.kind)) {
        source.lastUsed = Date.now();
        await dbPut(SOURCE_STORE, source);
        await dbPut(PREF_STORE, { key: "currentSourceId", value: source.id });
        await refreshRecentSources();
      }

      renderProjectList();
      renderYearOptions();
      showDashboard();
      renderDashboard();
      dom.refreshButton.disabled = false;
      const warning = parsed.invalidFiles.length ? `；${parsed.invalidFiles.length} 个文件未能读取` : "";
      dom.sourceSummary.textContent = `${source.name} · ${state.records.length.toLocaleString("zh-CN")} 轮问答${warning}`;
      showNotice(`已载入 ${state.records.length.toLocaleString("zh-CN")} 轮问答，来自 ${parsed.monthlyFileCount} 个月度文件${warning}`,
        parsed.invalidFiles.length > 0);
      return true;
    } catch (error) {
      console.error(error);
      showNotice(error.message || String(error), true, 7000);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function readSourceFiles(source) {
    if (source.kind === "server-configured") {
      if (Array.isArray(source.prefetchedFiles)) {
        const files = source.prefetchedFiles;
        delete source.prefetchedFiles;
        return files;
      }
      const payload = await requestConfiguredArchive();
      if (!payload?.files?.length) throw new Error("配置的对话归档目录当前不可用，请手动选择记录目录。");
      source.name = payload.source_name || source.name;
      return payload.files;
    }
    if (source.kind === "directory") {
      const entries = [];
      for await (const [name, handle] of source.handle.entries()) {
        if (handle.kind !== "file" || !isArchiveFileName(name)) continue;
        const file = await handle.getFile();
        entries.push({ name, text: await file.text(), lastModified: file.lastModified });
      }
      return entries;
    }
    if (source.kind === "files") {
      return Promise.all(source.handles.filter((handle) => isArchiveFileName(handle.name)).map(async (handle) => {
        const file = await handle.getFile();
        return { name: file.name, text: await file.text(), lastModified: file.lastModified };
      }));
    }
    return Promise.all((source.files || []).filter((file) => isArchiveFileName(file.name)).map(async (file) => ({
      name: file.name,
      text: await file.text(),
      lastModified: file.lastModified
    })));
  }

  function isArchiveFileName(name) {
    return MONTHLY_FILE_PATTERN.test(name) || name === INDEX_FILE_NAME;
  }

  function parseArchiveFiles(files) {
    const invalidFiles = [];
    const records = [];
    const sessionIndex = new Map();
    let monthlyFileCount = 0;

    const ordered = [...files].sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
    for (const file of ordered) {
      try {
        const document = JSON.parse(file.text.replace(/^\uFEFF/, ""));
        if (file.name === INDEX_FILE_NAME) {
          const sessions = document?.sessions;
          if (sessions && typeof sessions === "object") {
            for (const [sessionId, value] of Object.entries(sessions)) {
              if (value && typeof value === "object") sessionIndex.set(sessionId, value);
            }
          }
          continue;
        }
        if (!MONTHLY_FILE_PATTERN.test(file.name)) continue;
        const fileRecords = Array.isArray(document) ? document : document?.records;
        if (!Array.isArray(fileRecords)) throw new Error("缺少 records 数组");
        monthlyFileCount += 1;
        for (const record of fileRecords) {
          if (record && typeof record === "object" && !Array.isArray(record)) records.push(record);
        }
      } catch (error) {
        invalidFiles.push({ name: file.name, message: error.message || String(error) });
      }
    }

    return { records, sessionIndex, invalidFiles, monthlyFileCount };
  }

  function normalizeRecords(inputRecords, sessionIndex) {
    const deduplicated = new Map();
    for (let index = 0; index < inputRecords.length; index += 1) {
      const raw = inputRecords[index];
      const sessionId = cleanText(raw.session_id) || `unknown-session-${index}`;
      const turnId = cleanText(raw.turn_id) || `unknown-turn-${index}`;
      const recordId = cleanText(raw.record_id) || `${sessionId}:${turnId}`;
      const indexed = sessionIndex.get(sessionId) || {};
      const merged = deduplicated.has(recordId) ? mergeDuplicateRecords(deduplicated.get(recordId), raw) : { ...raw };
      merged.record_id = recordId;
      merged.session_id = sessionId;
      merged.turn_id = turnId;
      merged.user_prompt = cleanMultiline(merged.user_prompt);
      merged.assistant_response = cleanMultiline(merged.assistant_response);
      merged.conversation_title = cleanText(merged.conversation_title) || cleanText(indexed.conversation_title) || titleFromPrompt(merged.user_prompt);
      merged.cwd = cleanText(merged.cwd) || cleanText(indexed.cwd);
      merged.project = cleanText(merged.project) || cleanText(indexed.project) || projectNameFromCwd(merged.cwd) || "未分类项目";
      merged._projectKey = projectKey(merged.cwd, merged.project);
      merged._dateKey = dateKeyFromRecord(merged);
      merged._timestamp = timestampFromRecord(merged);
      merged._searchText = [merged.project, merged.cwd, merged.conversation_title, merged.user_prompt, merged.assistant_response, merged.model]
        .filter(Boolean).join("\n").toLocaleLowerCase("zh-CN");
      deduplicated.set(recordId, merged);
    }
    return Array.from(deduplicated.values()).sort((a, b) => a._timestamp - b._timestamp || a.record_id.localeCompare(b.record_id));
  }

  function mergeDuplicateRecords(existing, incoming) {
    const merged = { ...existing, ...incoming };
    if (existing.prompt_status === "matched" && incoming.prompt_status === "missing") {
      for (const key of ["prompt_status", "prompt_time", "duration_seconds", "user_prompt"]) merged[key] = existing[key];
    }
    if (!cleanText(incoming.conversation_title)) merged.conversation_title = existing.conversation_title;
    return merged;
  }

  function buildProjects(records) {
    const projects = new Map();
    for (const record of records) {
      let project = projects.get(record._projectKey);
      if (!project) {
        project = {
          key: record._projectKey,
          nameCounts: new Map(),
          cwd: record.cwd,
          records: [],
          sessions: new Set(),
          latest: 0,
          color: PROJECT_COLORS[hashString(record._projectKey) % PROJECT_COLORS.length]
        };
        projects.set(project.key, project);
      }
      const name = record.project || "未分类项目";
      project.nameCounts.set(name, (project.nameCounts.get(name) || 0) + 1);
      project.records.push(record);
      project.sessions.add(record.session_id);
      project.latest = Math.max(project.latest, record._timestamp);
    }
    for (const project of projects.values()) {
      project.name = Array.from(project.nameCounts.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh-CN"))[0][0];
    }
    return projects;
  }

  function projectKey(cwd, project) {
    const normalizedCwd = cleanText(cwd).replace(/\\/g, "/").replace(/\/+$/, "").toLocaleLowerCase("zh-CN");
    return normalizedCwd ? `cwd:${normalizedCwd}` : `name:${cleanText(project).toLocaleLowerCase("zh-CN")}`;
  }

  function projectNameFromCwd(cwd) {
    return cleanText(cwd).replace(/\\/g, "/").replace(/\/+$/, "").split("/").pop() || "";
  }

  function titleFromPrompt(prompt) {
    for (const rawLine of cleanMultiline(prompt).split("\n")) {
      const line = rawLine.replace(/^(?:[#>*+-]+\s*|\d+[.)、]\s*)+/, "").trim();
      if (line) return line.length > 48 ? `${line.slice(0, 47)}…` : line;
    }
    return "未命名对话";
  }

  function dateKeyFromRecord(record) {
    for (const value of [record.prompt_time, record.response_time]) {
      const text = cleanText(value);
      const match = text.match(/^(\d{4}-\d{2}-\d{2})/);
      if (match) return match[1];
      const timestamp = Date.parse(text);
      if (Number.isFinite(timestamp)) return localDateKey(new Date(timestamp));
    }
    return "";
  }

  function timestampFromRecord(record) {
    for (const value of [record.prompt_time, record.response_time]) {
      const parsed = Date.parse(cleanText(value));
      if (Number.isFinite(parsed)) return parsed;
    }
    return 0;
  }

  function cleanText(value) {
    return value == null ? "" : String(value).replace(/\s+/g, " ").trim();
  }

  function cleanMultiline(value) {
    return value == null ? "" : String(value).replace(/\r\n?/g, "\n").trim();
  }

  function summarizeFileNames(names) {
    if (names.length === 1) return names[0];
    const months = names.filter((name) => MONTHLY_FILE_PATTERN.test(name)).length;
    return `${months || names.length} 个对话记录文件`;
  }

  function latestYear(records) {
    const years = records.map((record) => Number(record._dateKey.slice(0, 4))).filter(Number.isFinite);
    return years.length ? Math.max(...years) : new Date().getFullYear();
  }

  function renderYearOptions() {
    const years = Array.from(new Set(state.records.map((record) => Number(record._dateKey.slice(0, 4))).filter(Number.isFinite)))
      .sort((a, b) => b - a);
    dom.yearSelect.replaceChildren(...years.map((year) => new Option(String(year), String(year))));
    dom.yearSelect.value = String(state.selectedYear);
    refreshCustomSelect("yearSelect");
  }

  function renderProjectList() {
    dom.allProjectsButton.classList.toggle("active", state.selectedProject === "all");
    dom.projectCount.textContent = String(state.projects.size);
    dom.projectList.replaceChildren();
    const projects = Array.from(state.projects.values()).sort((a, b) => b.latest - a.latest || a.name.localeCompare(b.name, "zh-CN"));
    for (const project of projects) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "project-item";
      button.dataset.projectKey = project.key;
      button.title = project.cwd || project.name;
      button.style.setProperty("--project-color", project.color);

      const dot = document.createElement("span");
      dot.className = "project-dot";
      dot.textContent = Array.from(project.name)[0]?.toLocaleUpperCase("zh-CN") || "?";
      const name = document.createElement("span");
      name.className = "project-name";
      name.textContent = project.name;
      const count = document.createElement("span");
      count.className = "project-turns";
      count.textContent = String(project.records.length);
      button.append(dot, name, count);
      button.addEventListener("click", () => selectProject(project.key));
      dom.projectList.append(button);
    }
  }

  function selectProject(key) {
    state.selectedProject = key;
    state.selectedDate = mostRecentActiveDate(getFilteredRecords(), state.selectedYear);
    dom.allProjectsButton.classList.toggle("active", key === "all");
    for (const item of dom.projectList.querySelectorAll(".project-item")) {
      item.classList.toggle("active", item.dataset.projectKey === key);
    }
    renderDashboard();
  }

  function getFilteredRecords() {
    return state.records.filter((record) => {
      if (state.selectedProject !== "all" && record._projectKey !== state.selectedProject) return false;
      return !state.search || record._searchText.includes(state.search);
    });
  }

  function showDashboard() {
    dom.welcomeView.hidden = true;
    dom.dashboardView.hidden = false;
  }

  function renderDashboard() {
    const filtered = getFilteredRecords();
    const yearRecords = filtered.filter((record) => record._dateKey.startsWith(`${state.selectedYear}-`));
    const project = state.selectedProject === "all" ? null : state.projects.get(state.selectedProject);
    dom.dashboardTitle.textContent = project?.name || "全部项目";
    dom.turnStat.textContent = yearRecords.length.toLocaleString("zh-CN");
    dom.turnStatHint.textContent = `${state.selectedYear} 年`;
    dom.sessionStat.textContent = new Set(yearRecords.map((record) => record.session_id)).size.toLocaleString("zh-CN");
    dom.activeDayStat.textContent = new Set(yearRecords.map((record) => record._dateKey).filter(Boolean)).size.toLocaleString("zh-CN");
    dom.activeDayHint.textContent = `${state.selectedYear} 年`;
    dom.durationStat.textContent = formatDuration(yearRecords.reduce((total, record) => total + numericDuration(record.duration_seconds), 0));
    dom.calendarHeading.textContent = `${state.selectedYear} 年度活动`;
    renderHeatmap(yearRecords);
    renderSelectedDate(filtered);
  }

  function renderHeatmap(yearRecords) {
    const counts = new Map();
    for (const record of yearRecords) counts.set(record._dateKey, (counts.get(record._dateKey) || 0) + 1);
    const thresholds = heatThresholds(Array.from(counts.values()));
    const first = new Date(state.selectedYear, 0, 1);
    const last = new Date(state.selectedYear, 11, 31);
    const gridStart = addDays(first, -first.getDay());
    const gridEnd = addDays(last, 6 - last.getDay());
    const totalDays = Math.round((gridEnd - gridStart) / 86400000) + 1;
    const totalWeeks = Math.ceil(totalDays / 7);

    dom.heatmap.replaceChildren();
    for (let offset = 0; offset < totalDays; offset += 1) {
      const date = addDays(gridStart, offset);
      const key = localDateKey(date);
      const count = counts.get(key) || 0;
      const button = document.createElement("button");
      button.type = "button";
      button.className = `heatmap-day level-${heatLevel(count, thresholds)}`;
      button.dataset.date = key;
      button.setAttribute("role", "gridcell");
      if (date.getFullYear() !== state.selectedYear) {
        button.classList.add("outside-year");
        button.disabled = true;
        button.tabIndex = -1;
      } else {
        button.setAttribute("aria-label", `${formatDateLong(key)}，${count} 轮问答`);
        button.classList.toggle("selected", key === state.selectedDate);
        if (!count) {
          button.disabled = true;
          button.classList.add("no-activity");
          button.setAttribute("aria-label", `${formatDateLong(key)}，没有对话记录`);
        } else {
          button.addEventListener("click", () => {
            state.selectedDate = state.selectedDate === key ? "all" : key;
            renderDashboard();
            window.setTimeout(() => dom.recordGroups.closest(".records-section")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
          });
          button.addEventListener("pointerenter", (event) => showTooltip(event, key, count));
          button.addEventListener("pointermove", positionTooltip);
          button.addEventListener("pointerleave", hideTooltip);
        }
      }
      dom.heatmap.append(button);
    }

    renderMonthLabels(gridStart, totalWeeks);
  }

  function renderMonthLabels(gridStart, totalWeeks) {
    dom.monthLabels.replaceChildren();
    const monthNames = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"];
    for (let month = 0; month < 12; month += 1) {
      const first = new Date(state.selectedYear, month, 1);
      const week = Math.floor((first - gridStart) / 86400000 / 7);
      const label = document.createElement("span");
      label.className = "month-label";
      label.textContent = monthNames[month];
      label.style.left = `${(week / totalWeeks) * 100}%`;
      dom.monthLabels.append(label);
    }
  }

  function heatThresholds(values) {
    const positive = values.filter((value) => value > 0).sort((a, b) => a - b);
    if (!positive.length) return [1, 2, 3];
    const quantile = (ratio) => positive[Math.min(positive.length - 1, Math.floor((positive.length - 1) * ratio))];
    return [quantile(0.25), quantile(0.5), quantile(0.75)];
  }

  function heatLevel(count, thresholds) {
    if (!count) return 0;
    if (count <= thresholds[0]) return 1;
    if (count <= thresholds[1]) return 2;
    if (count <= thresholds[2]) return 3;
    return 4;
  }

  function renderSelectedDate(filteredRecords) {
    if (state.selectedDate === "all") {
      const projectCount = new Set(filteredRecords.map((record) => record._projectKey)).size;
      const sessionCount = new Set(filteredRecords.map((record) => record.session_id)).size;
      const dates = filteredRecords.map((record) => record._dateKey).filter(Boolean).sort();
      dom.dateScopeLabel.textContent = "全部日期";
      dom.selectedDateHeading.textContent = state.selectedProject === "all" ? "全部历史对话" : "该项目的全部历史对话";
      dom.selectedDateSummary.textContent = `${filteredRecords.length} 轮问答 · ${sessionCount} 个对话 · ${projectCount} 个项目${formatDateRange(dates)}`;
      renderRecordGroups(filteredRecords);
      return;
    }
    if (!state.selectedDate) {
      dom.dateScopeLabel.textContent = "所选日期";
      dom.selectedDateHeading.textContent = "当前年份没有匹配记录";
      dom.selectedDateSummary.textContent = "请切换年份、项目或清除搜索条件";
      dom.recordGroups.innerHTML = '<div class="records-empty">没有可显示的对话记录。</div>';
      return;
    }
    const dayRecords = filteredRecords.filter((record) => record._dateKey === state.selectedDate);
    const projectCount = new Set(dayRecords.map((record) => record._projectKey)).size;
    const sessionCount = new Set(dayRecords.map((record) => record.session_id)).size;
    dom.dateScopeLabel.textContent = "所选日期";
    dom.selectedDateHeading.textContent = formatDateLong(state.selectedDate);
    dom.selectedDateSummary.textContent = `${dayRecords.length} 轮问答 · ${sessionCount} 个对话 · ${projectCount} 个项目`;
    renderRecordGroups(dayRecords);
  }

  function renderRecordGroups(dayRecords) {
    dom.recordGroups.replaceChildren();
    if (!dayRecords.length) {
      const empty = document.createElement("div");
      empty.className = "records-empty";
      empty.textContent = state.search ? "当天没有符合搜索条件的记录。" : "当天没有对话记录。";
      dom.recordGroups.append(empty);
      return;
    }

    const projectGroups = groupBy(dayRecords, (record) => record._projectKey);
    const orderedProjects = Array.from(projectGroups.entries()).sort((a, b) => {
      const latestA = Math.max(...a[1].map((record) => record._timestamp));
      const latestB = Math.max(...b[1].map((record) => record._timestamp));
      return latestB - latestA;
    });

    for (const [projectKeyValue, records] of orderedProjects) {
      const project = state.projects.get(projectKeyValue);
      const section = document.createElement("section");
      section.className = "project-group";
      const heading = document.createElement("div");
      heading.className = "project-group-heading";
      heading.style.setProperty("--project-color", project?.color || PROJECT_COLORS[0]);
      const dot = document.createElement("span");
      dot.className = "project-dot";
      dot.textContent = Array.from(project?.name || records[0].project || "?")[0];
      const title = document.createElement("h4");
      title.textContent = project?.name || records[0].project || "未分类项目";
      const summary = document.createElement("span");
      summary.textContent = `${records.length} 轮 · ${new Set(records.map((record) => record.session_id)).size} 个对话`;
      heading.append(dot, title, summary);
      section.append(heading);

      const sessions = Array.from(groupBy(records, (record) => record.session_id).values())
        .sort((a, b) => Math.max(...b.map((record) => record._timestamp)) - Math.max(...a.map((record) => record._timestamp)));
      for (const sessionRecords of sessions) {
        section.append(createConversationCard(sessionRecords));
      }
      dom.recordGroups.append(section);
    }
  }

  function createConversationCard(records) {
    const ordered = [...records].sort((a, b) => a._timestamp - b._timestamp);
    const latest = ordered[ordered.length - 1];
    const details = document.createElement("details");
    details.className = "conversation-card";
    details.open = false;
    details._records = ordered;
    const summary = document.createElement("summary");
    summary.className = "conversation-summary";

    const titleRow = document.createElement("div");
    titleRow.className = "conversation-title-row";
    const chevron = document.createElement("span");
    chevron.className = "conversation-chevron";
    chevron.textContent = "›";
    const title = document.createElement("span");
    title.className = "conversation-title";
    title.textContent = ordered.find((record) => record.conversation_title)?.conversation_title || "未命名对话";
    titleRow.append(chevron, title);

    const metadata = document.createElement("div");
    metadata.className = "conversation-meta";
    const totalSessionTurns = state.records.filter((record) => record.session_id === latest.session_id).length;
    metadata.textContent = `${ordered.length} 轮（完整对话 ${totalSessionTurns} 轮） · ${formatTime(latest.prompt_time || latest.response_time)}`;
    summary.append(titleRow, metadata);

    const list = document.createElement("div");
    list.className = "turn-list";
    details.append(summary, list);
    details.addEventListener("toggle", () => {
      if (details.open) renderConversationIfNeeded(details);
    });
    return details;
  }

  function renderConversationIfNeeded(details) {
    if (details.dataset.rendered === "true") return;
    details.dataset.rendered = "true";
    const list = details.querySelector(".turn-list");
    const records = details._records || [];
    records.forEach((record, index) => list.append(createTurn(record, index + 1)));
  }

  function createTurn(record, number) {
    const turn = document.createElement("article");
    turn.className = "turn";
    const header = document.createElement("div");
    header.className = "turn-header";
    const numberLabel = document.createElement("span");
    numberLabel.className = "turn-number";
    numberLabel.textContent = `第 ${number} 轮`;
    header.append(numberLabel);
    appendMetaChip(header, formatDateTime(record.prompt_time || record.response_time));
    if (record.model) appendMetaChip(header, record.model);
    if (numericDuration(record.duration_seconds)) appendMetaChip(header, formatDuration(numericDuration(record.duration_seconds)));
    if (record.permission_mode) appendMetaChip(header, record.permission_mode);
    if (record.prompt_status === "missing") appendMetaChip(header, "问题未匹配", true);
    turn.append(header);
    turn.append(createMessage("用户", record.user_prompt || "未获取到用户问题。", "user"));
    turn.append(createMessage("Codex", record.assistant_response || "未获取到助手回答。", "assistant"));
    return turn;
  }

  function appendMetaChip(parent, text, warning = false) {
    const chip = document.createElement("span");
    chip.className = `meta-chip${warning ? " warning" : ""}`;
    chip.textContent = text;
    parent.append(chip);
  }

  function createMessage(labelText, source, role) {
    const message = document.createElement("section");
    message.className = `message message-${role}`;
    const label = document.createElement("div");
    label.className = "message-label";
    const title = document.createElement("span");
    title.textContent = labelText;
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "copy-message-button";
    copy.textContent = "复制原文";
    copy.addEventListener("click", async () => {
      try {
        await ArchiveMarkdown.copyText(source);
        showToast("已复制原文");
      } catch {
        showToast("复制失败");
      }
    });
    label.append(title, copy);
    const body = document.createElement("div");
    body.className = "markdown-body";
    body.textContent = source;
    message.append(label, body);
    ArchiveMarkdown.renderInto(body, source).catch((error) => {
      console.warn("Markdown 渲染失败", error);
      body.textContent = source;
    });
    return message;
  }

  function groupBy(items, keySelector) {
    const groups = new Map();
    for (const item of items) {
      const key = keySelector(item);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    }
    return groups;
  }

  function mostRecentActiveDate(records, year) {
    return records.map((record) => record._dateKey)
      .filter((key) => key.startsWith(`${year}-`))
      .sort()
      .pop() || null;
  }

  function numericDuration(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : 0;
  }

  function formatDuration(seconds) {
    if (seconds < 60) return `${Math.round(seconds)} 秒`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.round((seconds % 3600) / 60);
    return minutes ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`;
  }

  function formatDateLong(dateKey) {
    const date = parseLocalDate(dateKey);
    return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "short" }).format(date);
  }

  function formatDateRange(sortedDateKeys) {
    if (!sortedDateKeys.length) return "";
    const first = sortedDateKeys[0];
    const last = sortedDateKeys[sortedDateKeys.length - 1];
    return first === last ? ` · ${first}` : ` · ${first} 至 ${last}`;
  }

  function formatDateTime(value) {
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return cleanText(value) || "时间未知";
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
    }).format(date);
  }

  function formatTime(value) {
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "时间未知";
    return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  }

  function parseLocalDate(key) {
    const [year, month, day] = key.split("-").map(Number);
    return new Date(year, month - 1, day);
  }

  function localDateKey(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function addDays(date, amount) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate() + amount);
  }

  function hashString(value) {
    let hash = 2166136261;
    for (const character of value) {
      hash ^= character.codePointAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return Math.abs(hash);
  }

  function showTooltip(event, key, count) {
    const filtered = getFilteredRecords().filter((record) => record._dateKey === key);
    const sessions = new Set(filtered.map((record) => record.session_id)).size;
    const projects = new Set(filtered.map((record) => record._projectKey)).size;
    dom.tooltip.textContent = `${formatDateLong(key)} · ${count} 轮问答 · ${sessions} 个对话 · ${projects} 个项目`;
    dom.tooltip.hidden = false;
    positionTooltip(event);
  }

  function positionTooltip(event) {
    const gap = 12;
    const rect = dom.tooltip.getBoundingClientRect();
    dom.tooltip.style.left = `${Math.min(window.innerWidth - rect.width - 8, event.clientX + gap)}px`;
    dom.tooltip.style.top = `${Math.max(8, event.clientY - rect.height - gap)}px`;
  }

  function hideTooltip() {
    dom.tooltip.hidden = true;
  }

  function showNotice(message, isError = false, duration = 4200) {
    window.clearTimeout(noticeTimer);
    dom.notice.textContent = message;
    dom.notice.classList.toggle("error", isError);
    dom.notice.hidden = false;
    noticeTimer = window.setTimeout(() => { dom.notice.hidden = true; }, duration);
  }

  function showToast(message) {
    window.clearTimeout(toastTimer);
    dom.toast.textContent = message;
    dom.toast.hidden = false;
    toastTimer = window.setTimeout(() => { dom.toast.hidden = true; }, 1800);
  }

  function setBusy(busy, message = "") {
    dom.openDirectoryButton.disabled = busy;
    dom.openFilesButton.disabled = busy;
    dom.welcomeDirectoryButton.disabled = busy;
    dom.welcomeFilesButton.disabled = busy;
    dom.refreshButton.disabled = busy || !state.currentSource;
    if (busy && message) dom.sourceSummary.textContent = message;
  }
})();
