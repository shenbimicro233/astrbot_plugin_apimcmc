/**
 * Minecraft 服务器监控 WebUI
 *
 * 优化变更：
 *   - Store 响应式状态管理，消除分散全局变量
 *   - <template> 模板标签，HTML 结构移出 JS
 *   - DocumentFragment 批量日志渲染，减少 DOM reflow
 *   - 事件委托替代逐个绑定，减少内存开销
 */

import { Store } from './store.js';

const bridge = window.AstrBotPluginPage;
if (!bridge || typeof bridge.ready !== 'function') {
  document.body.innerHTML =
    '<div style="padding:48px;text-align:center;font-size:16px;color:#ef4444;">' +
    '<h2 style="font-size:22px;margin-bottom:12px;">桥接 API 不可用</h2>' +
    '<p>插件页面加载异常，请确保通过 AstrBot WebUI「插件」→「Minecraft服务器监控」进入。</p></div>';
  throw new Error('bridge unavailable');
}

// ── DOM 快捷引用 ──
const $ = (id) => document.getElementById(id);
const $template = (id) => /** @type {HTMLTemplateElement} */ (document.getElementById(id));
const $grid = () => $('cardsGrid');
const $console = () => $('logConsole');

// ── 常量 ──
const LOG_POLL_MS = 3000;
const LOG_DOM_MAX = 300;
const ICONS = {
  success: '<path d="M20 6L9 17l-5-5"></path>',
  error: '<line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line>',
  info: '<circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line>',
};

// ── 全局状态 ──
const state = new Store({
  editingGid: null,
  allCfg: {},
  filter: 'all',
  searchQuery: '',
  logSinceId: 0,
  logFollowBottom: true,
});

let logPollTimer = null;
let logLines = [];       // 仅用于计数裁剪，不驱动渲染
let confirmResolve = null;

// ── 工具函数 ──

function escHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function normalizeLevel(level) {
  const lv = String(level || 'INFO').toUpperCase();
  return lv === 'WARNING' ? 'WARN' : lv;
}

function isErrorLevel(level) {
  return ['WARN', 'ERROR'].includes(normalizeLevel(level));
}

// ── Toast ──

function showToast(msg, type) {
  type = type || 'info';
  const el = $('toast');
  if (!el) return;
  const icon = $('toastIcon');
  const text = $('toastText');
  if (!icon || !text) return;
  text.textContent = msg;
  icon.innerHTML = ICONS[type] || ICONS.info;
  el.className = 'toast toast-' + type + ' show';
  clearTimeout(el._hide);
  el._hide = setTimeout(() => el.classList.remove('show'), 2800);
}

// ── 确认框 ──

function showConfirm(title, text) {
  return new Promise((resolve) => {
    confirmResolve = resolve;
    const ct = $('confirmTitle');
    const cx = $('confirmText');
    if (ct) ct.textContent = title;
    if (cx) cx.textContent = text;
    const co = $('confirmOverlay');
    if (co) co.classList.add('active');
  });
}

function closeConfirm(result) {
  const co = $('confirmOverlay');
  if (co) co.classList.remove('active');
  if (confirmResolve) confirmResolve(result);
  confirmResolve = null;
}

// ── 统计面板 ──

function updateStats() {
  const vals = Object.values(state.get('allCfg'));
  const en = vals.filter(c => c.enabled !== false).length;
  const ja = vals.filter(c => c.server_type === 'java').length;
  const be = vals.filter(c => c.server_type !== 'java').length;
  const st = (id, val) => { const el = $(id); if (el) el.textContent = val; };
  st('statTotal', vals.length);
  st('statEnabled', en);
  st('statJava', ja);
  st('statBedrock', be);
}

// ── 卡片渲染 ──

function showSkeleton() {
  const grid = $grid();
  if (!grid) return;
  const tpl = $template('skeleton-template');
  if (!tpl) return;
  const fragment = document.createDocumentFragment();
  for (let i = 0; i < 4; i++) {
    fragment.appendChild(tpl.content.cloneNode(true));
  }
  grid.innerHTML = '';
  grid.appendChild(fragment);
}

