import { createQuickSetup } from "./quick-setup.js";
const $ = id => document.getElementById(id);
const copy = value => JSON.parse(JSON.stringify(value));
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const list = value => Array.isArray(value) ? value.map(String) : String(value || '').split(/[,，\r\n]+/).map(v => v.trim()).filter(Boolean);
const serverIds = value => (Array.isArray(value) ? value : [value]).flatMap(item => String(item ?? '').split(/[,，\r\n]+/)).map(item => item.trim()).filter(Boolean);
const ranges = { page_size: [1, 100], sync_interval: [5, 3600], rcon_timeout: [1, 30], restart_timeout: [5, 3600], poll_interval: [1, 30], rcon_port: [1, 65535], max_entries_per_user: [1, 100] };
Object.assign(ranges, {"notification_max_attempts":[1,100],"notification_retry_seconds":[0,3600],"notification_max_age_hours":[1,720],"background_retry_seconds":[1,300],"disk_warning_mb":[0,1048576],"backup_interval_hours":[0,8760],"backup_keep":[1,100],"logs_retention_days":[0,3650],"logs_max_entries":[0,10000000],"alert_consecutive":[1,100],"alert_cooldown":[10,86400],"alert_tps_threshold":[1,20],"query_cache_seconds":[0,60],"query_user_cooldown":[0,300],"query_group_cooldown":[0,300],"query_concurrency":[1,64],"restart_countdown":[0,3600]});
Object.assign(ranges, {bridge_poll_seconds:[1,60],bridge_max_chars:[32,2000],bridge_cooldown_seconds:[0,300]});
Object.assign(ranges, {spark_log_wait_seconds:[1,15]});
Object.assign(ranges, {query_timeout_seconds:[0,300],tps_capability_cache_seconds:[0,86400]});
const entityRequired = new Set(['id', 'name', 'directory', 'rcon_host', 'rcon_port', 'platform_id', 'group_id', 'max_entries_per_user', 'tps_command', 'players_command']);
const commonGeneral=new Set(['page_size','sync_interval','restart_countdown','game_broadcast','approval_required','alerts_enabled']);
const commonCommands=new Set(['prefix','add','list','remove','modify','mine','players','tps','help']);
const basicServer=new Set(['name','directory','rcon_host','rcon_port','rcon_password','online_mode']);
const basicGroup=new Set(['platform_id','group_id','server_ids','admin_ids','max_entries_per_user']);
const outputLabels={template:'机器人变量模板（可自由修改）',raw:'完整原文'};

