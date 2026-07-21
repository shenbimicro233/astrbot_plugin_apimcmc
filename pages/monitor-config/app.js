const bridge = window.AstrBotPluginPage;

// ── 检查桥接 API 是否可用 ──
if (!bridge || typeof bridge.ready !== 'function') {
  document.body.innerHTML =
    '<div style="padding:48px;text-align:center;font-size:16px;color:#ef4444;">' +
    '<h2 style="font-size:22px;margin-bottom:12px;">桥接 API 不可用</h2>' +
    '<p>插件页面加载异常，请确保通过 AstrBot WebUI「插件」→「Minecraft服务器监控」进入。</p></div>';
  throw new Error('bridge unavailable');
}

const $ = (id) => document.getElementById(id);
let editingGroupId = null;
let allConfigs = {}; // 全量配置缓存
let currentFilter = 'all'; // all | enabled | disabled | java | bedrock
let currentQuery = '';

// ── 运行日志状态 ──
const LOG_POLL_MS = 3000;
const LOG_DOM_MAX = 300;
let logSinceId = 0;
let logPollTimer = null;
let logFollowBottom = true;
let logLines = [];

// ── Toast ──
const ICONS = {
  success: '<path d="M20 6L9 17l-5-5"></path>',
  error: '<line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line>',
  info: '<circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line>',
};

function showToast(msg, type = 'info') {
  const el = $('toast');
  const icon = $('toastIcon');
  $('toastText').textContent = msg;
  icon.innerHTML = ICONS[type] || ICONS.info;
  el.className = 'toast toast-' + type + ' show';
  clearTimeout(el._hide);
  el._hide = setTimeout(() => el.classList.remove('show'), 2800);
}

// ── 工具函数 ──
function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

// ── 加载状态 ──
function showSkeleton() {
  const grid = $('cardsGrid');
  grid.innerHTML = Array.from({ length: 4 })
    .map(() => `
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
      </div>
    `).join('');
}

function showEmptyState() {
  $('cardsGrid').innerHTML = `
    <div class="empty-state" style="grid-column: 1 / -1;">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <rect x="2" y="7" width="20" height="14" rx="2"></rect>
        <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path>
      </svg>
      <h3>暂无配置</h3>
      <p>点击右上角「添加配置」为 QQ 群设置 Minecraft 服务器监控。</p>
    </div>`;
}

// ── 加载配置 ──
async function loadTable() {
  showSkeleton();
  try {
    const res = await bridge.apiGet('configs');
    allConfigs = res || {};
    updateStats();
    renderCards();
  } catch (e) {
    showToast('加载配置失败：' + (e.message || e), 'error');
    showEmptyState();
  }
}

function updateStats() {
  const configs = Object.values(allConfigs);
  const enabled = configs.filter(c => c.enabled !== false).length;
  const java = configs.filter(c => c.server_type === 'java').length;
  const bedrock = configs.filter(c => c.server_type !== 'java').length;
  $('statTotal').textContent = configs.length;
  $('statEnabled').textContent = enabled;
  $('statJava').textContent = java;
  $('statBedrock').textContent = bedrock;
}

function matchesFilter(cfg, filter) {
  if (filter === 'enabled') return cfg.enabled !== false;
  if (filter === 'disabled') return cfg.enabled === false;
  if (filter === 'java') return cfg.server_type === 'java';
  if (filter === 'bedrock') return cfg.server_type !== 'java';
  return true;
}

function matchesQuery(cfg, gid, query) {
  if (!query) return true;
  const q = query.toLowerCase();
  return String(gid).toLowerCase().includes(q)
    || String(cfg.name || '').toLowerCase().includes(q)
    || String(cfg.server_ip || '').toLowerCase().includes(q);
}