function showEmptyState(templateId) {
  const grid = $grid();
  if (!grid) return;
  const tpl = $template(templateId || 'empty-config-template');
  if (!tpl) return;
  grid.innerHTML = '';
  grid.appendChild(tpl.content.cloneNode(true));
}

/** 用 card-template 克隆 + 数据填充，替代 innerHTML 拼串。 */
function createCardElement(gid, cfg) {
  const tpl = $template('card-template');
  if (!tpl) return null;
  const frag = tpl.content.cloneNode(true);

  const bind = (field) => frag.querySelector(`[data-field="${field}"]`);
  const isJava = cfg.server_type === 'java';

  // 文本绑定
  const nameEl = bind('name');
  if (nameEl) nameEl.textContent = cfg.name || '未命名';

  const subEl = bind('group-sub');
  if (subEl) subEl.textContent = `群号 ${gid}`;

  const badgeEl = bind('status-badge');
  if (badgeEl) {
    const enabled = cfg.enabled !== false;
    badgeEl.textContent = enabled ? '已启用' : '已禁用';
    badgeEl.className = 'badge ' + (enabled ? 'badge-on' : 'badge-off');
  }

  const addrEl = bind('address');
  if (addrEl) addrEl.textContent = `${cfg.server_ip}:${cfg.server_port}`;

  const typeBadge = bind('type-badge');
  if (typeBadge) {
    typeBadge.textContent = isJava ? 'Java' : 'Bedrock';
    typeBadge.className = 'badge ' + (isJava ? 'badge-java' : 'badge-bedrock');
  }

  const apiEl = bind('api-source');
  if (apiEl) {
    apiEl.textContent = cfg.api_source
      ? (cfg.api_source === 'mcstatus' ? 'mcstatus.io' : 'mcmotdapi' + (cfg.mcmotdapi_host ? ' (' + cfg.mcmotdapi_host + ')' : ''))
      : '全局默认';
  }

  const fmtEl = bind('msg-format');
  if (fmtEl) fmtEl.textContent = cfg.simple_mode === true ? '简化' : (cfg.simple_mode === false ? '完整' : '全局默认');

  const hitoEl = bind('hitokoto');
  if (hitoEl) hitoEl.textContent = cfg.use_hitokoto !== false ? '开启' : '关闭';

  const gidEl = bind('group-id');
  if (gidEl) {
    gidEl.textContent = gid;
    gidEl.style.fontFamily = 'monospace';
  }

  // 事件绑定（替代内联 onclick）
  const editBtn = frag.querySelector('[data-action="edit"]');
  if (editBtn) editBtn.addEventListener('click', () => editConfig(gid));

  const delBtn = frag.querySelector('[data-action="delete"]');
  if (delBtn) delBtn.addEventListener('click', () => deleteConfig(gid));

  return frag;
}

function matchFlt(cfg, f) {
  if (f === 'enabled') return cfg.enabled !== false;
  if (f === 'disabled') return cfg.enabled === false;
  if (f === 'java') return cfg.server_type === 'java';
  if (f === 'bedrock') return cfg.server_type !== 'java';
  return true;
}

function matchQ(cfg, gid, query) {
  if (!query) return true;
  const lq = query.toLowerCase();
  return String(gid).toLowerCase().includes(lq)
    || String(cfg.name || '').toLowerCase().includes(lq)
    || String(cfg.server_ip || '').toLowerCase().includes(lq);
}

