const bridge = window.AstrBotPluginPage;
if (!bridge || typeof bridge.ready !== 'function') {
  document.body.innerHTML =
    '<div style="padding:48px;text-align:center;font-size:16px;color:#ef4444;">' +
    '<h2 style="font-size:22px;margin-bottom:12px;">桥接 API 不可用</h2>' +
    '<p>插件页面加载异常，请确保通过 AstrBot WebUI「插件」→「Minecraft服务器监控」进入。</p></div>';
  throw new Error('bridge unavailable');
}

const $ = (id) => document.getElementById(id);
let editingGid = null;
let allCfg = {};
let flt = 'all';
let q = '';

const LOG_POLL_MS = 3000;
const LOG_DOM_MAX = 300;
let logSinceId = 0;
let logPollTimer = null;
let logFollowBtm = true;
let logLines = [];

const ICONS = {
  success: '<path d="M20 6L9 17l-5-5"></path>',
  error: '<line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line>',
  info: '<circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line>',
};

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

function escHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function showSkeleton() {
  const grid = $('cardsGrid');
  if (!grid) return;
  grid.innerHTML = Array.from({ length: 4 }).map(() => `
    <div class="card skeleton-card">
      <div class="card-header">
        <div><div class="skeleton" style="height:16px;width:140px;margin-bottom:6px;"></div><div class="skeleton" style="height:12px;width:80px;"></div></div>
        <div class="skeleton" style="height:22px;width:52px;border-radius:12px;"></div>
      </div>
      <div class="card-body">
        <div class="row"><div class="skeleton" style="height:14px;width:60px;"></div><div class="skeleton" style="height:14px;width:100px;"></div></div>
        <div class="row"><div class="skeleton" style="height:14px;width:60px;"></div><div class="skeleton" style="height:14px;width:70px;"></div></div>
        <div class="row"><div class="skeleton" style="height:14px;width:60px;"></div><div class="skeleton" style="height:14px;width:90px;"></div></div>
      </div>
      <div class="card-footer">
        <div class="skeleton" style="height:28px;width:56px;border-radius:8px;"></div>
        <div class="skeleton" style="height:28px;width:56px;border-radius:8px;"></div>
      </div>
    </div>`).join('');
}

function showEmptyState() {
  const grid = $('cardsGrid');
  if (!grid) return;
  grid.innerHTML = `
    <div class="empty-state" style="grid-column: 1 / -1;">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <rect x="2" y="7" width="20" height="14" rx="2"></rect>
        <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path>
      </svg>
      <h3>暂无配置</h3>
      <p>点击右上角「添加配置」为 QQ 群设置 Minecraft 服务器监控。</p>
    </div>`;
}

async function loadTable() {
  showSkeleton();
  try {
    const res = await bridge.apiGet('configs');
    allCfg = res || {};
    updateStats();
    renderCards();
  } catch (e) {
    showToast('加载配置失败：' + (e.message || e), 'error');
    showEmptyState();
  }
}

