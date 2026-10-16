const $ = id => document.getElementById(id);
function el(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}
export function downloadExport(result) {
  const bytes = result.content_base64 ? Uint8Array.from(atob(result.content_base64), char => char.charCodeAt(0)) : result.content;
  const blob = new Blob([bytes], {type: result.mime || 'application/zip'});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob); link.download = result.name;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
function humanBytes(bytes) { return bytes === null || bytes === undefined ? '未知' : (bytes / 1024 / 1024).toFixed(1) + ' MB'; }
function stamp(value) { return value ? new Date(value).toLocaleString('zh-CN', {hour12:false}) : '尚无记录'; }
export function createOperations({api, work, notice, confirmAction, group, onRestored}) {
  let active = 'diagnostics';
  let busy = false;
  let restore = null;
  let cleanup = null;
  const params = () => group() ? {group: group()} : {};
  const post = payload => api('apiPost', 'operations', {...params(), ...payload});
  const get = kind => api('apiGet', 'operations', {...params(), kind});
  function button(text, fn, confirmation = '', danger = false) {
    const control = el('button', text, danger ? 'danger' : 'secondary');
    control.addEventListener('click', async () => {
      if (confirmation && !await confirmAction(confirmation)) return;
      await work(fn);
    });
    return control;
  }
  function syncState() {
    $('ops-restore-apply').disabled = busy || !restore;
    $('ops-cleanup-apply').disabled = busy || !cleanup || !cleanup.count;
  }
  async function diagnose() {
    const result = await get('diagnostics');
    const container = $('ops-health'); container.replaceChildren();
    const summary = el('article', undefined, 'ops-card'); summary.append(el('h4', '后台任务与存储'));
    for (const [name, health] of Object.entries(result.workers || {})) summary.append(el('p', ({sync:'在线探测与同步',notifications:'通知投递',maintenance:'保留策略与告警'}[name] || name) + '：' + ({waiting:'正常等待',running:'运行中',retrying:'异常后重试',paused:'已暂停',starting:'启动中'}[health.state] || health.state) + ' · 最近成功 ' + stamp(health.last_success) + (health.last_error ? ' · 最近错误：' + health.last_error : '')));
    for (const [name, storage] of Object.entries(result.storage || {})) summary.append(el('p', (name === 'plugin_data' ? '插件数据' : '临时目录') + '：' + storage.path + ' · 剩余 ' + humanBytes(storage.free_bytes) + (storage.error ? ' · ' + storage.error : '')));
    summary.append(el('p', '数据库及 WAL 共 ' + humanBytes(Object.values(result.database_bytes || {}).reduce((sum, value) => sum + value, 0)) + ' · 待发送 ' + (result.queue?.pending || 0) + ' · 失败通知 ' + (result.queue?.failed || 0)));
    container.append(summary);
    for (const server of result.servers || []) {
      const card = el('article', undefined, 'ops-card'); card.append(el('h4', server.name + '（' + server.id + '）'));
      card.append(el('p', 'RCON：' + server.rcon + ' · 检查耗时 ' + (server.status_latency_ms ?? '—') + ' ms'));
      card.append(el('p', '服务端目录：' + server.directory.path + ' · 可写：' + (server.directory.writable === true ? '是' : '否') + ' · 剩余 ' + humanBytes(server.directory.free_bytes)));
      if (server.directory.error) card.append(el('p', server.directory.error));
      card.append(el('p', server.whitelist.readable ? '白名单可读，共 ' + server.whitelist.entries + ' 条' : server.whitelist.error));
      if (server.spark) card.append(el('p', 'spark：' + server.spark.status + (server.spark.error ? ' · ' + server.spark.error : '')));
      container.append(card);
    }
  }
  function showRestore(preview) {
    restore = preview;
    $('ops-restore-summary').textContent = '配置组：' + (preview.groups.map(item => item.id + '（' + item.group_id + '）').join('、') || '无') + '\n服务器：' + (preview.servers.join('、') || '无') + '\n白名单 ' + preview.counts.entries + ' 条，日志 ' + preview.counts.logs + ' 条\n' + preview.note;
    $('ops-restore-preview').hidden = false;
    syncState();
  }
  async function backups() {
    const result = await get('backups');
    const container = $('ops-backup-list'); container.replaceChildren();
    for (const backup of result.backups) {
      const card = el('article', undefined, 'ops-card'); card.append(el('h4', backup.name), el('p', stamp(backup.created) + ' · ' + humanBytes(backup.bytes) + ' · ' + (backup.label || 'manual')));
      const buttons = el('div', undefined, 'buttons');
      buttons.append(button('下载', async () => downloadExport(await post({action:'backup_download',name:backup.name}))), button('预览恢复', async () => showRestore(await post({action:'restore_preview',name:backup.name}))));
      card.append(buttons); container.append(card);
    }
    if (!result.backups.length) container.append(el('p', '暂无备份，可立即创建或上传已有备份。', 'empty'));
  }
  async function notifications() {
    const result = await get('notifications');
    const container = $('ops-notification-list'); container.replaceChildren();
    for (const item of result.notifications) {
      const card = el('article', undefined, 'ops-card');
      card.append(el('h4', '#' + item.id + ' · ' + (item.state === 'failed' ? '已进入失败列表' : '等待发送') + ' · 已尝试 ' + item.attempts + ' 次'), el('p', item.origin + ' · ' + stamp(item.created)), el('p', item.text));
      if (item.last_error) card.append(el('p', '最近失败：' + item.last_error));
      if (item.next_attempt && item.state !== 'failed') card.append(el('p', '下次尝试：' + stamp(item.next_attempt * 1000)));
      card.append(button('重新加入发送队列', async () => {await post({action:'notification_retry',id:item.id}); await notifications(); notice('已重新加入发送队列。');}));
      container.append(card);
    }
    if (!result.notifications.length) container.append(el('p', '当前没有待发送或失败通知。', 'empty'));
  }
  async function requests() {
    const container = $('ops-request-list'); container.replaceChildren();
    if (!group()) {container.append(el('p', '先配置并选择一个群组。', 'empty')); return;}
    const result = await get('requests');
    for (const item of result.requests) {
      const card = el('article', undefined, 'ops-card');
      card.append(el('h4', '#' + item.id + ' · ' + (item.kind === 'add' ? '添加' : '修改') + ' ' + item.player), el('p', '申请人：' + (item.user_name || item.user) + '（' + item.user + '） · ' + stamp(item.created)));
      const buttons = el('div', undefined, 'buttons');
      buttons.append(button('通过', async () => {const reply=await post({action:'approve',id:item.id});notice(reply.message);await requests();}, '通过 #' + item.id + ' ' + item.player + ' 的白名单申请？'), button('拒绝', async () => {const reply=await post({action:'reject',id:item.id,detail:$('ops-reject-reason').value.trim() || '管理员拒绝'});notice(reply.message);await requests();}, '拒绝 #' + item.id + ' ' + item.player + ' 的申请？', true));
      card.append(buttons); container.append(card);
    }
    if (!result.requests.length) container.append(el('p', '暂无待审核申请。', 'empty'));
  }
  async function load() {
    if (active === 'diagnostics') await diagnose();
    if (active === 'backups') await backups();
    if (active === 'notifications') await notifications();
    if (active === 'requests') await requests();
    syncState();
  }
  for (const control of document.querySelectorAll('[data-ops-tab]')) control.addEventListener('click', () => work(async () => {
    active = control.dataset.opsTab;
    document.querySelectorAll('[data-ops-tab]').forEach(item => {item.classList.toggle('active', item === control);item.setAttribute('aria-pressed',String(item === control));});
    document.querySelectorAll('.ops-section').forEach(section => section.hidden = section.id !== 'ops-' + active);
    await load();
  }));
  $('ops-diagnose').addEventListener('click', () => work(diagnose));
  $('ops-backup-refresh').addEventListener('click', () => work(backups));
  $('ops-backup-create').addEventListener('click', () => work(async () => {await post({action:'backup_create'});await backups();notice('插件数据备份已创建。');}));
  $('ops-backup-upload').addEventListener('change', () => work(async () => {
    const file = $('ops-backup-upload').files[0]; if (!file) return;
    if (file.size > 32 * 1024 * 1024) throw new Error('备份不能超过 32 MB。');
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = ''; for (let index=0; index<bytes.length; index+=32768) binary += String.fromCharCode(...bytes.subarray(index,index+32768));
    showRestore(await post({action:'restore_preview',data:btoa(binary)}));
    $('ops-backup-upload').value='';
  }));
  $('ops-restore-apply').addEventListener('click', async () => {
    if (!restore || !await confirmAction('恢复将替换当前插件配置、白名单编号、归属和日志，并放弃配置草稿。当前数据会先自动备份。确认恢复？')) return;
    await work(async () => {const result=await post({action:'restore_apply',token:restore.token,revision:restore.revision,confirmed:true});restore=null;$('ops-restore-preview').hidden=true;await onRestored();await backups();notice(result.message);});
  });
  $('ops-notification-refresh').addEventListener('click', () => work(notifications));
  $('ops-request-refresh').addEventListener('click', () => work(requests));
  $('ops-cleanup-preview').addEventListener('click', () => work(async () => {cleanup=await post({action:'cleanup_preview'});$('ops-cleanup-summary').textContent='保留天数：' + (cleanup.days || '不限制') + '；最多条数：' + (cleanup.maximum || '不限制') + '。本次将删除 ' + cleanup.count + ' 条日志。';syncState();}));
  $('ops-cleanup-apply').addEventListener('click', async () => {
    if (!cleanup || !cleanup.count || !await confirmAction('确认删除预览中的 ' + cleanup.count + ' 条日志？')) return;
    await work(async () => {const result=await post({action:'cleanup_apply',token:cleanup.token});cleanup=null;$('ops-cleanup-summary').textContent='已删除 ' + result.count + ' 条日志。';syncState();});
  });
  return {load,setBusy(value){busy=value;syncState();}};
}