function renderCards() {
  const grid = $('cardsGrid');
  const entries = Object.entries(allConfigs).filter(([gid, cfg]) =>
    matchesFilter(cfg, currentFilter) && matchesQuery(cfg, gid, currentQuery)
  );

  if (!entries.length && !Object.keys(allConfigs).length) {
    showEmptyState();
    return;
  }

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
    const typeLabel = isJava ? 'Java' : 'Bedrock';
    const typeClass = isJava ? 'badge-java' : 'badge-bedrock';
    const statusClass = cfg.enabled !== false ? 'badge-on' : 'badge-off';
    const statusText = cfg.enabled !== false ? '已启用' : '已禁用';
    return `
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">${escHtml(cfg.name || '未命名')}</div>
            <div class="card-sub">群号 ${escHtml(gid)}</div>
          </div>
          <span class="badge ${statusClass}">${statusText}</span>
        </div>
        <div class="card-body">
          <div class="row"><span class="k">服务器地址</span><span class="v">${escHtml(cfg.server_ip)}:${escHtml(String(cfg.server_port))}</span></div>
          <div class="row"><span class="k">服务器类型</span><span class="badge ${typeClass}">${typeLabel}</span></div>
          <div class="row"><span class="k">附加一言</span><span class="v">${cfg.use_hitokoto !== false ? '开启' : '关闭'}</span></div>
          <div class="row"><span class="k">群号</span><span class="v" style="font-family:monospace;">${escHtml(gid)}</span></div>
        </div>
        <div class="card-footer">
          <button class="btn btn-secondary btn-sm" onclick="editConfig('${escHtml(gid)}')">编辑</button>
          <button class="btn btn-danger btn-sm" onclick="deleteConfig('${escHtml(gid)}')">删除</button>
        </div>
      </div>
    `;
  }).join('');
}

// ── 搜索与筛选 ──
const applyFilter = debounce(() => {
  currentQuery = ($('searchInput').value || '').trim();
  renderCards();
}, 200);

function bindFilters() {
  $('searchInput').addEventListener('input', applyFilter);

  document.querySelectorAll('.chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      currentFilter = chip.dataset.filter;
      renderCards();
    });
  });
}

// ── 确认框 ──
let confirmResolve = null;
function showConfirm(title, text) {
  return new Promise((resolve) => {
    confirmResolve = resolve;
    $('confirmTitle').textContent = title;
    $('confirmText').textContent = text;
    $('confirmOverlay').classList.add('active');
  });
}
function closeConfirm(result) {
  $('confirmOverlay').classList.remove('active');
  if (confirmResolve) confirmResolve(result);
  confirmResolve = null;
}
$('confirmCancel').addEventListener('click', () => closeConfirm(false));
$('confirmOk').addEventListener('click', () => closeConfirm(true));

// ── Modal: 打开新增 ──
$('addBtn').addEventListener('click', () => {
  editingGroupId = null;
  $('modalTitle').textContent = '添加配置';
  $('f_group_id').value = '';
  $('f_group_id').disabled = false;
  $('f_name').value = 'Minecraft服务器';
  $('f_ip').value = '';
  $('f_port').value = '19132';
  $('f_type').value = 'bedrock';
  $('f_enabled').checked = true;
  $('f_hitokoto').checked = true;
  clearFormErrors();
  $('modalOverlay').classList.add('active');
  $('f_group_id').focus();
});

// ── Modal: 打开编辑 ──
window.editConfig = async function (gid) {
  const cfg = allConfigs[gid];
  if (!cfg) return showToast('配置不存在', 'error');
  editingGroupId = gid;
  $('modalTitle').textContent = '编辑配置 - ' + gid;
  $('f_group_id').value = gid;
  $('f_group_id').disabled = true;
  $('f_name').value = cfg.name || '';
  $('f_ip').value = cfg.server_ip || '';
  $('f_port').value = String(cfg.server_port || '');
  $('f_type').value = cfg.server_type || 'bedrock';
  $('f_enabled').checked = cfg.enabled !== false;
  $('f_hitokoto').checked = cfg.use_hitokoto !== false;
  clearFormErrors();
  $('modalOverlay').classList.add('active');
};