function renderCards() {
  const grid = $grid();
  if (!grid) return;

  const allCfg = state.get('allCfg');
  const flt = state.get('filter');
  const q = state.get('searchQuery');
  const entries = Object.entries(allCfg).filter(([gid, cfg]) => matchFlt(cfg, flt) && matchQ(cfg, gid, q));

  // 空状态
  if (!entries.length && !Object.keys(allCfg).length) {
    showEmptyState('empty-config-template');
    return;
  }
  if (!entries.length) {
    showEmptyState('empty-match-template');
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const [gid, cfg] of entries) {
    const card = createCardElement(gid, cfg);
    if (card) fragment.appendChild(card);
  }

  grid.innerHTML = '';
  grid.appendChild(fragment);
}

// ── 筛选 / 搜索 ──

const applyFilter = debounce(() => {
  state.set('searchQuery', ($('searchInput').value || '').trim());
  renderCards();
}, 200);

function bindFilters() {
  const si = $('searchInput');
  if (si) si.addEventListener('input', applyFilter);

  // 筛选芯片 — 事件委托
  const toolbar = document.querySelector('.toolbar');
  if (toolbar) {
    toolbar.addEventListener('click', (e) => {
      const chip = e.target.closest('.chip');
      if (!chip) return;
      document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      state.set('filter', chip.dataset.filter);
      renderCards();
    });
  }
}

// ── 数据加载 ──

async function loadTable() {
  showSkeleton();
  try {
    const res = await bridge.apiGet('configs');
    state.set('allCfg', res || {});
    updateStats();
    renderCards();
  } catch (e) {
    showToast('加载配置失败：' + (e.message || e), 'error');
    showEmptyState();
  }
}

// ── 编辑 / 新建 ──

function openAddModal() {
  state.set('editingGid', null);
  const mt = $('modalTitle');
  if (mt) mt.textContent = '添加配置';
  setFormValues({ group_id: '', name: 'Minecraft服务器', ip: '', port: '19132', type: 'bedrock', enabled: true, hitokoto: true, simple_mode: false, api_source: '', mcmotdapi_host: '', mcmotdapi_ssl: true });
  clearFormErrors();
  const mo = $('modalOverlay');
  if (mo) mo.classList.add('active');
  const fg = $('f_group_id');
  if (fg) { fg.disabled = false; fg.focus(); }
  setTimeout(toggleMcmotdapiConfig, 0);
}

function editConfig(gid) {
  const cfg = state.get('allCfg')[gid];
  if (!cfg) return showToast('配置不存在', 'error');
  state.set('editingGid', gid);
  const mt = $('modalTitle');
  if (mt) mt.textContent = '编辑配置 - ' + gid;
  setFormValues({
    group_id: gid, name: cfg.name || '', ip: cfg.server_ip || '',
    port: String(cfg.server_port || ''), type: cfg.server_type || 'bedrock',
    enabled: cfg.enabled !== false, hitokoto: cfg.use_hitokoto !== false,
    simple_mode: cfg.simple_mode === true,
    api_source: cfg.api_source || '', mcmotdapi_host: cfg.mcmotdapi_host || '',
    mcmotdapi_ssl: cfg.mcmotdapi_ssl !== false,
  });
  const fg = $('f_group_id');
  if (fg) fg.disabled = true;
  clearFormErrors();
  const mo = $('modalOverlay');
  if (mo) mo.classList.add('active');
  setTimeout(toggleMcmotdapiConfig, 0);
}

function closeModal() {
  const mo = $('modalOverlay');
  if (mo) mo.classList.remove('active');
  state.set('editingGid', null);
}

function toggleMcmotdapiConfig() {
  const show = $('f_api_source') && $('f_api_source').value === 'mcmotdapi';
  const grp = $('mcmotdapi_config_group');
  if (grp) grp.style.display = show ? '' : 'none';
}

function clearFormErrors() {
  ['err_group_id', 'err_ip', 'err_port'].forEach(id => { const el = $(id); if (el) el.classList.remove('show'); });
}

function setFieldError(id, show) {
  const el = $(id);
  if (el) el.classList.toggle('show', show);
}