function el(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

export function createSettings({ api, work, notice, output, confirmAction, refresh, onQuickSaved }) {
  let loaded = false;
  let configError = '';
  let schema = {};
  let formConfig = {};
  let saved = '';
  let revision = '';
  let jsonDirty = false;
  let activeSection = 'connections';
  let platforms = [];
  let bindings = { groups: [], servers: [] };
  let defaultTps = '';
  let variables = [];
  let tpsExamples = [];
  let messageTemplates = [];
  let messageLimit = 8;
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
      if (Array.isArray(group.server_ids) && group.server_ids.some(item => typeof item !== 'string' && !(typeof item === 'number' && Number.isInteger(item)))) throw new Error('服务器 ID 列表只能包含文本或整数，多个 ID 用逗号或换行分隔。');
      if (group.group_id != null && !Array.isArray(group.group_id) && !['string', 'number'].includes(typeof group.group_id)) throw new Error('群号必须是文本、数字或群号列表。');
      for (const key of ['server_ids', 'admin_ids']) if (group[key] !== undefined && !Array.isArray(group[key]) && typeof group[key] !== 'string') throw new Error(`${key} 必须是账号/ID 列表。`);
    }
  }
  function normalize(value) {
    checkShape(value);
    const result = defaults(schema, value);
    delete result.spark_file_max_age;
    result.servers = (result.servers || []).map(row => {
      const server = defaults(serverItems(), row);
      delete server.tps_source; delete server.spark_file; delete server.tps_mode;
      if (row.tps_command === undefined) server.tps_command = row.tps_mode === 'native' ? 'tps' : 'spark tps';
      return server;
    });
    result.groups = (result.groups || []).map(row => {
      const group = defaults(groupItems(), row);
      group.group_id = [...new Set(list(group.group_id))].join(",");
      group.server_ids = serverIds(group.server_ids);
      group.admin_ids = list(group.admin_ids);
      return group;
    });
    return result;
  }
  function serverItems() { return schema.servers?.templates?.server?.items || {}; }
  function groupItems() { return schema.groups?.templates?.group?.items || {}; }
  function hasChanges() { return jsonDirty || (loaded && JSON.stringify(formConfig) !== saved); }
  function syncState() {
    $('config-state').textContent = hasChanges() ? '有未保存的修改' : configError ? '配置待修正' : '配置已保存';
    $('config-state').classList.toggle('unsaved', hasChanges());
    $('save-config').disabled = busy || !loaded;
    $('reload-config').disabled = busy || !loaded;
    $('add-server').disabled = busy || !loaded;
    $('add-group').disabled = busy || !loaded || !formConfig.servers?.length;
    $('spark-defaults').disabled = busy || !loaded;
    $('variable-save').disabled = busy || !loaded;
    $('entity-save').disabled = busy || !loaded;
    $('entity-test').disabled = busy || !loaded;
    if(loaded) renderSetup();
    $('save-config').hidden=activeSection==='connections' && !$('manual-settings').open && !hasChanges();
    $('settings-note').textContent=activeSection==='connections' && !$('manual-settings').open ? '填写目录和群号，扫描确认后点击“保存并完成配置”。' : '修改后点击“保存并生效”。';
    quickSetup.setBusy(busy || !loaded);
  }
  function renderSetup() {
    const rootReady=Boolean(String(formConfig.server_root || '').trim());
    const serverReady=Boolean(formConfig.servers?.length);
    const groupReady=Boolean(formConfig.groups?.some(group=>group.group_id && group.platform_id && group.server_ids?.length));
    const complete=rootReady && serverReady && groupReady;
    const steps=$('setup-steps');steps.replaceChildren();
    for(const [label,done] of [['服务端目录',rootReady],['添加服务器',serverReady],['绑定群聊',groupReady]]){
      const item=el('li',(done?'✓ ':'')+label);item.classList.toggle('done',done);steps.append(item);
    }
    $('setup-title').textContent=complete?'配置已就绪':'三步完成配置';
    $('setup-description').textContent=complete ? (hasChanges()?'还有未保存的修改，保存后可在群内发送 /mc 使用。':'在绑定的群内发送 /mc 即可使用；新增服务器和群聊可直接保存。') : '按顺序填写即可。内部 ID 自动生成，其他选项可保持默认。';
    $('setup-next').textContent=!rootReady?'填写服务端目录':!serverReady?'添加第一台服务器':!groupReady?'绑定群聊':hasChanges()?'保存并开始使用':'查看群内用法';
    $('setup-next').disabled=busy;
  }
  $('setup-next').addEventListener('click',async()=>{
    if(!String(formConfig.server_root || '').trim()) {$('setting-server-root').focus();return;}
    if(!formConfig.servers.length) {openEntity('server');return;}
    const invalid=formConfig.groups.findIndex(group=>!group.group_id || !group.platform_id || !group.server_ids?.length);
    if(!formConfig.groups.length || invalid>=0) {openEntity('group',invalid);return;}
    if(hasChanges()) {await work(saveConfig);return;}
    changeSection('commands');$('message-help').focus();notice('在绑定群发送 /mc 查看常用指令；/mc 帮助 all 查看全部指令。');
  });
  function touch() {
    editVersion++;
    // An incomplete advanced edit remains pending; form changes must not erase it.
    if (!jsonDirty) $('config-editor').value = JSON.stringify(formConfig, null, 2);
    syncState();
  }
  function commitJson() {
    if (!jsonDirty) return;
    let value;
    try { value = JSON.parse($('config-editor').value); }
    catch { throw new Error('配置不是有效的 JSON，请检查逗号、引号和花括号。'); }
    formConfig = normalize(value);
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
    syncState();
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
        const option = el('option', key === 'tps_output' ? outputLabels[optionValue] || optionValue : String(optionValue));
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
      if (inputId.startsWith('message-') || inputId.startsWith('all-message-') || inputId === 'tps-template-editor') {
        control.dataset.messageKey = key;
        control.addEventListener('input', () => {
          for (const peer of document.querySelectorAll('[data-message-key]')) if (peer !== control && peer.dataset.messageKey === key) peer.value = control.value;
        });
      }
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
    if(key==='tps_command') {
      const presets=el('div',undefined,'command-presets');
      for(const command of ['spark tps','tps','forge tps','tick query']) {
        const choose=el('button',command,'secondary');choose.type='button';choose.title='填入查询指令';
        choose.addEventListener('click',()=>{control.value=command;control.dispatchEvent(new Event('input',{bubbles:true}));control.focus();});presets.append(choose);
      }
      wrapper.append(presets);
    }
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
  function renderMessages() {
    const category = $('message-category').value;
    const needle = $('message-search').value.trim().toLowerCase();
    const container = $('message-fields'); container.replaceChildren();
    const selected = messageTemplates.filter(row => (!category || row.category === category) && (!needle || [row.key, row.default, formConfig.messages[row.key], ...row.variables.map(item => item.name)].join(' ').toLowerCase().includes(needle)));
    const priority=['help','added','removed','modified','list','entry','error','servers'];
    const rank=key=>{const index=priority.indexOf(key);return index<0?priority.length:index;};
    selected.sort((a,b)=>rank(a.key)-rank(b.key));
    $('message-count').textContent = `显示 ${Math.min(messageLimit,selected.length)} 项，匹配 ${selected.length} 项 · 全部 ${messageTemplates.length} 项`;
    $('message-more').hidden=selected.length<=messageLimit;
    if (!selected.length) container.append(el('p', '未找到匹配文案，可选择“全部分类”后重试。', 'subtle'));
    for (const row of selected.slice(0,messageLimit)) {
      const spec = schema.messages.items[row.key];
      const shared = ['help', 'tps', 'tps_warning', 'tps_error'].includes(row.key) || row.key.startsWith('bridge_');
      const entry = field(row.key, spec, formConfig.messages[row.key], {id: (shared ? 'all-message-' : 'message-') + row.key, onChange: value => {formConfig.messages[row.key] = value; touch();}});
      entry.wrapper.classList.add('message-template');
      entry.wrapper.prepend(el('code', 'messages.' + row.key, 'template-key'));
      const variables = el('div', undefined, 'template-variables');
      for (const variable of row.variables) {
        const button = el('button', '{' + variable.name + '}', 'secondary variable-token'); button.type = 'button'; button.title = variable.description;
        button.addEventListener('click', () => {
          const token = '{' + variable.name + '}';
          entry.control.setRangeText(token, entry.control.selectionStart, entry.control.selectionEnd, 'end');
          entry.control.dispatchEvent(new Event('input', {bubbles:true})); entry.control.focus();
        }); variables.append(button);
      }
      if (!row.variables.length) variables.append(el('span', '纯文本，无必需变量', 'field-hint'));
      const actions = el('div', undefined, 'template-actions');
      const restore = el('button', '恢复此项默认', 'secondary'); restore.type = 'button';
      restore.addEventListener('click', async () => {
        if (!await confirmAction('恢复此项模板的默认文字？其他修改不受影响。')) return;
        entry.control.value = row.default; entry.control.dispatchEvent(new Event('input', {bubbles:true}));
      }); actions.append(restore);
      entry.wrapper.append(variables, actions); container.append(entry.wrapper);
    }
  }
  $('message-search').addEventListener('input', ()=>{messageLimit=8;renderMessages();});
  $('message-category').addEventListener('change', ()=>{messageLimit=8;renderMessages();});
  $('message-more').addEventListener('click',()=>{messageLimit+=8;renderMessages();});
  function renderTpsServers() {
    const container = $('tps-server-fields'); container.replaceChildren();
    if (!formConfig.servers.length) container.append(el('p', '先在“服务器与群聊”中添加服务器，再设置 TPS 查询指令。', 'subtle'));
    for (const server of formConfig.servers) {
      const block = el('div', undefined, 'tps-server-card');
      block.append(el('h4', `${server.name || server.id}（${server.id}）`));
      const grid = el('div', undefined, 'field-grid');
      const command = field('tps_command', serverItems().tps_command, server.tps_command, { id: `tps-${server.id}-command`, required: true, onChange: value => { server.tps_command = value; touch(); } });
      command.control.placeholder = 'spark tps / tps / forge tps / tick query';
      const detect = field('tps_auto_detect', serverItems().tps_auto_detect, server.tps_auto_detect, {id:`tps-${server.id}-auto-detect`,onChange:value=>{server.tps_auto_detect=value;touch();}});
      const output=field('tps_output',serverItems().tps_output,server.tps_output,{id:`tps-${server.id}-output`,required:true,onChange:value=>{server.tps_output=value;touch();render();}});
      grid.append(command.wrapper,output.wrapper,detect.wrapper);
      const logOptions=el('details',undefined,'spark-log-options');
      logOptions.append(el('summary','spark 无回复时：新增日志回读（无需 Java 采集器）'));
      const logGrid=el('div',undefined,'field-grid');
      for(const key of ['spark_log_fallback','spark_log_path','spark_log_encoding','spark_log_wait_seconds']) {
        logGrid.append(field(key,serverItems()[key],server[key],{id:`tps-${server.id}-${key}`,onChange:value=>{server[key]=value;touch();}}).wrapper);
      }
      logOptions.append(logGrid);
      block.append(grid,logOptions); container.append(block);
    }
  }
  const scopeLabels = {spark:'TPS 变量模板',status:'状态',players:'玩家'};
  const formatLabels={spark:'spark',paper:'Paper/Spigot',forge:'Forge/NeoForge',tick:'原版 tick query',custom:'自定义回复',status:'状态/玩家'};
  function renderVariables() {
    const search = $('variable-search').value.trim().toLowerCase();
    const group = $('variable-group').value, scope = $('variable-scope').value, format = $('variable-format').value;
    if(search || group || scope || format) $('tps-variable-details').open=true;
    const rows = variables.filter(variable => (!format || (variable.formats || []).includes(format)) && (!group || (variable.group || variable.category) === group) && (!scope || (variable.scopes || ['spark']).includes(scope)) && (!search || [variable.name,variable.description,variable.unit,variable.group].join(' ').toLowerCase().includes(search)));
    const table = $('tps-variables'); table.replaceChildren();
    for (const variable of rows) {
      const row = el('tr'), token = el('td');
      token.append(button(variable.placeholder, () => {
        const editor = $('variable-preview-template');
        editor.setRangeText(variable.placeholder, editor.selectionStart, editor.selectionEnd, 'end');
        editor.focus(); updateVariablePreview();
      }, 'variable-token'));
      row.append(token);
      for (const value of [(variable.group || variable.category) + ' / ' + (variable.scopes || ['spark']).map(scope=>scopeLabels[scope] || scope).join('、') + ' · 来源：' + (variable.formats || []).map(format=>formatLabels[format] || format).join('、'),variable.description,variable.unit || '—',variable.example]) row.append(el('td',value));
      table.append(row);
    }
    $('variable-count').textContent = `显示 ${rows.length} / ${variables.length} 个变量。点击变量可插入下方预览模板。`;
  }
  function selectedTpsExample() { return tpsExamples.find(example=>example.id===$('variable-example-kind').value) || tpsExamples[0]; }
  function updateVariablePreview(reset=false) {
    const kind=$('variable-preview-kind').value,scope=kind==='tps'?'spark':kind;
    const editor=$('variable-preview-template'),missing=formConfig.tps_missing_value || '暂无数据';
    if(reset) editor.value=formConfig.messages[kind] || '';
    $('variable-example-kind').disabled=kind!=='tps';
    let samples;
    if(kind==='tps') {
      samples={...Object.fromEntries(variables.map(variable=>[variable.name,missing])),...(selectedTpsExample()?.preview || {})};
      for(const name of Object.keys(samples)) if(samples[name]==='暂无数据') samples[name]=missing;
    } else {
      samples=Object.fromEntries(variables.map(variable=>[variable.name,String(variable.example ?? '')]));
      Object.assign(samples,{query_source:'RCON',query_command:'list',query_error:'',query_warning:'',result:kind==='status'?'在线（RCON 查询成功）':samples.players_raw});
    }
    const unavailable=new Set();
    $('variable-preview-result').textContent=editor.value.replace(/\{\{|\}\}|\{([^{}]+)\}/g,(whole,name)=>{
      if(whole==='{{') return '{';if(whole==='}}') return '}';
      const variable=variables.find(item=>item.name===name);
      if(!variable || !(variable.scopes || ['spark']).includes(scope)) unavailable.add(name);
      return variable && (variable.scopes || ['spark']).includes(scope) ? samples[name] ?? missing : missing;
    });
    const unpaired=/[{}]/.test(editor.value.replace(/\{\{|\}\}|\{[^{}]*\}/g,''));
    $('variable-preview-warning').textContent=unpaired?'模板花括号不完整；字面花括号请写 {{ 和 }}。':unavailable.size?'当前模板未提供这些变量：'+[...unavailable].join('、'):'预览采用所选格式的真实形状样例；该格式未提供的变量显示缺失值，不会跨核心补造。';
  }
  for(const id of ['variable-search','variable-group','variable-scope','variable-format']) $(id).addEventListener(id==='variable-search'?'input':'change',renderVariables);
  $('variable-preview-kind').addEventListener('change',()=>updateVariablePreview(true));
  $('variable-preview-template').addEventListener('input',()=>updateVariablePreview());
  $('variable-example-kind').addEventListener('change',()=>{
    if($('variable-preview-kind').value==='tps') $('variable-preview-template').value=selectedTpsExample()?.template || '';
    updateVariablePreview();
  });
  $('variable-example').addEventListener('click',()=>{
    const kind=$('variable-preview-kind').value;
    $('variable-preview-template').value=kind==='tps'?selectedTpsExample()?.template || schema.messages.items.tps.default:({status:'[{server}] {status}\n在线：{players_online}/{players_max}\n玩家：{players_names}\n来源：{query_source} · {query_latency_ms} ms · 缓存：{query_cached}',players:'[{server}] 在线：{players_online}/{players_max}\n玩家：{players_names}\n耗时：{query_latency_ms} ms'})[kind];
    updateVariablePreview();
  });
  function render() {
    const root = field('server_root', schema.server_root, formConfig.server_root, { id: 'setting-server-root', onChange: value => { formConfig.server_root = value; touch(); } });
    $('root-field').replaceChildren(root.wrapper);
    const general = Object.fromEntries(Object.entries(schema).filter(([key, item]) => !key.startsWith('bridge_') && !['server_root', 'tps_missing_value'].includes(key) && !['object', 'template_list'].includes(item.type)));
    addFields('general-fields',Object.fromEntries(Object.entries(general).filter(([key])=>commonGeneral.has(key))),formConfig,'general-');
    addFields('general-advanced-fields',Object.fromEntries(Object.entries(general).filter(([key])=>!commonGeneral.has(key))),formConfig,'general-');
    addFields('bridge-fields', Object.fromEntries(Object.entries(schema).filter(([key]) => key.startsWith('bridge_'))), formConfig, 'bridge-');
    const bridgeGroups = $('bridge-group-fields'); bridgeGroups.replaceChildren();
    if (!formConfig.groups.length) bridgeGroups.append(el('p', '先在“服务器与群聊”添加群绑定，再开启本组互通。', 'subtle'));
    formConfig.groups.forEach((group, index) => {
      const block = el('div', undefined, 'config-block');
      block.append(el('h4', `群 ${group.group_id || group.id}`));
      const fields = el('div', undefined, 'field-grid');
      for (const [key, spec] of Object.entries(groupItems()).filter(([key]) => key.startsWith('bridge_'))) {
        fields.append(field(key, spec, group[key], {id: `bridge-group-${index}-${key}`, onChange: value => { group[key] = value; if(key==='bridge_enabled' && value){formConfig.bridge_enabled=true;$('bridge-bridge_enabled').checked=true;} touch(); }}).wrapper);
      }
      block.append(fields); bridgeGroups.append(block);
    });
    addFields('bridge-message-fields', Object.fromEntries(Object.entries(schema.messages.items).filter(([key]) => key.startsWith('bridge_'))), formConfig.messages, 'message-');
    addFields('command-fields',Object.fromEntries(Object.entries(schema.commands.items).filter(([key])=>commonCommands.has(key))),formConfig.commands,'command-');
    addFields('command-advanced-fields',Object.fromEntries(Object.entries(schema.commands.items).filter(([key])=>!commonCommands.has(key))),formConfig.commands,'command-');
    addFields('help-field', { help: schema.messages.items.help }, formConfig.messages, 'message-');
    renderMessages();
    addFields('spark-fields', { tps_missing_value: schema.tps_missing_value }, formConfig, 'spark-');
    const tps = field('tps', schema.messages.items.tps, formConfig.messages.tps, { id: 'tps-template-editor', onChange: value => { formConfig.messages.tps = value; touch(); } });
    $('tps-template-field').replaceChildren(tps.wrapper);
    addFields('spark-message-fields', { tps_warning: schema.messages.items.tps_warning, tps_error: schema.messages.items.tps_error }, formConfig.messages, 'message-');
    renderTpsServers();
    const hasTemplate=formConfig.servers.some(server=>server.tps_output==='template');
    $('spark-template-hint').textContent=hasTemplate ? '以下模板适用于选择“自定义变量模板”的服务器，各查询核心都可使用。' : '当前服务器使用原文；可在 TPS 回复方式选择变量模板后编辑。';
    for(const id of ['tps-template-editor','spark-tps_missing_value','message-tps_warning','message-tps_error']) $(id).disabled=!hasTemplate;
    renderVariables();
    updateVariablePreview(true);
    renderEntities();
    quickSetup.update({config:formConfig,platforms,revision});
    if (!jsonDirty) $('config-editor').value = JSON.stringify(formConfig, null, 2);
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
    formConfig.servers.forEach((server, index) => {
      const row = el('article', undefined, 'configured-row'); row.dataset.id = server.id;
      const info = el('div'); info.append(el('h4', server.name || server.id), el('p', `${server.id} · ${server.directory} · RCON ${server.rcon_host}:${server.rcon_port}`, 'subtle'));
      const groups = formConfig.groups.filter(g => g.server_ids.includes(server.id));
      info.append(el('p', groups.length ? `绑定群：${groups.map(group => `${group.group_id}（${group.id}）`).join('；')}` : '尚未绑定群', 'subtle'));
      const controls = el('div', undefined, 'buttons');
      controls.append(button('编辑服务器', () => openEntity('server', index)), button('移除服务器', () => removeEntity('server', index), 'danger'));
      row.append(info, controls); servers.append(row);
    });
    if (!formConfig.servers.length) servers.append(el('div', '还没有服务器。填写共同父目录，然后点击“添加服务器”。', 'empty'));
    const groups = $('configured-groups'); groups.replaceChildren();
    formConfig.groups.forEach((group, index) => {
      const row = el('article', undefined, 'configured-row'); row.dataset.id = group.id;
      const info = el('div'); info.append(el('h4', `群 ${group.group_id}`), el('p', `${group.id} · 平台 ${group.platform_id} · 每人 ${group.max_entries_per_user} 条`, 'subtle'));
      info.append(el('p', `服务器：${group.server_ids.join('、')} · 管理员：${group.admin_ids.join('、') || 'AstrBot 全局管理员'}`, 'subtle'));
      const controls = el('div', undefined, 'buttons');
      controls.append(button('编辑群聊', () => openEntity('group', index)), button('移除群绑定', () => removeEntity('group', index), 'danger'));
      row.append(info, controls); groups.append(row);
    });
    if (!formConfig.groups.length) groups.append(el('div', '还没有群绑定。添加服务器后，点击“添加群聊”。', 'empty'));
  }
  function unusedId(prefix, rows, reserved = []) {
    let number = 1;
    while (rows.some(row => row.id === prefix + number) || reserved.includes(prefix + number)) number++;
    return prefix + number;
  }
  function serverChoices(group) {
    const wrapper = el('fieldset', undefined, 'server-choices full-width');
    wrapper.append(el('legend', '本配置服务器（可多选、可共享）'));
    const choices = [];
    for (const server of formConfig.servers) {
      const sharedGroups = formConfig.groups.filter((g, index) => index !== (entity?.kind === 'group' ? entity.index : -1) && g.server_ids.includes(server.id));
      const label = el('label', undefined, 'server-choice');
      const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.value = server.id;
      checkbox.checked = group.server_ids.includes(server.id);
      checkbox.disabled = false;
      const name = el('span', `${server.name}（${server.id}）`);
      if (sharedGroups.length) name.append(el('small', `同时供 ${sharedGroups.map(g => g.group_id || g.id).join('、')} 使用`, 'field-hint'));
      label.append(checkbox, name); wrapper.append(label); choices.push(checkbox);
    }
    wrapper.append(el('p', '共享服同步各组白名单并集，各组独立管理条目。同组服务器需使用相同 UUID 模式。', 'field-hint'));
    return { wrapper, read: () => choices.filter(c => c.checked && !c.disabled).map(c => c.value) };
  }
  function openEntity(kind, index = -1) {
    try { commitJson(); } catch (error) { notice(error.message, true); return; }
    const collection = kind === 'server' ? formConfig.servers : formConfig.groups;
    const items = kind === 'server' ? serverItems() : groupItems();
    const row = index >= 0 ? copy(collection[index]) : defaults(items);
    if (index < 0) {
      row.id = unusedId(kind === 'server' ? 'server' : 'group', collection, kind === 'server' ? bindings.servers.map(b => b.server) : bindings.groups.map(b => b.id));
      row.__template_key = kind;
      if (kind === 'server') {
        row.name = '服务器 ' + row.id.replace('server', ''); row.directory = row.id;
        let port = 25575; while (formConfig.servers.some(s => s.rcon_port === port)) port++; row.rcon_port = port;
      } else {row.platform_id=platforms[0]?.id || '';if(formConfig.servers.length===1)row.server_ids=[formConfig.servers[0].id];}
    }
    const fields = new Map();
    entity = { kind, index, row, fields };
    const container = $('entity-fields'); container.replaceChildren();
    const advanced=$('entity-advanced-fields');advanced.replaceChildren();$('entity-advanced').open=false;
    $('entity-test').hidden=kind!=='server';$('entity-test-result').hidden=true;
    $('entity-error').hidden = true;
    $('entity-title').textContent = `${index < 0 ? '添加' : '编辑'}${kind === 'server' ? '服务器' : '群聊'}`;
    $('entity-intro').textContent = kind === 'server' ? '目录填写相对路径，已有密码留空可保留。' : '多个群号用逗号或换行分隔，可重复选择共享服务器。' + (index >= 0 ? '更换平台会清空原用户归属。' : '');
    $('entity-intro').textContent+=' 点击保存后立即生效，取消不保留本次修改。';
    const persisted = kind === 'group' ? bindings.groups.find(g => g.id === row.id) : index >= 0 && JSON.parse(saved).servers.some(s => s.id === row.id);
    if (kind === 'group' && persisted) {
      if (!row.platform_id) row.platform_id = persisted.platform;
      if (!row.group_id) row.group_id = persisted.group_id;
    }
    for (const [key, item] of Object.entries(items)) {
      if (kind === 'group' && key === 'server_ids') {
        const choices = serverChoices(row); fields.set(key, choices); container.append(choices.wrapper); continue;
      }
      const locked = Boolean(persisted) && key === 'id' ;
      const descriptor = copy(item);
      if (kind === 'group' && key === 'platform_id' && platforms.length && !locked) {
        descriptor.options = [...new Set([row.platform_id, ...platforms.map(p => p.id)].filter(Boolean))];
      }
      const entry = field(key, descriptor, row[key], { id: 'entity-' + key, readonly: locked, required: entityRequired.has(key), passwordKept: key === 'rcon_password' && row[key] === '__KEEP__' });
      if (locked) entry.wrapper.append(el('span', '配置 ID 用于关联白名单和日志，保持固定；群号与消息平台可以修改。', 'field-hint'));
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
      fields.set(key, entry); (kind==='server'?basicServer:basicGroup).has(key) ? container.append(entry.wrapper) : advanced.append(entry.wrapper);
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
    const rows = kind === 'server' ? formConfig.servers : formConfig.groups;
    if (rows.some((row, i) => i !== index && row.id === candidate.id)) throw new Error('配置 ID 已存在，请换一个 ID。');
    if (kind === 'server') {
      if (!formConfig.server_root.trim()) throw new Error('请先填写服务器共同父目录。');
      if (rows.some((row, i) => i !== index && row.rcon_host.toLowerCase() === candidate.rcon_host.toLowerCase() && row.rcon_port === candidate.rcon_port)) throw new Error('RCON 地址和端口已被其他服务器使用。');
      if (candidate.rcon_password === '__KEEP__' && index < 0) throw new Error('新服务器需要实际密码或密码环境变量。');
      if(!JSON.parse(saved).servers.some(server=>server.id===candidate.id) && !candidate.rcon_password && !candidate.rcon_password_env?.trim())throw new Error('请填写 RCON 密码，或在更多选项中设置密码环境变量。');
    } else {
      const previous = bindings.groups.find(g => g.id === candidate.id);
      if (previous && (previous.platform !== candidate.platform_id || previous.group_id !== candidate.group_id)) candidate.broadcast_origin = '';
      if (!candidate.server_ids.length) throw new Error('至少勾选一台属于本群的服务器。');
      candidate.group_id = [...new Set(list(candidate.group_id))].join(',');
      const identities = list(candidate.group_id);
      if (!identities.length) throw new Error('至少填写一个群号，多个群号用逗号或换行分隔。');
      if (identities.some(id => /\s/.test(id))) throw new Error('单个群号不能包含空白。');
      if (rows.some((row, i) => i !== index && row.platform_id === candidate.platform_id && list(row.group_id).some(id => identities.includes(id)))) throw new Error('该平台中的群号已经绑定到其他配置。');
      const servers = formConfig.servers.filter(server => candidate.server_ids.includes(server.id));
      if (new Set(servers.map(s => s.online_mode)).size > 1) throw new Error('本群的服务器需要使用相同 UUID 模式。');
    }
  }
  $('entity-form').addEventListener('submit', async event => {
    event.preventDefault();
    if(busy)return;
    try {
      const candidate=copy(entity.row);
      for(const [key,entry] of entity.fields)candidate[key]=entry.read();
      validateEntity(candidate);
      const proposed=copy(formConfig);
      const rows=entity.kind==='server'?proposed.servers:proposed.groups;
      if(entity.kind==='group' && candidate.bridge_enabled && !rows[entity.index]?.bridge_enabled)proposed.bridge_enabled=true;
      if(entity.index<0)rows.push(candidate);else rows[entity.index]=candidate;
      await work(async()=>{
        const controls=[...$('entity-form').querySelectorAll('input,select,textarea')].map(control=>[control,control.disabled]);
        controls.forEach(([control])=>{control.disabled=true;});
        $('entity-error').hidden=true;
        try {
          await saveConfig(proposed);
          $('config-dialog').close();entity=null;
        } catch(error) {
          $('entity-error').textContent=error.message || String(error);$('entity-error').hidden=false;
        } finally {controls.forEach(([control,disabled])=>{control.disabled=disabled;});}
      });
    } catch(error) {$('entity-error').textContent=error.message || String(error);$('entity-error').hidden=false;}
  });
  $('entity-test').addEventListener('click',()=>work(async()=>{
    try {
      if(!entity || entity.kind!=='server')return;
      const candidate=copy(entity.row);for(const [key,entry] of entity.fields)candidate[key]=entry.read();
      validateEntity(candidate);
      $('entity-test-result').hidden=false;$('entity-test-result').classList.remove('error');$('entity-test-result').textContent='正在测试 RCON 连接…';$('entity-error').hidden=true;
      const result=await api('apiPost','operations',{action:'test_connection',server_root:formConfig.server_root,server:candidate});
      $('entity-test-result').textContent=`连接正常 · ${result.latency_ms} ms · 在线 ${result.players_online}/${result.players_max} 人`;
    } catch(error) {$('entity-test-result').hidden=false;$('entity-test-result').textContent=error.message || String(error);$('entity-test-result').classList.add('error');}
  }));
  for (const id of ['entity-cancel', 'entity-close']) $(id).addEventListener('click', () => $('config-dialog').close());
  $('config-dialog').addEventListener('close',()=>{entity=null;});
  $('config-dialog').addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  async function removeEntity(kind,index) {
    try {
      commitJson();
      const proposed=copy(formConfig);
      const rows=kind==='server'?proposed.servers:proposed.groups;
      const row=rows[index];
      const affected=kind==='server'?proposed.groups.filter(group=>group.server_ids.includes(row.id)):[];
      const empty=affected.filter(group=>group.server_ids.length===1);
      const prompt=kind==='server' ? `移除 ${row.name}（${row.id}）并保存？${affected.length ? '将同时从 '+affected.length+' 个配置组解绑。' : ''}${empty.length ? '仅绑定此服的 '+empty.length+' 个配置组会一并移除绑定。' : ''}服务端文件、历史白名单和日志保留。` : `移除群 ${row.group_id} 的绑定并保存？其他群仍可使用共享服务器，历史白名单和日志保留。`;
      if(!await confirmAction(prompt+' 当前未保存的设置也会一并提交。'))return;
      if(kind==='server')proposed.groups=proposed.groups.map(group=>({...group,server_ids:group.server_ids.filter(sid=>sid!==row.id)})).filter(group=>group.server_ids.length || !affected.some(item=>item.id===group.id));
      rows.splice(index,1);
      await work(()=>saveConfig(proposed));
    } catch(error) {notice(error.message || String(error),true);}
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
      commitJson(); formConfig.messages.tps = defaultTps;
      touch(); render(); notice('已恢复初始 TPS 回复模板。查询指令保持当前设置，点击“保存并生效”应用。');
    } catch (error) { notice(error.message, true); }
  });
  $('preview-help').addEventListener('click', () => {
    const context = {};
    for (const [key, value] of Object.entries(formConfig.commands)) context[key] = list(value)[0] || '';
    context.prefix = '/' + context.prefix;
    $('help-preview').textContent = formConfig.messages.help.replace(/\{\{|\}\}|\{([A-Za-z0-9_]+)\}/g, (whole, name) => whole === '{{' ? '{' : whole === '}}' ? '}' : context[name] ?? whole);
    $('help-preview').hidden = false;
  });
  function validateFields() {
    for (const input of document.querySelectorAll('#settings input, #settings textarea, #settings select')) {
      if (input.id === 'config-editor' || input.checkValidity()) continue;
      const section = input.closest('.settings-section'); if (section) changeSection(section.id.replace('settings-', ''));
      for(let detail=input.closest('details');detail;detail=detail.parentElement.closest('details'))detail.open=true;
      input.reportValidity(); throw new Error('请检查表单中标出的配置项。');
    }
    if (formConfig.servers.length && !formConfig.server_root.trim()) throw new Error('请填写服务器共同父目录。');
  }
  async function saveConfig(candidate=null) {
    if(candidate===null){commitJson();validateFields();}
    const submitted=copy(candidate || formConfig);
    const submittedVersion=editVersion;
    const result=await api('apiPost','config',{config:submitted,revision});
    // The POST is the commit boundary. A later refresh failure must never be
    // reported as a failed save or roll the displayed configuration back.
    try {
      if(submittedVersion!==editVersion){
        const latest=await api('apiGet','config');
        revision=latest.revision;saved=JSON.stringify(normalize(latest.config));
        notice('配置已保存；保存期间的新修改尚未提交，请再次保存。');syncState();
      } else {await load(true);notice(result.message);}
      output(result.message);await refresh();
    } catch(error) {
      if(submittedVersion===editVersion){const accepted=copy(submitted);for(const server of accepted.servers || [])if(server.rcon_password)server.rcon_password='__KEEP__';formConfig=normalize(accepted);saved=JSON.stringify(formConfig);jsonDirty=false;render();}
      notice('配置已保存，但页面刷新失败，请点击“重新载入”。'+(error.message || String(error)),true);
      output(result.message);
    }
  }
  async function saveQuickSetup(quick,scanRevision){
    if(hasChanges())throw new Error('请先保存或重新载入当前页面的修改，再使用快速配置。');
    const result=await api('apiPost','config',{quick_setup:quick,revision:scanRevision});
    try{await load(true);if(onQuickSaved)await onQuickSaved(result.setup);else await refresh();notice('快速配置已完成，在对应群聊发送 /mc 即可开始使用。');}
    catch(error){notice('配置已保存，但页面刷新失败，请点击“重新载入”。'+(error.message || String(error)),true);}
    output(result.message);return result;
  }
  $('save-config').addEventListener('click',()=>work(()=>saveConfig()));
  $('variable-save').addEventListener('click',()=>work(async()=>{
    commitJson();validateFields();
    const proposed=copy(formConfig);
    proposed.messages[$('variable-preview-kind').value]=$('variable-preview-template').value;
    const controls=['variable-preview-kind','variable-example-kind','variable-preview-template'].map(id=>[$(id),$(id).disabled]);
    controls.forEach(([control])=>{control.disabled=true;});
    try{await saveConfig(proposed);}finally{controls.forEach(([control,disabled])=>{control.disabled=disabled;});}
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
    if (!schema?.servers?.templates?.server?.items?.tps_command || !schema?.groups?.templates) throw new Error('管理页面需要新版配置接口，请重载插件后刷新页面。');
    platforms = response.platforms || [];
    bindings = response.bindings || { groups: [], servers: [] };
    variables = response.tps_variables || []; defaultTps = response.tps_default_template || '';
    messageTemplates = response.message_templates || Object.entries(schema.messages.items).map(([key,item]) => ({key,category:'common',category_label:'常用回复',default:item.default,variables:[]}));
    const selectedCategory = $('message-category').value;
    const categories = new Map([['', '全部分类']]);
    for (const row of messageTemplates) categories.set(row.category, row.category_label);
    $('message-category').replaceChildren();
    for (const [value,label] of categories) {const option=el('option',label);option.value=value;$('message-category').append(option);}
    $('message-category').value = categories.has(selectedCategory) ? selectedCategory : 'common';
    const exampleId=$('variable-example-kind').value;
    tpsExamples=response.tps_examples || [];
    $('variable-example-kind').replaceChildren();
    for(const example of tpsExamples){const option=el('option',example.label);option.value=example.id;$('variable-example-kind').append(option);}
    if(tpsExamples.some(example=>example.id===exampleId)) $('variable-example-kind').value=exampleId;

    revision = response.revision || '';
    configError = response.config_error || '';
    try {
      formConfig = normalize(response.config);
      saved = JSON.stringify(formConfig); jsonDirty = false;
    } catch (error) {
      formConfig = normalize({}); saved = JSON.stringify(formConfig); jsonDirty = true;
      configError = configError || error.message;
      $('config-editor').value = JSON.stringify(response.config, null, 2);
      activeSection = 'advanced';
    }
    loaded = true; editVersion++;
    $('data-directory').textContent = '插件数据库与日志：' + response.data_directory;
    render();
  }
  const quickSetup=createQuickSetup({api,work,save:saveQuickSetup,getContext:()=>({hasChanges:hasChanges()}),
    openManual(root){
      $('manual-settings').open=true;
      if(!formConfig.server_root && !formConfig.servers.length && root){formConfig.server_root=root;$('setting-server-root').value=root;touch();}
      $('setup-guide').scrollIntoView({block:'start'});syncState();
    }
  });
  $('manual-settings').addEventListener('toggle',()=>{if(loaded)syncState();});
  document.addEventListener('invalid',event=>{
    for(let detail=event.target.closest?.('details');detail;detail=detail.parentElement.closest('details'))detail.open=true;
  },true);
  return {
    load,setBusy:value=>{busy=value;if(loaded)syncState();},hasChanges,
    beginSetup(){changeSection('connections');quickSetup.focus();},
    editServer(id){const index=formConfig.servers.findIndex(server=>server.id===id);$('manual-settings').open=true;changeSection('connections');if(index>=0)openEntity('server',index);else notice('当前页面已移除此服务器，请重新载入。',true);}
  };
}