function closeModal() {
  $('modalOverlay').classList.remove('active');
  editingGroupId = null;
}
$('cancelBtn').addEventListener('click', closeModal);
$('modalOverlay').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeModal();
});

function clearFormErrors() {
  ['err_group_id', 'err_ip', 'err_port'].forEach((id) => $(id).classList.remove('show'));
}

function setFieldError(id, show) {
  $(id).classList.toggle('show', show);
}

// ── Modal: 提交 ──
$('configForm').addEventListener('submit', async (e) => {
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
  };

  const submitBtn = $('submitBtn');
  submitBtn.disabled = true;
  submitBtn.textContent = '保存中…';
  try {
    await bridge.apiPost('configs/save', data);
    showToast(editingGroupId ? '配置已更新' : '配置已添加', 'success');
    closeModal();
    await loadTable();
  } catch (err) {
    showToast('保存失败：' + (err.message || err), 'error');
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = '保存';
  }
});

// ── 删除 ──
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

// ════════════════════════════════
// 运行日志
// ════════════════════════════════

function normalizeLevel(level) {
  const lv = String(level || 'INFO').toUpperCase();
  return lv === 'WARNING' ? 'WARN' : lv;
}

function isErrorLevel(level) {
  const lv = normalizeLevel(level);
  return lv === 'WARN' || lv === 'ERROR';
}

function updateMonitorStatus(data) {
  const running = !!(data && data.monitor_running);
  const dot = $('monitorDot');
  const text = $('monitorStatusText');
  const meta = $('monitorMetaText');

  dot.className = 'status-dot ' + (running ? 'on' : 'off');
  text.textContent = running ? '监控中' : '未运行';

  const statEl = $('statMonitor');
  statEl.textContent = running ? '监控中' : '未运行';
  statEl.className = 'stat-value ' + (running ? 'on' : 'off');

  const parts = [];
  if (data && data.check_interval != null) parts.push('间隔 ' + data.check_interval + 's');
  if (data && data.enabled_groups != null) parts.push('启用 ' + data.enabled_groups + ' 群');
  if (data && data.last_check_at) parts.push('上次检查 ' + data.last_check_at);
  if (data && data.last_round_summary) parts.push(data.last_round_summary);
  meta.textContent = parts.join(' · ');
}

function appendLogDom(entry) {
  const consoleEl = $('logConsole');
  const empty = $('logEmpty');
  if (empty) empty.remove();

  const line = document.createElement('div');
  line.className = 'log-line';
  line.dataset.level = normalizeLevel(entry.level);
  line.dataset.id = String(entry.id);

  const lv = normalizeLevel(entry.level);
  line.innerHTML =
    '<span class="ts">' + escHtml(entry.ts || '') + '</span>' +
    '<span class="lv lv-' + escHtml(lv) + '">' + escHtml(lv) + '</span>' +
    '<span class="msg">' + escHtml(entry.msg || '') + '</span>';

  const errorsOnly = $('errorsOnlyChk').checked;
  if (errorsOnly && !isErrorLevel(lv)) {
    line.style.display = 'none';
  }
  consoleEl.appendChild(line);

  while (consoleEl.querySelectorAll('.log-line').length > LOG_DOM_MAX) {
    const first = consoleEl.querySelector('.log-line');
    if (first) first.remove();
  }
  highlightLogSearch();
}

function refilterLogDom() {
  const errorsOnly = $('errorsOnlyChk').checked;
  const lines = $('logConsole').querySelectorAll('.log-line');
  lines.forEach((el) => {
    const lv = el.dataset.level || 'INFO';
    const matched = matchesLogSearch(el);
    const visible = (!errorsOnly || isErrorLevel(lv)) && matched;
    el.style.display = visible ? '' : 'none';
    el.classList.toggle('log-highlight', matched && ($('logSearch').value || '').trim() !== '');
  });
  updateLogCount();
  if (logFollowBottom) scrollLogToBottom();
}

