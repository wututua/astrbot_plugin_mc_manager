import { createSettings } from "./settings.js";
const $ = id => document.getElementById(id);
const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
let snapshot = { groups: [], servers: [] };
let currentTab = 'overview';
let logPage = 1;
let logTotal = 0;
let editingId = '';
let running = false;
const settings = createSettings({ api, work, notice, output, confirmAction, refresh: () => loadSnapshot(false) });

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function notice(text, error = false) {
  $('notice').textContent = text;
  $('notice').classList.toggle('error', error);
}
function output(text) { $('output').textContent = text; $('feedback').hidden = !text; }
function groupId() {
  if (!$('group').value) throw new Error('请先在配置中添加群与服务器绑定。');
  return $('group').value;
}
async function api(method, endpoint, data) {
  const result = await bridge[method](endpoint, data);
  if (result?.status === 'error') throw new Error(result.message || '请求失败。');
  return result?.data ?? result;
}
async function work(fn) {
  if (running) return;
  running = true;
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  settings.setBusy(true);
  try { await fn(); }
  catch (err) { notice(err.message || String(err), true); output(err.message || String(err)); }
  finally {
    running = false;
    document.querySelectorAll('button').forEach(b => b.disabled = false);
    $('prev').disabled = logPage <= 1;
    $('next').disabled = logPage * 20 >= logTotal;
    settings.setBusy(false);
  }
}
async function confirmAction(text) {
  const dialog = $('confirm-dialog');
  $('confirm-text').textContent = text;
  dialog.returnValue = 'cancel';
  return new Promise(resolve => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true });
    dialog.showModal();
  });
}
function actionButton(text, action, args, confirmText = '', className = 'secondary') {
  const button = node('button', text, className);
  button.addEventListener('click', async () => {
    if (confirmText && !await confirmAction(confirmText)) return;
    await work(() => actionRequest(action, args));
  });
  return button;
}
async function actionRequest(action, args = []) {
  const result = await api('apiPost', 'action', { group: groupId(), action, args });
  output(result.message);
  notice(action === 'restart' ? '重启任务已启动，结果将广播到对应群聊。' : '操作完成。');
  await loadSnapshot(false);
  if (currentTab === 'whitelist') await loadEntries();
  if (currentTab === 'audit') await loadLogs();
}
function renderServers() {
  const container = $('server-cards');
  container.replaceChildren();
  const group = snapshot.groups.find(g => g.id === $('group').value);
  $('selected-server-count').textContent = group?.server_ids.length || 0;
  if (!group) {
    const empty = node('div', undefined, 'empty');
    const configure = node('button', '添加服务器与群聊');
    configure.addEventListener('click', () => work(() => selectTab('settings')));
    empty.append(node('h3', '开始管理你的服务器'), node('p', '先配置服务端目录、连接信息和群聊绑定。'), configure);
    container.append(empty);
    return;
  }
  for (const server of snapshot.servers.filter(s => group.server_ids.includes(s.id))) {
    const card = node('article', undefined, 'card');
    const head = node('div', undefined, 'card-head');
    const status = server.status === '在线' ? ['在线', 'online'] : server.status === '重启中' ? ['重启中', 'restarting'] : server.status.includes('离线') ? ['离线', 'offline'] : [server.status === '探测中' ? '检测中' : '待检测', ''];
    const badge = node('span', status[0], 'badge ' + status[1]); badge.title = server.status;
    head.append(node('h3', server.name), badge);
    const directory = node('details', undefined, 'server-directory');
    directory.append(node('summary', '服务端目录'), node('div', server.directory, 'path'));
    card.append(head, node('div', server.id, 'sid'), directory);
    if (server.pending) card.append(node('p', '待同步：' + server.pending, 'pending'));
    const buttons = node('div', undefined, 'buttons');
    buttons.append(actionButton('在线玩家', 'players', [server.id]), actionButton('TPS', 'tps', [server.id]),
      actionButton('重启', 'restart', [server.id], `将向 ${server.name} 发送 stop。
请确认启动脚本已经配置自动重启。`, 'danger'));
    card.append(buttons);
    container.append(card);
  }
}
async function loadSnapshot(probe) {
  snapshot = await api('apiGet', 'snapshot', { probe: probe ? '1' : '0' });
  $('configuration-error').hidden = !snapshot.config_error;
  $('configuration-error').textContent = snapshot.config_error ? '配置待修正：' + snapshot.config_error : '';
  $('server-count').textContent = snapshot.servers.length;
  $('group-count').textContent = snapshot.groups.reduce((total, group) => total + (group.group_ids?.length || 1), 0);
  $('pending-count').textContent = snapshot.servers.filter(s => s.pending).length;
  $('notification-count').textContent = snapshot.notifications_pending;
  const selected = $('group').value;
  $('group').replaceChildren();
  for (const group of snapshot.groups) {
    const option = node('option', `${group.id} · ${group.group_id}`);
    option.value = group.id;
    $('group').append(option);
  }
  $('group').disabled = !snapshot.groups.length;
  if (!snapshot.groups.length) { const option = node('option', '尚未配置'); option.value = ''; $('group').append(option); }
  else if (snapshot.groups.some(g => g.id === selected)) $('group').value = selected;
  else $('group').value = snapshot.groups[0].id;
  const group = snapshot.groups.find(g => g.id === $('group').value);
  $('group-info').textContent = group ? `${group.server_ids.length} 台服务器 · ${group.entries} 条白名单 · ${group.broadcast_ready ? `已记录 ${group.broadcast_group_ids?.length || 1}/${group.group_ids?.length || 1} 个群的广播会话` : '在各群内发送一次 /mc 以记录广播会话'}` : '';
  renderServers();
}
async function loadEntries() {
  const result = await api('apiGet', 'entries', { group: groupId() });
  $('entries').replaceChildren();
  for (const entry of result.entries) {
    const row = node('tr');
    for (const value of ['#' + entry.id, entry.name, entry.owner || '历史导入', entry.uuid]) row.append(node('td', value));
    const cell = node('td');
    const buttons = node('div', undefined, 'buttons');
    const edit = node('button', '修改', 'secondary');
    edit.addEventListener('click', () => {
      editingId = String(entry.id);
      $('edit-player').value = entry.name;
      $('edit-dialog').showModal();
    });
    buttons.append(edit, actionButton('移除', 'remove', [String(entry.id)], `从本群所有服务器移除 #${entry.id} ${entry.name}？`, 'danger'));
    cell.append(buttons); row.append(cell); $('entries').append(row);
  }
  if (!result.entries.length) {
    const row = node('tr'); const cell = node('td', '暂无白名单。'); cell.colSpan = 5; row.append(cell); $('entries').append(row);
  }
}
const actionLabels = { add: '添加', remove: '移除', initial_import: '初始导入', import: '导入', sync: '同步', restart_requested: '申请重启', restart_success: '重启成功', restart_failed: '重启失败', bind_group: '绑定群聊', unbind_group: '释放群绑定', change_group: '修改群号/平台', move_server: '转移服务器' };
async function loadLogs() {
  const result = await api('apiGet', 'logs', { group: groupId(), page: logPage, user: $('log-user').value.trim() });
  logTotal = result.total;
  $('logs').replaceChildren();
  for (const entry of result.logs) {
    const row = node('tr');
    const date = new Date(entry.time).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
    for (const value of [date, actionLabels[entry.action] || entry.action, entry.group_id, `${entry.user_name || ''} (${entry.user})`, entry.player || '—', entry.detail]) row.append(node('td', value));
    $('logs').append(row);
  }
  if (!result.logs.length) {
    const row = node('tr'); const cell = node('td', '暂无符合条件的日志。'); cell.colSpan = 6; row.append(cell); $('logs').append(row);
  }
  $('log-page').textContent = `第 ${logPage} / ${Math.max(1, Math.ceil(logTotal / 20))} 页 · ${logTotal} 条`;
  $('prev').disabled = logPage <= 1;
  $('next').disabled = logPage * 20 >= logTotal;
}
async function loadCurrentTab() {
  if (currentTab === 'whitelist') await loadEntries();
  if (currentTab === 'audit') await loadLogs();
  if (currentTab === 'settings') await settings.load();
}
async function selectTab(tab) {
  currentTab = tab;
  output('');
  const titles = { overview: ['服务器概览', '查看服务器状态与同步进度。'], whitelist: ['白名单管理', '添加玩家，维护当前配置组的白名单。'], audit: ['操作日志', '查找每一次白名单和服务器操作。'], settings: ['配置与菜单', '管理连接、群聊和机器人回复。'] };
  $('page-title').textContent = titles[tab][0]; $('page-subtitle').textContent = titles[tab][1];
  $('scope-bar').hidden = tab === 'settings'; $('overview-stats').hidden = tab !== 'overview';
  document.querySelectorAll('[data-tab]').forEach(button => {
    const active = button.dataset.tab === tab;
    button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
    if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  });
  for (const id of ['overview', 'whitelist', 'audit', 'settings']) $(id).hidden = id !== tab;
  await loadCurrentTab();
}
for (const button of document.querySelectorAll('[data-tab]')) {
  button.addEventListener('click', () => work(async () => {
    await selectTab(button.dataset.tab);
  }));
}
$('refresh').addEventListener('click', () => work(async () => { await loadSnapshot(true); await loadCurrentTab(); notice('状态已刷新。'); }));
$('group').addEventListener('change', () => work(async () => { logPage = 1; output(''); await loadSnapshot(false); await loadCurrentTab(); }));
$('add-form').addEventListener('submit', event => { event.preventDefault(); work(async () => { await actionRequest('add', [$('player').value.trim()]); $('player').value = ''; }); });
$('sync').addEventListener('click', () => work(() => actionRequest('sync')));
$('import').addEventListener('click', async () => {
  if (await confirmAction('将合并本群各服文件里的白名单。手动导入可能重新引入曾删除、尚未同步的条目；导入条目由管理员管理。')) await work(() => actionRequest('import'));
});
$('clear-output').addEventListener('click', () => output(''));
$('edit-cancel').addEventListener('click', () => $('edit-dialog').close());
$('edit-form').addEventListener('submit', event => { event.preventDefault(); const name = $('edit-player').value.trim(); $('edit-dialog').close(); work(() => actionRequest('modify', [editingId, name])); });
$('log-filter').addEventListener('submit', event => { event.preventDefault(); logPage = 1; work(loadLogs); });
$('prev').addEventListener('click', () => work(async () => { logPage = Math.max(1, logPage - 1); await loadLogs(); }));
$('next').addEventListener('click', () => work(async () => { logPage++; await loadLogs(); }));
await work(async () => {
  if (!bridge) throw new Error('请从 AstrBot 的插件详情页打开管理面板。');
  await bridge.ready();
  await loadSnapshot(false);
  if (snapshot.config_error) {
    await selectTab('settings');
    notice('插件已加载，请在下方修正群或服务器配置后保存。', true);
  } else notice('后台已连接，服务器状态自动更新。');
});

setInterval(() => {
  if (!document.hidden && !running && currentTab !== "settings") work(() => loadSnapshot(false));
}, 10000);