function setFormValues(v) {
  const fields = [
    ['f_group_id', v.group_id],
    ['f_name', v.name],
    ['f_ip', v.ip],
    ['f_port', v.port],
    ['f_type', v.type],
    ['f_mcmotdapi_host', v.mcmotdapi_host],
  ];
  fields.forEach(([id, val]) => {
    const el = $(id);
    if (el) { el.value = String(val || ''); el.disabled = false; }
  });
  const f_en = $('f_enabled');
  if (f_en) f_en.checked = v.enabled !== false;
  const f_h = $('f_hitokoto');
  if (f_h) f_h.checked = v.hitokoto !== false;
  const f_simple = $('f_simple_mode');
  if (f_simple) f_simple.checked = v.simple_mode === true;
  const f_ssl = $('f_mcmotdapi_ssl');
  if (f_ssl) f_ssl.checked = v.mcmotdapi_ssl !== false;
  const f_as = $('f_api_source');
  if (f_as) f_as.value = v.api_source || '';
}

// ── 删除 ──

async function deleteConfig(gid) {
  const ok = await showConfirm('确认删除？', `群号 ${gid} 的监控配置将被永久删除。`);
  if (!ok) return;
  try {
    await bridge.apiPost('configs/delete', { group_id: gid });
    showToast('配置已删除', 'success');
    await loadTable();
  } catch (e) {
    showToast('删除失败：' + (e.message || e), 'error');
  }
}

// ── 日志控制台 ──

/** 用 log-line-template 克隆单条日志 DOM，避免 createElement + innerHTML 拼串。 */
function createLogLineElement(entry) {
  const tpl = $template('log-line-template');
  if (!tpl) return null;
  const frag = tpl.content.cloneNode(true);
  const lv = normalizeLevel(entry.level);

  const tsEl = frag.querySelector('[data-field="ts"]');
  if (tsEl) tsEl.textContent = entry.ts || '';

  const lvEl = frag.querySelector('[data-field="level"]');
  if (lvEl) {
    lvEl.textContent = lv;
    lvEl.className = 'lv lv-' + lv;
  }

  const msgEl = frag.querySelector('[data-field="msg"]');
  if (msgEl) msgEl.textContent = entry.msg || '';

  const line = frag.firstElementChild;
  if (line) {
    line.dataset.level = lv;
    line.dataset.id = String(entry.id);
  }

  return frag;
}

function refilterLogDom() {
  const errOnly = $('errorsOnlyChk');
  const eo = errOnly ? errOnly.checked : false;
  const lines = $console().querySelectorAll('.log-line');
  const searchEl = $('logSearch');
  const sq = searchEl ? (searchEl.value || '').trim() : '';
  lines.forEach((el) => {
    const lv = el.dataset.level || 'INFO';
    const matched = !sq || el.textContent.toLowerCase().includes(sq.toLowerCase());
    const visible = (!eo || isErrorLevel(lv)) && matched;
    el.style.display = visible ? '' : 'none';
    el.classList.toggle('log-highlight', matched && sq !== '');
  });
  updateLogCount();
  if (state.get('logFollowBottom')) scrollLogToBottom();
}

function highlightLogSearch() {
  const searchEl = $('logSearch');
  const q = searchEl ? (searchEl.value || '').trim() : '';
  const lines = $console().querySelectorAll('.log-line');
  lines.forEach((el) => {
    if (!q) { el.classList.remove('log-highlight'); return; }
    el.classList.toggle('log-highlight', el.textContent.toLowerCase().includes(q.toLowerCase()));
  });
}

function updateLogCount() {
  const all = $console().querySelectorAll('.log-line').length;
  const visible = Array.from($console().querySelectorAll('.log-line')).filter(el => el.style.display !== 'none').length;
  const errOnly = $('errorsOnlyChk');
  const eo = errOnly ? errOnly.checked : false;
  const searchEl = $('logSearch');
  const searching = searchEl ? (searchEl.value || '').trim() !== '' : false;
  const ct = $('logCountText');
  const ft = $('logFollowText');
  if (ct) ct.textContent = (eo || searching) ? ('显示 ' + visible + ' / 共 ' + all + ' 条') : (all + ' 条');
  if (ft) ft.textContent = state.get('logFollowBottom') ? '跟随最新' : '已暂停跟随（上滚中）';
}