function matchesLogSearch(el) {
  const q = ($('logSearch').value || '').trim().toLowerCase();
  if (!q) return true;
  return el.textContent.toLowerCase().includes(q);
}

function highlightLogSearch() {
  const q = ($('logSearch').value || '').trim();
  const lines = $('logConsole').querySelectorAll('.log-line');
  lines.forEach((el) => {
    if (!q) {
      el.classList.remove('log-highlight');
      return;
    }
    const matched = el.textContent.toLowerCase().includes(q.toLowerCase());
    el.classList.toggle('log-highlight', matched);
  });
}

function updateLogCount() {
  const all = $('logConsole').querySelectorAll('.log-line').length;
  const visible = Array.from($('logConsole').querySelectorAll('.log-line'))
    .filter((el) => el.style.display !== 'none').length;
  const errorsOnly = $('errorsOnlyChk').checked;
  const searching = ($('logSearch').value || '').trim() !== '';
  let text = all + ' 条';
  if (errorsOnly || searching) text = '显示 ' + visible + ' / 共 ' + all + ' 条';
  $('logCountText').textContent = text;
  $('logFollowText').textContent = logFollowBottom ? '跟随最新' : '已暂停跟随（上滚中）';
}

function scrollLogToBottom() {
  const el = $('logConsole');
  el.scrollTop = el.scrollHeight;
  logFollowBottom = true;
  updateLogCount();
}

function onLogScroll() {
  const el = $('logConsole');
  const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
  logFollowBottom = dist < 40;
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
      if (logLines.length > LOG_DOM_MAX) {
        logLines = logLines.slice(-LOG_DOM_MAX);
      }
      if (typeof data.next_id === 'number' && data.next_id > logSinceId) {
        logSinceId = data.next_id - 1;
      }
      refilterLogDom();
      if (logFollowBottom) scrollLogToBottom();
    } else if (initial) {
      updateLogCount();
    }
  } catch (e) {
    $('monitorStatusText').textContent = '日志拉取失败';
    $('monitorDot').className = 'status-dot off';
  }
}

function startLogPolling() {
  stopLogPolling();
  if (!$('autoRefreshChk').checked) return;
  logPollTimer = setInterval(() => pullLogs(false), LOG_POLL_MS);
}

function stopLogPolling() {
  if (logPollTimer) {
    clearInterval(logPollTimer);
    logPollTimer = null;
  }
}

async function clearLogs() {
  const ok = await showConfirm('确认清空日志？', '运行日志将被全部清空，且无法恢复。');
  if (!ok) return;
  try {
    await bridge.apiPost('logs/clear', {});
    logLines = [];
    logSinceId = 0;
    const consoleEl = $('logConsole');
    consoleEl.innerHTML = '<div class="log-empty" id="logEmpty">日志已清空</div>';
    updateLogCount();
    await pullLogs(false);
    showToast('日志已清空', 'success');
  } catch (e) {
    showToast('清空失败：' + (e.message || e), 'error');
  }
}

function bindLogUi() {
  $('logConsole').addEventListener('scroll', onLogScroll);
  $('errorsOnlyChk').addEventListener('change', refilterLogDom);
  $('autoRefreshChk').addEventListener('change', () => {
    if ($('autoRefreshChk').checked) {
      startLogPolling();
      pullLogs(false);
    } else {
      stopLogPolling();
    }
  });
  $('scrollBottomBtn').addEventListener('click', scrollLogToBottom);
  $('clearLogsBtn').addEventListener('click', clearLogs);
  $('logSearch').addEventListener('input', debounce(refilterLogDom, 200));
}

// ── 初始化 ──
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
    $('monitorStatusText').textContent = '页面初始化失败';
    $('monitorDot').className = 'status-dot off';
  }
}
main();