function updateStats() {
  const vals = Object.values(allCfg);
  const en = vals.filter(c => c.enabled !== false).length;
  const ja = vals.filter(c => c.server_type === 'java').length;
  const be = vals.filter(c => c.server_type !== 'java').length;
  const st = (id, val) => { const el = $(id); if (el) el.textContent = val; };
  st('statTotal', vals.length);
  st('statEnabled', en);
  st('statJava', ja);
  st('statBedrock', be);
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
  const grid = $('cardsGrid');
  if (!grid) return;
  const entries = Object.entries(allCfg).filter(([gid, cfg]) => matchFlt(cfg, flt) && matchQ(cfg, gid, q));

  if (!entries.length && !Object.keys(allCfg).length) { showEmptyState(); return; }
  if (!entries.length) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column: 1 / -1;">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line>
        </svg>
        <h3>没有匹配的配置</h3>
        <p>尝试调整搜索关键词或筛选条件。</p>
      </div>`;
    return;
  }

  grid.innerHTML = entries.map(([gid, cfg]) => {
    const isJava = cfg.server_type === 'java';
    return `
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">${escHtml(cfg.name || '未命名')}</div>
            <div class="card-sub">群号 ${escHtml(gid)}</div>
          </div>
          <span class="badge ${cfg.enabled !== false ? 'badge-on' : 'badge-off'}">${cfg.enabled !== false ? '已启用' : '已禁用'}</span>
        </div>
        <div class="card-body">
          <div class="row"><span class="k">服务器地址</span><span class="v">${escHtml(cfg.server_ip)}:${escHtml(String(cfg.server_port))}</span></div>
          <div class="row"><span class="k">服务器类型</span><span class="badge ${isJava ? 'badge-java' : 'badge-bedrock'}">${isJava ? 'Java' : 'Bedrock'}</span></div>
          <div class="row"><span class="k">API 源</span><span class="v">${cfg.api_source ? (cfg.api_source === 'mcstatus' ? 'mcstatus.io' : 'mcmotdapi' + (cfg.mcmotdapi_host ? ' (' + escHtml(cfg.mcmotdapi_host) + ')' : '')) : '全局默认'}</span></div>
          <div class="row"><span class="k">消息格式</span><span class="v">${cfg.simple_mode === true ? '简化' : (cfg.simple_mode === false ? '完整' : '全局默认')}</span></div>
          <div class="row"><span class="k">附加一言</span><span class="v">${cfg.use_hitokoto !== false ? '开启' : '关闭'}</span></div>
          <div class="row"><span class="k">群号</span><span class="v" style="font-family:monospace;">${escHtml(gid)}</span></div>
        </div>
        <div class="card-footer">
          <button class="btn btn-secondary btn-sm" onclick="editConfig('${escHtml(gid)}')">编辑</button>
          <button class="btn btn-danger btn-sm" onclick="deleteConfig('${escHtml(gid)}')">删除</button>
        </div>
      </div>`;
  }).join('');
}

const applyFilter = debounce(() => {
  q = ($('searchInput').value || '').trim();
  renderCards();
}, 200);

function bindFilters() {
  const si = $('searchInput');
  if (si) si.addEventListener('input', applyFilter);
  document.querySelectorAll('.chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      flt = chip.dataset.filter;
      renderCards();
    });
  });
}

let confirmResolve = null;
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
const confirmCancel = $('confirmCancel');
if (confirmCancel) confirmCancel.addEventListener('click', () => closeConfirm(false));
const confirmOk = $('confirmOk');
if (confirmOk) confirmOk.addEventListener('click', () => closeConfirm(true));

function openAddModal() {
  editingGid = null;
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
const addBtn = $('addBtn');
if (addBtn) addBtn.addEventListener('click', openAddModal);

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

window.editConfig = async function (gid) {
  const cfg = allCfg[gid];
  if (!cfg) return showToast('配置不存在', 'error');
  editingGid = gid;
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
  // 编辑时锁定群号，不可修改
  const fg = $('f_group_id');
  if (fg) fg.disabled = true;
  clearFormErrors();
  const mo = $('modalOverlay');
  if (mo) mo.classList.add('active');
  setTimeout(toggleMcmotdapiConfig, 0);
};

function closeModal() {
  const mo = $('modalOverlay');
  if (mo) mo.classList.remove('active');
  editingGid = null;
}
const cancelBtn = $('cancelBtn');
if (cancelBtn) cancelBtn.addEventListener('click', closeModal);

function toggleMcmotdapiConfig() {
  const show = $('f_api_source') && $('f_api_source').value === 'mcmotdapi';
  const grp = $('mcmotdapi_config_group');
  if (grp) grp.style.display = show ? '' : 'none';
}
const fApiSource = $('f_api_source');
if (fApiSource) fApiSource.addEventListener('change', toggleMcmotdapiConfig);

function clearFormErrors() {
  ['err_group_id', 'err_ip', 'err_port'].forEach(id => { const el = $(id); if (el) el.classList.remove('show'); });
}

function setFieldError(id, show) {
  const el = $(id);
  if (el) el.classList.toggle('show', show);
}

const configForm = $('configForm');
if (configForm) {
  configForm.addEventListener('submit', async (e) => {
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
      showToast(editingGid ? '配置已更新' : '配置已添加', 'success');
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

window.deleteConfig = async function (gid) {
  const ok = await showConfirm('确认删除？', `群号 ${gid} 的监控配置将被永久删除。`);
  if (!ok) return;
  try {
    await bridge.apiPost('configs/delete', { group_id: gid });
    showToast('配置已删除', 'success');
    await loadTable();
  } catch (e) {
    showToast('删除失败：' + (e.message || e), 'error');
  }
};

function normalizeLevel(level) {
  const lv = String(level || 'INFO').toUpperCase();
  return lv === 'WARNING' ? 'WARN' : lv;
}

function isErrorLevel(level) {
  return ['WARN', 'ERROR'].includes(normalizeLevel(level));
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

function appendLogDom(entry) {
  const consoleEl = $('logConsole');
  if (!consoleEl) return;
  const empty = $('logEmpty');
  if (empty) empty.remove();

  const line = document.createElement('div');
  line.className = 'log-line';
  const lv = normalizeLevel(entry.level);
  line.dataset.level = lv;
  line.dataset.id = String(entry.id);
  line.innerHTML =
    '<span class="ts">' + escHtml(entry.ts || '') + '</span>' +
    '<span class="lv lv-' + escHtml(lv) + '">' + escHtml(lv) + '</span>' +
    '<span class="msg">' + escHtml(entry.msg || '') + '</span>';

  const errOnly = $('errorsOnlyChk');
  if (errOnly && errOnly.checked && !isErrorLevel(lv)) line.style.display = 'none';
  consoleEl.appendChild(line);

  while (consoleEl.querySelectorAll('.log-line').length > LOG_DOM_MAX) {
    const first = consoleEl.querySelector('.log-line');
    if (first) first.remove();
  }
  highlightLogSearch();
}

function refilterLogDom() {
  const errOnly = $('errorsOnlyChk');
  const eo = errOnly ? errOnly.checked : false;
  const lines = $('logConsole').querySelectorAll('.log-line');
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
  if (logFollowBtm) scrollLogToBottom();
}

function highlightLogSearch() {
  const searchEl = $('logSearch');
  const q = searchEl ? (searchEl.value || '').trim() : '';
  const lines = $('logConsole').querySelectorAll('.log-line');
  lines.forEach((el) => {
    if (!q) { el.classList.remove('log-highlight'); return; }
    el.classList.toggle('log-highlight', el.textContent.toLowerCase().includes(q.toLowerCase()));
  });
}

function updateLogCount() {
  const all = $('logConsole').querySelectorAll('.log-line').length;
  const visible = Array.from($('logConsole').querySelectorAll('.log-line')).filter(el => el.style.display !== 'none').length;
  const errOnly = $('errorsOnlyChk');
  const eo = errOnly ? errOnly.checked : false;
  const searchEl = $('logSearch');
  const searching = searchEl ? (searchEl.value || '').trim() !== '' : false;
  const ct = $('logCountText');
  const ft = $('logFollowText');
  if (ct) ct.textContent = (eo || searching) ? ('显示 ' + visible + ' / 共 ' + all + ' 条') : (all + ' 条');
  if (ft) ft.textContent = logFollowBtm ? '跟随最新' : '已暂停跟随（上滚中）';
}

function scrollLogToBottom() {
  const el = $('logConsole');
  if (!el) return;
  el.scrollTop = el.scrollHeight;
  logFollowBtm = true;
  updateLogCount();
}

function onLogScroll() {
  const el = $('logConsole');
  if (!el) return;
  const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
  logFollowBtm = dist < 40;
  updateLogCount();
}

async function pullLogs(initial) {
  try {
    const res = await bridge.apiGet('logs', {
      since_id: logSinceId,
      limit: initial ? 200 : 100,
    });
    const data = res || {};
    updateMonitorStatus(data);

    const logs = Array.isArray(data.logs) ? data.logs : [];
    if (logs.length) {
      for (const entry of logs) {
        logLines.push(entry);
        appendLogDom(entry);
        if (entry.id > logSinceId) logSinceId = entry.id;
      }
      if (logLines.length > LOG_DOM_MAX) logLines = logLines.slice(-LOG_DOM_MAX);
      if (typeof data.next_id === 'number' && data.next_id > logSinceId) logSinceId = data.next_id - 1;
      refilterLogDom();
      if (logFollowBtm) scrollLogToBottom();
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
    logSinceId = 0;
    const ce = $('logConsole');
    if (ce) ce.innerHTML = '<div class="log-empty" id="logEmpty">日志已清空</div>';
    updateLogCount();
    await pullLogs(false);
    showToast('日志已清空', 'success');
  } catch (e) {
    showToast('清空失败：' + (e.message || e), 'error');
  }
}

function bindLogUi() {
  const lc = $('logConsole');
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

async function main() {
  try {
    await bridge.ready();
    bindFilters();
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