function scrollLogToBottom() {
  const el = $console();
  if (!el) return;
  el.scrollTop = el.scrollHeight;
  state.set('logFollowBottom', true);
  updateLogCount();
}

function onLogScroll() {
  const el = $console();
  if (!el) return;
  const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
  state.set('logFollowBottom', dist < 40);
  updateLogCount();
}

async function pullLogs(initial) {
  try {
    const res = await bridge.apiGet('logs', {
      since_id: state.get('logSinceId'),
      limit: initial ? 200 : 100,
    });
    const data = res || {};
    updateMonitorStatus(data);

    const logs = Array.isArray(data.logs) ? data.logs : [];
    if (logs.length) {
      // DocumentFragment 批量追加，减少 DOM reflow
      const fragment = document.createDocumentFragment();
      let newSinceId = state.get('logSinceId');

      for (const entry of logs) {
        logLines.push(entry);
        const lineEl = createLogLineElement(entry);
        if (lineEl) fragment.appendChild(lineEl);
        if (entry.id > newSinceId) newSinceId = entry.id;
      }

      // 移除空状态提示
      const empty = $('logEmpty');
      if (empty) empty.remove();

      $console().appendChild(fragment);

      // 裁剪
      if (logLines.length > LOG_DOM_MAX) logLines = logLines.slice(-LOG_DOM_MAX);

      state.set('logSinceId', newSinceId);
      if (typeof data.next_id === 'number' && data.next_id > newSinceId) {
        state.set('logSinceId', data.next_id - 1);
      }

      refilterLogDom();
      if (state.get('logFollowBottom')) scrollLogToBottom();
    } else if (initial) {
      updateLogCount();
    }
  } catch (e) {
    const st = $('monitorStatusText');
    if (st) st.textContent = '日志拉取失败';
    const dot = $('monitorDot');
    if (dot) dot.className = 'status-dot off';
  }
}

function updateMonitorStatus(data) {
  const running = !!(data && data.monitor_running);
  const dot = $('monitorDot');
  const text = $('monitorStatusText');
  const meta = $('monitorMetaText');
  if (dot) dot.className = 'status-dot ' + (running ? 'on' : 'off');
  if (text) text.textContent = running ? '监控中' : '未运行';
  const se = $('statMonitor');
  if (se) { se.textContent = running ? '监控中' : '未运行'; se.className = 'stat-value ' + (running ? 'on' : 'off'); }
  const parts = [];
  if (data && data.check_interval != null) parts.push('间隔 ' + data.check_interval + 's');
  if (data && data.enabled_groups != null) parts.push('启用 ' + data.enabled_groups + ' 群');
  if (data && data.last_check_at) parts.push('上次检查 ' + data.last_check_at);
  if (data && data.last_round_summary) parts.push(data.last_round_summary);
  if (meta) meta.textContent = parts.join(' · ');
}

function startLogPolling() {
  stopLogPolling();
  const ar = $('autoRefreshChk');
  if (!ar || !ar.checked) return;
  logPollTimer = setInterval(() => pullLogs(false), LOG_POLL_MS);
}

function stopLogPolling() {
  if (logPollTimer) { clearInterval(logPollTimer); logPollTimer = null; }
}

async function clearLogs() {
  const ok = await showConfirm('确认清空日志？', '运行日志将被全部清空，且无法恢复。');
  if (!ok) return;
  try {
    await bridge.apiPost('logs/clear', {});
    logLines = [];
    state.set('logSinceId', 0);
    const ce = $console();
    if (ce) ce.innerHTML = '<div class="log-empty" id="logEmpty">日志已清空</div>';
    updateLogCount();
    await pullLogs(false);
    showToast('日志已清空', 'success');
  } catch (e) {
    showToast('清空失败：' + (e.message || e), 'error');
  }
}

