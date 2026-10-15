const $ = id => document.getElementById(id);
const copy = value => JSON.parse(JSON.stringify(value));
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const list = value => Array.isArray(value) ? value.map(String) : String(value || '').split(/[,，\n]+/).map(v => v.trim()).filter(Boolean);
const ranges = { page_size: [1, 100], sync_interval: [5, 3600], rcon_timeout: [1, 30], restart_timeout: [5, 3600], poll_interval: [1, 30], spark_file_max_age: [3, 300], rcon_port: [1, 65535], max_entries_per_user: [1, 100] };
const entityRequired = new Set(['id', 'name', 'directory', 'rcon_host', 'rcon_port', 'platform_id', 'group_id', 'max_entries_per_user', 'tps_command', 'players_command', 'spark_file']);
const sourceLabels = { auto: '自动：优先本地采样，再尝试 RCON', rcon: 'RCON 命令回复', file: '本地 spark 采集文件' };

function el(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

export function createSettings({ api, work, notice, output, confirmAction, refresh }) {
  let loaded = false;
  let schema = {};
  let draft = {};
  let saved = '';
  let revision = '';
  let jsonDirty = false;
  let activeSection = 'connections';
  let platforms = [];
  let bindings = { groups: [], servers: [] };
  let defaultTps = '';
  let variables = [];
  let entity = null;
  let busy = false;
  let editVersion = 0;

  function defaults(items, raw = {}) {
    const result = copy(raw);
    for (const [key, item] of Object.entries(items)) {
      if (item.type === 'object') result[key] = defaults(item.items || {}, result[key] || {});
      else if (result[key] === undefined && item.default !== undefined) result[key] = copy(item.default);
    }
    return result;
  }
  function checkShape(value) {
    if (!isObject(value)) throw new Error('完整配置必须是 JSON 对象。');
    for (const key of ['servers', 'groups']) {
      if (value[key] !== undefined && (!Array.isArray(value[key]) || value[key].some(row => !isObject(row)))) throw new Error(`${key} 必须是对象数组。`);
    }
    for (const key of ['commands', 'messages']) if (value[key] !== undefined && !isObject(value[key])) throw new Error(`${key} 必须是 JSON 对象。`);
    for (const group of value.groups || []) {
      for (const key of ['server_ids', 'admin_ids']) if (group[key] !== undefined && !Array.isArray(group[key]) && typeof group[key] !== 'string') throw new Error(`${key} 必须是账号/ID 列表。`);
    }
  }
  function normalize(value) {
    checkShape(value);
    const result = defaults(schema, value);
    result.servers = (result.servers || []).map(row => defaults(serverItems(), row));
    result.groups = (result.groups || []).map(row => {
      const group = defaults(groupItems(), row);
      group.server_ids = list(group.server_ids);
      group.admin_ids = list(group.admin_ids);
      return group;
    });
    return result;
  }
  function serverItems() { return schema.servers?.templates?.server?.items || {}; }
  function groupItems() { return schema.groups?.templates?.group?.items || {}; }
  function hasChanges() { return jsonDirty || (loaded && JSON.stringify(draft) !== saved); }
  function syncState() {
    $('config-state').textContent = hasChanges() ? '有未保存的修改' : '配置已保存';
    $('config-state').classList.toggle('unsaved', hasChanges());
    $('save-config').disabled = busy || !loaded;
    $('reload-config').disabled = busy || !loaded;
    $('add-server').disabled = busy || !loaded;
    $('add-group').disabled = busy || !loaded || !draft.servers?.length;
  }
  function touch() {
    editVersion++;
    // An incomplete advanced edit remains pending; form changes must not erase it.
    if (!jsonDirty) $('config-editor').value = JSON.stringify(draft, null, 2);
    syncState();
  }
  function commitJson() {
    if (!jsonDirty) return;
    let value;
    try { value = JSON.parse($('config-editor').value); }
    catch { throw new Error('配置不是有效的 JSON，请检查逗号、引号和花括号。'); }
    draft = normalize(value);
    jsonDirty = false;
    editVersion++;
    render();
  }
  function changeSection(name) {
    if (name !== 'advanced' && jsonDirty) {
      try { commitJson(); } catch (error) { notice(error.message, true); return; }
    }
    activeSection = name;
    for (const button of document.querySelectorAll('[data-settings-tab]')) {
      const active = button.dataset.settingsTab === name;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    }
    for (const section of document.querySelectorAll('.settings-section')) section.hidden = section.id !== 'settings-' + name;
  }

  function field(key, item, value, { id, onChange, readonly = false, required = false, passwordKept = false } = {}) {
    const wrapper = el('div', undefined, 'config-field' + (item.type === 'text' ? ' full-width' : ''));
    const inputId = id || 'setting-' + key;
    const label = el('label', item.description || key);
    label.htmlFor = inputId;
    const control = el(item.type === 'text' || item.type === 'list' ? 'textarea' : item.options ? 'select' : 'input');
    control.id = inputId;
    control.name = key;
    if (item.type === 'bool') {
      control.type = 'checkbox';
      control.checked = value === true;
      wrapper.classList.add('switch-field');
    } else if (item.options) {
      for (const optionValue of item.options) {
        const option = el('option', key === 'tps_source' ? sourceLabels[optionValue] || optionValue : String(optionValue));
        option.value = String(optionValue);
        control.append(option);
      }
      control.value = String(value ?? '');
    } else {
      if (control.tagName === 'INPUT') control.type = key === 'rcon_password' ? 'password' : item.type === 'int' ? 'number' : 'text';
      control.value = item.type === 'list' ? list(value).join('\n') : passwordKept ? '' : String(value ?? '');
      if (item.type === 'list') { control.rows = 3; control.placeholder = '每行一个，也可以用逗号分隔'; }
      if (item.type === 'text') control.rows = key === 'help' || key === 'tps' ? 10 : 4;
      if (item.type === 'int') {
        control.min = String((ranges[key] || [1, 100000])[0]);
        control.max = String((ranges[key] || [1, 100000])[1]);
        control.step = '1';
      }
      if (key === 'id') { control.pattern = '[A-Za-z0-9_-]{1,64}'; control.maxLength = 64; }
      if (key === 'rcon_password') { control.autocomplete = 'new-password'; if (passwordKept) control.placeholder = '留空保留已保存的密码'; }
      if (key === 'tps_missing_value') control.maxLength = 32;
      control.readOnly = readonly;
      control.spellcheck = false;
    }
    control.required = required;
    if (readonly) wrapper.classList.add('readonly-field');
    function read() {
      if (item.type === 'bool') return control.checked;
      if (item.type === 'int') return control.value === '' ? '' : Number(control.value);
      if (item.type === 'list') return list(control.value);
      if (passwordKept && !control.value) return '__KEEP__';
      return control.value;
    }
    if (onChange) control.addEventListener(item.type === 'bool' || item.options ? 'change' : 'input', () => onChange(read()));
    wrapper.append(label, control);
    if (item.hint) wrapper.append(el('span', item.hint, 'field-hint'));
    return { wrapper, control, read };
  }
  function addFields(containerId, items, owner, prefix, skip = new Set()) {
    const container = $(containerId);
    container.replaceChildren();
    for (const [key, item] of Object.entries(items)) {
      if (skip.has(key)) continue;
      const entry = field(key, item, owner[key], { id: prefix + key, required: item.type === 'int', onChange: value => { owner[key] = value; touch(); } });
      container.append(entry.wrapper);
    }
  }
  function render() {
    const root = field('server_root', schema.server_root, draft.server_root, { id: 'setting-server-root', onChange: value => { draft.server_root = value; touch(); } });
    $('root-field').replaceChildren(root.wrapper);
    const general = Object.fromEntries(Object.entries(schema).filter(([key, item]) => !['server_root', 'tps_missing_value', 'spark_file_max_age'].includes(key) && !['object', 'template_list'].includes(item.type)));
    addFields('general-fields', general, draft, 'general-');
    addFields('command-fields', schema.commands.items, draft.commands, 'command-');
    addFields('help-field', { help: schema.messages.items.help }, draft.messages, 'message-');
    addFields('message-fields', schema.messages.items, draft.messages, 'message-', new Set(['help', 'tps', 'tps_warning', 'tps_error']));
    addFields('spark-fields', { tps_missing_value: schema.tps_missing_value, spark_file_max_age: schema.spark_file_max_age }, draft, 'spark-');
    const tps = field('tps', schema.messages.items.tps, draft.messages.tps, { id: 'tps-template-editor', onChange: value => { draft.messages.tps = value; touch(); } });
    $('tps-template-field').replaceChildren(tps.wrapper);
    addFields('spark-message-fields', { tps_warning: schema.messages.items.tps_warning, tps_error: schema.messages.items.tps_error }, draft.messages, 'message-');
    $('tps-variables').replaceChildren();
    for (const variable of variables) {
      const row = el('tr');
      for (const value of [variable.placeholder, variable.description, variable.unit || '—', variable.example]) row.append(el('td', value));
      $('tps-variables').append(row);
    }
    renderEntities();
    if (!jsonDirty) $('config-editor').value = JSON.stringify(draft, null, 2);
    changeSection(activeSection);
    syncState();
  }
  function button(text, handler, className = 'secondary') {
    const control = el('button', text, className);
    control.type = 'button';
    control.addEventListener('click', handler);
    return control;
  }
  function renderEntities() {
    const servers = $('configured-servers'); servers.replaceChildren();
    draft.servers.forEach((server, index) => {
      const row = el('article', undefined, 'configured-row'); row.dataset.id = server.id;
      const info = el('div'); info.append(el('h4', server.name || server.id), el('p', `${server.id} · ${server.directory} · RCON ${server.rcon_host}:${server.rcon_port}`, 'subtle'));
      const group = draft.groups.find(g => g.server_ids.includes(server.id));
      info.append(el('p', group ? `绑定群：${group.group_id}（${group.id}）` : '尚未绑定群', 'subtle'));
      const controls = el('div', undefined, 'buttons');
      controls.append(button('编辑服务器', () => openEntity('server', index)), button('移除服务器', () => removeEntity('server', index), 'danger'));
      row.append(info, controls); servers.append(row);
    });
    if (!draft.servers.length) servers.append(el('div', '还没有服务器。填写共同父目录，然后点击“添加服务器”。', 'empty'));
    const groups = $('configured-groups'); groups.replaceChildren();
    draft.groups.forEach((group, index) => {
      const row = el('article', undefined, 'configured-row'); row.dataset.id = group.id;
      const info = el('div'); info.append(el('h4', `群 ${group.group_id}`), el('p', `${group.id} · 平台 ${group.platform_id} · 每人 ${group.max_entries_per_user} 条`, 'subtle'));
      info.append(el('p', `服务器：${group.server_ids.join('、')} · 管理员：${group.admin_ids.join('、') || 'AstrBot 全局管理员'}`, 'subtle'));
      const controls = el('div', undefined, 'buttons');
      controls.append(button('编辑群聊', () => openEntity('group', index)), button('移除群绑定', () => removeEntity('group', index), 'danger'));
      row.append(info, controls); groups.append(row);
    });
    if (!draft.groups.length) groups.append(el('div', '还没有群绑定。添加服务器后，点击“添加群聊”。', 'empty'));
  }
  function unusedId(prefix, rows, reserved = []) {
    let number = 1;
    while (rows.some(row => row.id === prefix + number) || reserved.includes(prefix + number)) number++;
    return prefix + number;
  }
  function serverChoices(group) {
    const wrapper = el('fieldset', undefined, 'server-choices full-width');
    wrapper.append(el('legend', '本群服务器（可多选）'));
    const choices = [];
    for (const server of draft.servers) {
      const selectedGroup = draft.groups.find(g => g.id !== group.id && g.server_ids.includes(server.id));
      const history = bindings.servers.find(b => b.server === server.id && b.scope !== group.id);
      const locked = selectedGroup || history;
      const label = el('label', undefined, 'server-choice');
      const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.value = server.id;
      checkbox.checked = group.server_ids.includes(server.id);
      checkbox.disabled = Boolean(locked);
      const name = el('span', `${server.name}（${server.id}）`);
      if (locked) name.append(el('small', `归属 ${selectedGroup?.id || history.scope}`, 'field-hint'));
      label.append(checkbox, name); wrapper.append(label); choices.push(checkbox);
    }
    wrapper.append(el('p', '每台服务器只归属一个群；同一群下的 UUID 模式必须一致。', 'field-hint'));
    return { wrapper, read: () => choices.filter(c => c.checked && !c.disabled).map(c => c.value) };
  }
  function openEntity(kind, index = -1) {
    try { commitJson(); } catch (error) { notice(error.message, true); return; }
    const collection = kind === 'server' ? draft.servers : draft.groups;
    const items = kind === 'server' ? serverItems() : groupItems();
    const row = index >= 0 ? copy(collection[index]) : defaults(items);
    if (index < 0) {
      row.id = unusedId(kind === 'server' ? 'server' : 'group', collection, kind === 'server' ? bindings.servers.map(b => b.server) : bindings.groups.map(b => b.id));
      row.__template_key = kind;
      if (kind === 'server') {
        row.name = '服务器 ' + row.id.replace('server', ''); row.directory = row.id;
        let port = 25575; while (draft.servers.some(s => s.rcon_port === port)) port++; row.rcon_port = port;
      } else row.platform_id = platforms[0]?.id || '';
    }
    const fields = new Map();
    entity = { kind, index, row, fields };
    const container = $('entity-fields'); container.replaceChildren();
    $('entity-error').hidden = true;
    $('entity-title').textContent = `${index < 0 ? '添加' : '编辑'}${kind === 'server' ? '服务器' : '群聊'}`;
    $('entity-intro').textContent = kind === 'server' ? '目录填写相对于共同父目录的文件夹。密码留空可保留已保存的密码。' : '选择消息平台，填写群号和管理员账号，再勾选允许本群操作的服务器。';
    const persisted = kind === 'group' ? bindings.groups.find(g => g.id === row.id) : index >= 0 && JSON.parse(saved).servers.some(s => s.id === row.id);
    for (const [key, item] of Object.entries(items)) {
      if (kind === 'group' && key === 'server_ids') {
        const choices = serverChoices(row); fields.set(key, choices); container.append(choices.wrapper); continue;
      }
      const locked = Boolean(persisted) && (key === 'id' || kind === 'group' && ['platform_id', 'group_id'].includes(key));
      const descriptor = copy(item);
      if (kind === 'group' && key === 'platform_id' && platforms.length && !locked) {
        descriptor.options = [...new Set([row.platform_id, ...platforms.map(p => p.id)].filter(Boolean))];
      }
      const entry = field(key, descriptor, row[key], { id: 'entity-' + key, readonly: locked, required: entityRequired.has(key), passwordKept: key === 'rcon_password' && row[key] === '__KEEP__' });
      if (locked) entry.wrapper.append(el('span', '已保存的身份绑定保持固定；要添加其他群或服务器，请使用新的配置 ID。', 'field-hint'));
      if (kind === 'group' && key === 'platform_id' && descriptor.options) {
        for (const option of entry.control.options) {
          const platform = platforms.find(p => p.id === option.value);
          option.textContent = platform ? `${platform.display_name} · ${platform.id}` : option.value + '（当前配置）';
        }
      }
      if (kind === 'group' && key === 'platform_id' && descriptor.options) {
        const manualOption = el('option', '手动填写其他平台实例 ID'); manualOption.value = '__manual__'; entry.control.append(manualOption);
        const manual = el('input'); manual.type = 'text'; manual.id = 'entity-platform-manual'; manual.placeholder = '填写 /sid 回复中的 Bot ID'; manual.hidden = true; manual.required = false;
        entry.control.addEventListener('change', () => { manual.hidden = entry.control.value !== '__manual__'; manual.required = !manual.hidden; });
        entry.wrapper.append(manual);
        const read = entry.read; entry.read = () => entry.control.value === '__manual__' ? manual.value.trim() : read();
      }
      if (key === 'rcon_password' && row[key] === '__KEEP__') {
        const clearLabel = el('label', undefined, 'inline-check');
        const clear = el('input'); clear.type = 'checkbox'; clear.id = 'entity-clear-password';
        clearLabel.append(clear, el('span', '清除已保存的密码')); entry.wrapper.append(clearLabel);
        const read = entry.read; entry.read = () => clear.checked ? '' : read();
      }
      fields.set(key, entry); container.append(entry.wrapper);
    }
    if (kind === 'group') {
      fields.get('id').control.addEventListener('input', () => {
        const next = { ...row, id: fields.get('id').read(), server_ids: fields.get('server_ids').read() };
        const choices = serverChoices(next); fields.get('server_ids').wrapper.replaceWith(choices.wrapper); fields.set('server_ids', choices);
      });
    }
    $('config-dialog').showModal();
  }
  function validateEntity(candidate) {
    const { kind, index } = entity;
    const rows = kind === 'server' ? draft.servers : draft.groups;
    if (rows.some((row, i) => i !== index && row.id === candidate.id)) throw new Error('配置 ID 已存在，请换一个 ID。');
    if (kind === 'server') {
      if (!draft.server_root.trim()) throw new Error('请先填写服务器共同父目录。');
      if (rows.some((row, i) => i !== index && row.rcon_host.toLowerCase() === candidate.rcon_host.toLowerCase() && row.rcon_port === candidate.rcon_port)) throw new Error('RCON 地址和端口已被其他服务器使用。');
      if (candidate.rcon_password === '__KEEP__' && index < 0) throw new Error('新服务器需要实际密码或密码环境变量。');
    } else {
      const previous = bindings.groups.find(g => g.id === candidate.id);
      if (previous && (previous.platform !== candidate.platform_id || previous.group_id !== candidate.group_id)) throw new Error('此配置 ID 已有其他群的历史，请使用新的群配置 ID。');
      if (!candidate.server_ids.length) throw new Error('至少勾选一台属于本群的服务器。');
      if (rows.some((row, i) => i !== index && row.platform_id === candidate.platform_id && row.group_id === candidate.group_id)) throw new Error('该平台中的群号已经绑定。');
      const servers = draft.servers.filter(server => candidate.server_ids.includes(server.id));
      if (new Set(servers.map(s => s.online_mode)).size > 1) throw new Error('本群的服务器需要使用相同 UUID 模式。');
    }
  }
  $('entity-form').addEventListener('submit', event => {
    event.preventDefault();
    try {
      const candidate = copy(entity.row);
      for (const [key, entry] of entity.fields) candidate[key] = entry.read();
      validateEntity(candidate);
      const rows = entity.kind === 'server' ? draft.servers : draft.groups;
      if (entity.index < 0) rows.push(candidate); else rows[entity.index] = candidate;
      $('config-dialog').close(); entity = null; touch(); renderEntities();
      notice('已加入配置草稿，点击“保存并生效”应用。');
    } catch (error) { $('entity-error').textContent = error.message; $('entity-error').hidden = false; }
  });
  for (const id of ['entity-cancel', 'entity-close']) $(id).addEventListener('click', () => $('config-dialog').close());
  $('config-dialog').addEventListener('close', () => { entity = null; });
  async function removeEntity(kind, index) {
    try {
      commitJson();
      const rows = kind === 'server' ? draft.servers : draft.groups;
      const row = rows[index];
      if (kind === 'server') {
        const group = draft.groups.find(g => g.server_ids.includes(row.id));
        if (group) throw new Error(`服务器仍绑定群 ${group.group_id}，请先编辑或移除该群绑定。`);
      }
      const prompt = kind === 'server' ? `从配置中移除 ${row.name}（${row.id}）？服务端文件和历史数据会保留。` : `移除群 ${row.group_id} 的绑定？历史白名单、归属和日志会保留，保存后此群将停止使用 MC 功能。`;
      if (!await confirmAction(prompt)) return;
      rows.splice(index, 1); touch(); renderEntities();
      notice('已修改配置草稿，点击“保存并生效”应用。');
    } catch (error) { notice(error.message, true); }
  }
  $('add-server').addEventListener('click', () => openEntity('server'));
  $('add-group').addEventListener('click', () => openEntity('group'));
  for (const button of document.querySelectorAll('[data-settings-tab]')) button.addEventListener('click', () => changeSection(button.dataset.settingsTab));
  $('config-editor').addEventListener('input', () => { jsonDirty = true; editVersion++; syncState(); });
  $('apply-json').addEventListener('click', () => {
    try { commitJson(); notice('JSON 已应用到表单；检查配置后点击“保存并生效”。'); }
    catch (error) { notice(error.message, true); }
  });
  $('spark-defaults').addEventListener('click', () => {
    try {
      commitJson(); draft.messages.tps = defaultTps;
      for (const server of draft.servers) server.tps_command = 'spark tps';
      touch(); render(); notice('已恢复 spark 默认命令和 TPS 模板，点击“保存并生效”应用。');
    } catch (error) { notice(error.message, true); }
  });
  $('preview-help').addEventListener('click', () => {
    const context = {};
    for (const [key, value] of Object.entries(draft.commands)) context[key] = list(value)[0] || '';
    context.prefix = '/' + context.prefix;
    $('help-preview').textContent = draft.messages.help.replace(/\{\{|\}\}|\{([A-Za-z0-9_]+)\}/g, (whole, name) => whole === '{{' ? '{' : whole === '}}' ? '}' : context[name] ?? whole);
    $('help-preview').hidden = false;
  });
  function validateFields() {
    for (const input of document.querySelectorAll('#settings input, #settings textarea, #settings select')) {
      if (input.id === 'config-editor' || input.checkValidity()) continue;
      const section = input.closest('.settings-section'); if (section) changeSection(section.id.replace('settings-', ''));
      input.reportValidity(); throw new Error('请检查表单中标出的配置项。');
    }
    if (draft.servers.length && !draft.server_root.trim()) throw new Error('请填写服务器共同父目录。');
  }
  $('save-config').addEventListener('click', () => work(async () => {
    commitJson(); validateFields();
    const submittedVersion = editVersion;
    const result = await api('apiPost', 'config', { config: copy(draft), revision });
    if (submittedVersion !== editVersion) {
      // Preserve edits made while the request was in flight and only refresh
      // revision/password redaction for the successfully persisted snapshot.
      const latest = await api('apiGet', 'config');
      revision = latest.revision; saved = JSON.stringify(normalize(latest.config));
      notice('配置已保存；请求期间的新修改仍在草稿中，请再次保存。');
      syncState();
    } else { await load(true); notice(result.message); }
    output(result.message); await refresh();
  }));
  $('reload-config').addEventListener('click', async () => {
    if (hasChanges() && !await confirmAction('放弃本页未保存的配置修改，重新载入已保存配置？')) return;
    await work(async () => { await load(true); notice('已重新载入保存的配置。'); });
  });
  window.addEventListener('beforeunload', event => { if (hasChanges()) { event.preventDefault(); event.returnValue = ''; } });
  async function load(force = false) {
    if (loaded && !force) return;
    const response = await api('apiGet', 'config');
    schema = response.schema;
    if (!schema?.servers?.templates || !schema?.groups?.templates) throw new Error('管理页面需要新版配置接口，请重载插件后刷新页面。');
    platforms = response.platforms || [];
    bindings = response.bindings || { groups: [], servers: [] };
    variables = response.tps_variables || []; defaultTps = response.tps_default_template || '';
    revision = response.revision || '';
    draft = normalize(response.config);
    saved = JSON.stringify(draft); jsonDirty = false; loaded = true; editVersion++;
    $('data-directory').textContent = '插件数据库与日志：' + response.data_directory;
    render();
  }
  return { load, setBusy: value => { busy = value; if (loaded) syncState(); }, hasChanges };
}