function bindLogUi() {
  const lc = $console();
  if (lc) lc.addEventListener('scroll', onLogScroll);
  const ec = $('errorsOnlyChk');
  if (ec) ec.addEventListener('change', refilterLogDom);
  const ar = $('autoRefreshChk');
  if (ar) {
    ar.addEventListener('change', () => {
      if (ar.checked) { startLogPolling(); pullLogs(false); }
      else stopLogPolling();
    });
  }
  const sb = $('scrollBottomBtn');
  if (sb) sb.addEventListener('click', scrollLogToBottom);
  const cl = $('clearLogsBtn');
  if (cl) cl.addEventListener('click', clearLogs);
  const ls = $('logSearch');
  if (ls) ls.addEventListener('input', debounce(refilterLogDom, 200));
}

// ── 表单提交 ──

function bindFormSubmit() {
  const form = $('configForm');
  if (!form) return;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearFormErrors();

    const groupId = ($('f_group_id').value || '').trim();
    const ip = ($('f_ip').value || '').trim();
    const portRaw = ($('f_port').value || '').trim();
    const port = parseInt(portRaw, 10);

    let hasError = false;
    if (!groupId) { setFieldError('err_group_id', true); hasError = true; }
    if (!ip) { setFieldError('err_ip', true); hasError = true; }
    if (!portRaw || isNaN(port) || port <= 0 || port > 65535) { setFieldError('err_port', true); hasError = true; }
    if (hasError) return;

    const data = {
      group_id: groupId,
      name: ($('f_name').value || '').trim() || 'Minecraft服务器',
      server_ip: ip,
      server_port: port,
      server_type: $('f_type').value,
      enabled: $('f_enabled').checked,
      use_hitokoto: $('f_hitokoto').checked,
      simple_mode: $('f_simple_mode').checked,
      api_source: $('f_api_source').value,
      mcmotdapi_host: $('f_mcmotdapi_host').value,
      mcmotdapi_ssl: $('f_mcmotdapi_ssl').checked,
    };

    const sb = $('submitBtn');
    if (!sb) return;
    sb.disabled = true;
    sb.textContent = '保存中…';
    try {
      await bridge.apiPost('configs/save', data);
      showToast(state.get('editingGid') ? '配置已更新' : '配置已添加', 'success');
      closeModal();
      await loadTable();
    } catch (err) {
      showToast('保存失败：' + (err.message || err), 'error');
    } finally {
      sb.disabled = false;
      sb.textContent = '保存';
    }
  });
}

// ── 事件绑定（DOM 就绪后调用） ──

function bindStaticUi() {
  // 添加按钮
  const addBtn = $('addBtn');
  if (addBtn) addBtn.addEventListener('click', openAddModal);

  // Modal 取消
  const cancelBtn = $('cancelBtn');
  if (cancelBtn) cancelBtn.addEventListener('click', closeModal);

  // Confirm 按钮
  const confirmCancel = $('confirmCancel');
  if (confirmCancel) confirmCancel.addEventListener('click', () => closeConfirm(false));
  const confirmOk = $('confirmOk');
  if (confirmOk) confirmOk.addEventListener('click', () => closeConfirm(true));

  // mcmotdapi 配置显隐
  const fApiSource = $('f_api_source');
  if (fApiSource) fApiSource.addEventListener('change', toggleMcmotdapiConfig);
}

// ── 入口 ──

async function main() {
  try {
    await bridge.ready();
    bindStaticUi();
    bindFilters();
    bindFormSubmit();
    bindLogUi();
    await loadTable();
    await pullLogs(true);
    startLogPolling();
  } catch (e) {
    showToast('页面初始化失败：' + (e.message || e), 'error');
    const st = $('monitorStatusText');
    if (st) st.textContent = '页面初始化失败';
    const dot = $('monitorDot');
    if (dot) dot.className = 'status-dot off';
  }
}

main();
