const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const schema = JSON.parse(fs.readFileSync(path.join(root, '_conf_schema.json'), 'utf8'));
const defaults = JSON.parse(fs.readFileSync(path.join(root, 'config.default.json'), 'utf8'));
const tpsDocument=JSON.parse(fs.readFileSync(path.join(root,'tps.variables.json'),'utf8'));
const tpsVariables=tpsDocument.variables;
const tpsExamples=tpsDocument.examples;
const messageTemplates=JSON.parse(fs.readFileSync(path.join(root,'messages.variables.json'),'utf8')).templates;
const officialSdkRoot = process.env.MC_TEST_ASTRBOT_RELEASE;
const officialSdk = officialSdkRoot ? fs.readFileSync(path.join(officialSdkRoot, 'dashboard/plugin_page_bridge.js'), 'utf8') : '';
const playwrightModule = process.env.MC_TEST_PLAYWRIGHT_MODULE || path.join(root, '.research/browser/node_modules/playwright');
const { chromium } = require(playwrightModule);
const chromiumExecutable = process.env.MC_TEST_CHROMIUM;

(async () => {
  const server = http.createServer((req, res) => {
    const pathname = decodeURIComponent(req.url.split('?')[0]);
    if (pathname === '/bridge-sdk.js' && officialSdk) { res.writeHead(200, { 'Content-Type': 'text/javascript; charset=utf-8' }); res.end(officialSdk); return; }
    const target = path.resolve(root, 'pages/console', pathname === '/' ? 'index.html' : '.' + pathname);
    if (!target.startsWith(path.resolve(root, 'pages/console') + path.sep) || !fs.existsSync(target)) { res.writeHead(404); res.end(); return; }
    const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.png': 'image/png' };
    res.writeHead(200, { 'Content-Type': types[path.extname(target)] || 'application/octet-stream' });
    let content = fs.readFileSync(target);
    if (officialSdk && path.extname(target) === '.html') content = content.toString('utf8').replace('<script type="module" src="./app.js">', '<script src="/bridge-sdk.js"></script><script type="module" src="./app.js">');
    res.end(content);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true, ...(chromiumExecutable ? { executablePath: chromiumExecutable } : {}) });
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(({ schema, defaults, tpsVariables, tpsExamples, messageTemplates, useOfficialSdk }) => {
    const clone = data => JSON.parse(JSON.stringify(data));
    let config = { ...clone(defaults), server_root: 'D:/Minecraft', unknown_setting: 'preserved',
      servers: Array.from({ length: 5 }, (_, i) => ({ id: 'server' + (i + 1), name: '服务器 ' + (i + 1), directory: 'server' + (i + 1), rcon_host: '127.0.0.1', rcon_port: 25575 + i,
        rcon_password: i === 0 ? '__KEEP__' : '', rcon_password_env: i === 0 ? '' : 'MC_RCON_' + (i + 1), online_mode: false, tps_command: 'tps', players_command: 'list', tps_source: 'auto', spark_file: '.astrbot-spark-tps.txt', future_option: 'preserved' })),
      groups: [{ id: 'group1', platform_id: 'qq-bot', group_id: '111111111', server_ids: ['server1', 'server2', 'server3'], admin_ids: ['123456789'], max_entries_per_user: 1, broadcast_origin: '' },
               { id: 'group2', platform_id: 'qq-bot', group_id: '222222222', server_ids: ['server4', 'server5'], admin_ids: ['987654321'], max_entries_per_user: 1, broadcast_origin: '' }] };
    let revision = 'revision-1';
    let mockBackups=[];let mockNotification=true;let mockRequest=true;
    const entries = [{ id: 1, name: 'Steve', owner: '123456789', uuid: '5627dd98-e6be-3c21-b8a8-e92344183641' }, { id: 4, name: 'Alex', owner: '987654321', uuid: '36532b5e-c442-3dbb-a24c-c7e55d0f979a' }];
    const bindings = { groups: config.groups.map(g => ({ id: g.id, platform: g.platform_id, group_id: g.group_id })), servers: config.groups.flatMap(g => g.server_ids.map(s => ({ server: s, scope: g.id }))) };
    window.apiCalls = []; window.mockConflict = false; window.mockStorageFull = false; window.latestConfig = clone(config);
    window.prepareFreshConfiguration=()=>sessionStorage.setItem('mc-test-fresh','1');
    if(sessionStorage.getItem('mc-test-fresh')){sessionStorage.removeItem('mc-test-fresh');config=clone(defaults);bindings.groups=[];bindings.servers=[];window.noPlatforms=false;window.latestConfig=clone(config);}
    window.resetEmptyConfig = () => {
      config = clone(defaults); revision = 'revision-1'; window.latestConfig = clone(config);
      bindings.groups = []; bindings.servers = []; window.noPlatforms = true;
    };
    let configError = '';
    const recoveryState = JSON.parse(sessionStorage.getItem('mc-test-recovery') || 'null');
    if (recoveryState) {
      config = clone(recoveryState.config);
      bindings.groups = clone(recoveryState.bindings.groups); bindings.servers = clone(recoveryState.bindings.servers);
      configError = recoveryState.error; window.latestConfig = clone(config);
    }
    window.prepareRecovery = (keepHistory = false) => {
      const broken = clone(config);
      broken.groups[0].platform_id = ''; broken.groups[0].group_id = '';
      if (!keepHistory) broken.groups[0].id = '';
      sessionStorage.setItem('mc-test-recovery', JSON.stringify({ config: broken,
        bindings: keepHistory ? clone(bindings) : { groups: [], servers: [] },
        error: '第 1 条群绑定缺少：平台实例 ID（platform_id，填 /sid 的 Bot ID）、群号（group_id）。请编辑该群绑定，或删除未使用的空白项。' }));
    };
    window.prepareCompactServerIds = () => {
      const broken = clone(config);
      broken.groups[0].server_ids = ['server1,server2'];
      sessionStorage.setItem('mc-test-recovery', JSON.stringify({config:broken,bindings:clone(bindings),error:'服务器 server1,server2 不存在。'}));
    };
    const backend = {
      ready: async () => ({ plugin_name: 'astrbot_plugin_mc_manager' }),
      apiGet: async (endpoint, params) => {
        window.apiCalls.push({ endpoint, params });
        if (endpoint === 'snapshot' && configError) return { groups: [], servers: [], jobs: [], notifications_pending: 0, config_error: configError, configuration_required: true };
        if (endpoint === 'snapshot') return { groups: config.groups.map(g => ({ ...clone(g), group_ids: String(g.group_id).split(','), broadcast_group_ids: g.id === 'group1' ? String(g.group_id).split(',') : [], entries: g.id === 'group1' ? 2 : 0, broadcast_ready: g.id === 'group1' })),
          servers: config.servers.map((s, i) => ({ ...clone(s), directory: 'D:/Minecraft/' + s.directory, status: i === 2 ? '离线或 RCON 不可用' : '在线', pending: i === 2 ? 'RCON 不可用，等待自动同步' : '' })), jobs: [], notifications_pending: 0 };
        if (endpoint === 'operations') {
          if (params.kind === 'backups') return {backups:mockBackups,revision};
          if (params.kind === 'notifications') return {notifications:mockNotification ? [{id:1,origin:'qq-bot:GroupMessage:111111111',text:'测试重启结果',created:'2026-10-16T00:00:00Z',state:'failed',attempts:5,last_error:'适配器发送失败'}] : []};
          if (params.kind === 'requests') return {requests:mockRequest ? [{id:1,kind:'add',player:'NewPlayer',user:'123456789',user_name:'测试成员',created:'2026-10-16T00:00:00Z'}] : []};
          return {workers:{sync:{state:'waiting',last_success:'2026-10-16T00:00:00Z',last_error:''}},storage:{plugin_data:{path:'data/plugin_data/astrbot_plugin_mc_manager',free_bytes:1024*1024*1024}},database_bytes:{'mc.sqlite3':8192},queue:{pending:1,failed:1},servers:[{id:'server1',name:'服务器 1',rcon:'在线（RCON 查询成功）',status_latency_ms:3,directory:{path:'D:/Minecraft/server1',writable:true,free_bytes:1024*1024*1024},whitelist:{readable:true,entries:2},spark:{status:'完整'}}]};
        }
        if (endpoint === 'entries') return { entries: params.group === 'group1' ? clone(entries) : [] };
        if (endpoint === 'logs') return { logs: [{ time: '2026-10-13T14:30:00+00:00', action: 'add', group_id: '111111111', user: '123456789', user_name: '群成员', player: 'Steve', detail: '添加 #1；同步 server1,server2,server3' }], page: 1, total: 1 };
        if(endpoint==='config' && window.mockReadFailure){window.mockReadFailure=false;throw new Error('configuration refresh unavailable');}
        if (endpoint === 'config') return { config: clone(config), config_error: configError, schema, platforms: window.noPlatforms ? [] : [{ id: 'qq-bot', name: 'aiocqhttp', display_name: 'QQ / OneBot' }], bindings: clone(bindings), revision,
          data_directory: 'data/plugin_data/astrbot_plugin_mc_manager', tps_default_template: defaults.messages.tps, tps_variables: tpsVariables, tps_examples:tpsExamples, message_templates:messageTemplates };
      },
      apiPost: async (endpoint, body) => {
        window.apiCalls.push({ endpoint, body: clone(body) });
        if (endpoint === 'operations') {
          if(body.action==='discover_servers'){
            const token='scan-'+(window.mockScanNumber=(window.mockScanNumber || 0)+1);
            window.mockScan={token,revision,root:body.server_root};
            return {token,revision,server_root:body.server_root,single_server:false,servers:[
              {key:'alpha',name:'生存服',directory:'alpha',rcon_port:25575,rcon_host:'127.0.0.1',online_mode:false,ready:true,configured:false,whitelist_enabled:true,issues:[]},
              {key:'beta',name:'建筑服',directory:'beta',rcon_port:25576,rcon_host:'127.0.0.1',online_mode:false,ready:true,configured:false,whitelist_enabled:true,issues:[]},
              {key:'needs-rcon',name:'待设置服务器',directory:'needs-rcon',rcon_port:25577,rcon_host:'127.0.0.1',online_mode:false,ready:false,configured:false,whitelist_enabled:false,issues:['enable-rcon 未开启','rcon.password 未填写或格式无效']}
            ]};
          }
          if(body.action==='test_connection')return {server_id:body.server.id,latency_ms:3.2,players_online:'2',players_max:'20',result:'There are 2 of a max of 20 players online: Steve, Alex'};
          if (body.action === 'backup_create') {const backup={name:'mc-backup-20261016-000000-000000000001.zip',created:'2026-10-16T00:00:00Z',label:'manual',bytes:1234};mockBackups=[backup];return backup;}
          if (body.action === 'backup_download') return {name:body.name,content_base64:btoa('test backup'),sha256:'mock'};
          if (body.action === 'restore_preview') return {token:'restore-token',revision,groups:[{id:'group1',group_id:'111111111'}],servers:['server1'],counts:{entries:2,logs:3},note:'恢复前自动备份当前数据。'};
          if (body.action === 'restore_apply') return {message:'插件数据与配置已恢复。',revision};
          if (body.action === 'notification_retry') {mockNotification=false;return {notifications:[]};}
          if (body.action === 'approve' || body.action === 'reject') {mockRequest=false;return {message:'申请已处理。'};}
          if (body.action === 'cleanup_preview') return {token:'cleanup-token',count:2,days:30,maximum:1000};
          if (body.action === 'cleanup_apply') return {count:2};
          if (body.action === 'bulk_preview') return {token:'bulk-token',additions:[{name:'NewPlayer',owner:'',uuid:'test'}],skipped:[],errors:[]};
          if (body.action === 'bulk_apply' || body.action === 'bulk_remove' || body.action === 'bulk_transfer') return {count:1,sync:'已同步'};
          if (body.action === 'entries_export') return {name:'whitelist.json',mime:'application/json',content:JSON.stringify(entries),truncated:false};
          if (body.action === 'logs_export') return {name:'logs.csv',mime:'text/csv',content:'player,action\nSteve,add',truncated:false};
        }
        if (endpoint === 'config') {
          if (window.mockConflict) return { status: 'error', message: '配置已被其他页面修改，请先重新载入配置后再保存。' };
          if (window.mockStorageFull) return {status:'error',message:'保存配置失败：写入位置可用空间不足，或存储配额已用尽。请释放空间后重试。本次修改未生效。'};
          if (body.revision !== revision) throw new Error('配置版本冲突');
          let setupSummary;
          if(body.quick_setup){
            const q=body.quick_setup;
            if(q.token!==window.mockScan?.token || window.mockScan.revision!==revision)return {status:'error',message:'扫描结果已过期或配置已经变化，请重新扫描。'};
            const next=clone(config);next.server_root=window.mockScan.root;
            next.servers=q.selected.map((directory,index)=>({id:'server'+(index+1),name:directory==='alpha'?'生存服':'建筑服',directory,rcon_host:q.rcon_host,rcon_port:25575+index,rcon_password:'__KEEP__',online_mode:false,tps_command:'spark tps',tps_auto_detect:true,tps_output:'template'}));
            next.groups=[{id:'auto-group',platform_id:q.platform_id,group_id:q.group_id,server_ids:next.servers.map(server=>server.id)}];
            setupSummary={group_id:'auto-group',group_ids:[q.group_id],servers:next.groups[0].server_ids,added:next.servers.length};
            config=next;
          }else config=clone(body.config);
          configError = ''; sessionStorage.removeItem('mc-test-recovery');
          for (const server of config.servers) if (server.rcon_password) server.rcon_password = '__KEEP__';
          for (const archived of bindings.groups) archived.active = 0;
          for (const group of config.groups) {
            const saved = bindings.groups.find(g => g.id === group.id);
            if (saved) Object.assign(saved, { platform: group.platform_id, group_id: group.group_id, active: 1 });
            else bindings.groups.push({ id: group.id, platform: group.platform_id, group_id: group.group_id, active: 1 });
          }
          bindings.servers = config.groups.flatMap(group => group.server_ids.map(server => ({ server, scope: group.id })));
          revision = 'revision-' + (Number(revision.split('-')[1]) + 1);
          window.latestConfig = clone(config);
          return { message: '配置已保存并立即生效。',...(setupSummary?{setup:setupSummary}:{}) };
        }
        return { message: body.action === 'restart' ? '正在重启，等待广播。' : '测试操作完成。' };
      }
    };
    if (useOfficialSdk) {
      window.addEventListener('message', async event => {
        const message = event.data;
        if (event.source !== window || event.origin !== location.origin || message?.channel !== 'astrbot-plugin-page') return;
        if (message.kind === 'ready') {
          window.postMessage({ channel: 'astrbot-plugin-page', kind: 'context', context: { plugin_name: 'astrbot_plugin_mc_manager', page_name: 'console', view_name: 'console', locale: 'zh-CN', theme: 'dark', i18n: {} } }, location.origin);
        } else if (message.kind === 'request') {
          try {
            if (!['api:get', 'api:post'].includes(message.action)) throw new Error('Unexpected bridge action: ' + message.action);
            const data = message.action === 'api:get' ? await backend.apiGet(message.endpoint, message.params) : await backend.apiPost(message.endpoint, message.body);
            window.postMessage({ channel: 'astrbot-plugin-page', kind: 'response', requestId: message.requestId, ok: true, data }, location.origin);
          } catch (error) {
            window.postMessage({ channel: 'astrbot-plugin-page', kind: 'response', requestId: message.requestId, ok: false, error: error.message }, location.origin);
          }
        }
      });
    } else window.AstrBotPluginPage = backend;
  }, { schema, defaults, tpsVariables, tpsExamples, messageTemplates, useOfficialSdk: Boolean(officialSdk) });
  const clickTab = async name => {await page.locator(`[data-settings-tab="${name}"]`).click();if(name==='connections' && !await page.locator('#manual-settings').evaluate(detail=>detail.open))await page.locator('#manual-settings>summary').click();};
  async function save() {
    if(await page.locator('#save-config').isHidden())await page.locator('#manual-settings>summary').click();
    const count = await page.evaluate(() => window.apiCalls.filter(c => c.endpoint === 'config' && c.body).length);
    await page.locator('#save-config').click();
    await page.waitForFunction(previous => window.apiCalls.filter(c => c.endpoint === 'config' && c.body).length > previous, count);
    await page.waitForFunction(() => !document.getElementById('save-config').disabled);
  }
  async function submitEntity() {
    await page.locator('#entity-save').click();
    await page.waitForFunction(()=>!document.getElementById('config-dialog').open && !document.getElementById('save-config').disabled);
  }
  async function saveTemplate() {
    const count=await page.evaluate(()=>window.apiCalls.filter(call=>call.endpoint==='config' && call.body).length);
    await page.locator('#variable-save').click();
    await page.waitForFunction(previous=>window.apiCalls.filter(call=>call.endpoint==='config' && call.body).length>previous,count);
    await page.waitForFunction(()=>!document.getElementById('save-config').disabled);
  }
  async function screenshot(name, fullPage = true) { await page.screenshot({ path: path.join(root, 'docs/screenshots', name), fullPage, animations: 'disabled' }); }
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForFunction(() => document.getElementById('server-count').textContent === '5');
    fs.mkdirSync(path.join(root, 'docs/screenshots'), { recursive: true });
    assert.equal(await page.locator('.card').count(), 3);
    await page.waitForFunction(() => document.getElementById('brand-logo').complete && document.getElementById('brand-logo').naturalWidth > 0);
    assert.equal(await page.locator('#brand-logo').getAttribute('src'), './logo.png');
    assert.equal(await page.locator('#page-title').textContent(), '服务器概览');
    assert.equal(await page.locator('#overview-stats').isVisible(), true);
    assert.equal(await page.locator('#feedback').isHidden(), true);
    assert.equal(await page.locator('#entity-apply,#variable-apply').count(),0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'desktop overview overflow');
    assert.equal(await page.evaluate(() => getComputedStyle(document.getElementById('save-config')).backgroundColor), 'rgb(37, 99, 235)');

    await screenshot('desktop.png');
    await page.locator('#group').selectOption('group2');
    await page.waitForFunction(() => document.querySelectorAll('.card').length === 2);
    await page.locator('#group').selectOption('group1');
    await page.waitForFunction(() => document.querySelectorAll('.card').length === 3);
    await page.locator('[data-tab="whitelist"]').click();
    assert.equal(await page.locator('#page-title').textContent(), '白名单管理');
    assert.equal(await page.locator('#overview-stats').isHidden(), true);
    await page.waitForFunction(() => document.querySelectorAll('#entries tr').length === 2);
    await page.locator('#player').fill('Player123'); await page.locator('#add-form button').click();
    await page.waitForFunction(() => window.apiCalls.some(c => c.body?.action === 'add'));
    await page.locator('#entries button').filter({ hasText: '修改' }).first().click();
    await page.locator('#edit-player').fill('Player456'); await page.locator('#edit-form button[type="submit"]').click();
    await page.waitForFunction(() => window.apiCalls.some(c => c.body?.action === 'modify'));
    assert.equal(await page.locator('#feedback').isVisible(), true);
    await page.locator('#clear-output').click();
    assert.equal(await page.locator('#feedback').isHidden(), true);
    await page.locator('#entries button').filter({ hasText: '移除' }).first().click();
    await page.locator('#confirm-dialog button[value="cancel"]').click();
    assert.equal(await page.evaluate(() => window.apiCalls.filter(c => c.body?.action === 'remove').length), 0);
    await page.locator('#entries button').filter({ hasText: '移除' }).first().click();
    await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(() => window.apiCalls.some(c => c.body?.action === 'remove'));
    await page.locator('[data-tab="audit"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#logs tr').length === 1);
    await page.locator('#log-user').fill('123456789'); await page.locator('#log-filter button').click();
    await page.waitForFunction(() => window.apiCalls.some(c => c.endpoint === 'logs' && c.params.user === '123456789'));

    await page.locator('[data-tab="settings"]').click();
    assert.equal(await page.locator('#scope-bar').isHidden(), true);
    assert.equal(await page.locator('#page-title').textContent(), '配置与菜单');
    await page.waitForFunction(() => document.querySelectorAll('#configured-servers article').length === 5);
    assert.equal(await page.locator('#configured-groups article').count(), 2);
    assert.equal(await page.locator('#config-state').textContent(), '配置已保存');
    await screenshot('web-settings.png');
    await clickTab('bridge');
    assert.equal(await page.locator('#bridge-bridge_enabled').isChecked(), false);
    assert.equal(await page.locator('#bridge-group-0-bridge_enabled').isChecked(), false);
    await page.locator('#bridge-group-0-bridge_enabled').check();
    assert.equal(await page.locator('#bridge-bridge_enabled').isChecked(),true);
    await page.locator('#bridge-group-0-bridge_to_game').uncheck();
    await page.locator('#bridge-group-0-bridge_death').uncheck();
    await page.locator('#bridge-group-0-bridge_prefix').fill('#mc');
    await page.locator('#message-bridge_to_game').fill('§b[{group}] §f{nickname}: §r{message}');
    await save();
    const bridgeSaved = await page.evaluate(() => window.latestConfig);
    assert.equal(bridgeSaved.bridge_enabled, true);
    assert.equal(bridgeSaved.groups[0].bridge_enabled, true);
    assert.equal(bridgeSaved.groups[0].bridge_to_game, false);
    assert.equal(bridgeSaved.groups[0].bridge_death, false);
    assert.equal(bridgeSaved.groups[0].bridge_prefix, '#mc');
    assert.match(bridgeSaved.messages.bridge_to_game, /§b/);
    assert.equal(bridgeSaved.groups[1].bridge_enabled, false);
    await screenshot('bridge-settings.png');
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await screenshot('bridge-settings-mobile.png', false);
    await page.setViewportSize({width:1280,height:1000});
    await clickTab('connections');
    await page.locator('#configured-servers [data-id="server1"] button').filter({ hasText: '编辑服务器' }).click();
    assert.equal(await page.locator('#entity-id').getAttribute('readonly'), '');
    assert.equal(await page.locator('#entity-rcon_password').inputValue(), '');
    assert.equal(await page.locator('#entity-advanced').getAttribute('open'),null);
    const beforeProbe=await page.evaluate(()=>window.apiCalls.filter(call=>call.endpoint==='config' && call.body).length);
    await page.locator('#entity-test').click();
    await page.waitForFunction(()=>document.getElementById('entity-test-result').textContent.includes('连接正常'));
    assert.equal(await page.evaluate(()=>window.apiCalls.filter(call=>call.endpoint==='config' && call.body).length),beforeProbe);

    assert.equal(await page.locator('#entity-whitelist_overwrite').isChecked(), false);
    await page.locator('#entity-advanced summary').click();
    await page.locator('#entity-whitelist_overwrite').check();
    await page.locator('#entity-name').fill('生存服');
    const unchangedServer=await page.evaluate(()=>window.latestConfig.servers[0].name);
    await page.evaluate(()=>{window.mockStorageFull=true;});
    await page.locator('#entity-save').click();
    await page.waitForFunction(()=>document.getElementById('entity-error').textContent.includes('空间不足'));
    assert.equal(await page.locator('#config-dialog').evaluate(dialog=>dialog.open),true);
    assert.equal(await page.locator('#entity-name').inputValue(),'生存服');
    assert.equal(await page.evaluate(()=>window.latestConfig.servers[0].name),unchangedServer);
    await page.evaluate(()=>{window.mockStorageFull=false;});
    await submitEntity();
    assert.equal(await page.locator('#config-state').textContent(),'配置已保存');
    const countBeforeCancel=await page.evaluate(()=>window.apiCalls.filter(call=>call.endpoint==='config' && call.body).length);
    await page.locator('#add-server').click();
    await page.locator('#entity-name').fill('取消后不应保留');
    await page.locator('#entity-cancel').click();
    assert.equal(await page.locator('#configured-servers article').count(),5);
    assert.equal(await page.evaluate(()=>window.apiCalls.filter(call=>call.endpoint==='config' && call.body).length),countBeforeCancel);
    await page.locator('#add-server').click();
    assert.equal(await page.locator('#entity-name').inputValue(),'服务器 6');
    assert.equal(await page.locator('#entity-id').inputValue(), 'server6');
    await page.locator('#entity-name').fill('群三专用服');
    await page.locator('#entity-rcon_password').fill('new-private-password');
    await page.evaluate(()=>{window.mockReadFailure=true;});
    await submitEntity();
    assert.match(await page.locator('#notice').textContent(),/配置已保存，但页面刷新失败/);
    assert.equal((await page.locator('#config-editor').inputValue()).includes('new-private-password'),false);
    await page.locator('#reload-config').click();
    await page.waitForFunction(()=>document.getElementById('notice').textContent==='已重新载入保存的配置。' && !document.getElementById('save-config').disabled);

    assert.equal(await page.locator('#configured-servers article').count(), 6);
    await page.locator('#add-group').click();
    assert.equal(await page.locator('#entity-platform_id').inputValue(), 'qq-bot');
    await page.locator('#entity-group_id').fill('333333333\n333333334');
    await page.locator('#entity-admin_ids').fill('12345\n67890');
    await page.locator('#entity-max_entries_per_user').fill('2');
    assert.equal(await page.locator('#entity-fields input[value="server1"]').isDisabled(), false);
    assert.equal(await page.locator('#entity-fields input[value="server4"]').isDisabled(), false);
    await page.locator('#entity-fields input[value="server1"]').check();
    await page.locator('#entity-fields input[value="server6"]').check();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'mobile dialog overflow');
    await screenshot('add-group-mobile.png', false);
    await page.setViewportSize({ width: 1280, height: 1000 });
    await page.locator('#config-dialog').evaluate(dialog => { dialog.scrollTop = 0; });
    await page.locator('#config-dialog').screenshot({ path: path.join(root, 'docs/screenshots/add-group.png') });
    await submitEntity();
    assert.equal(await page.locator('#configured-groups article').count(), 3);
    // Dialog saves are already committed before switching views or refreshing.
    await page.locator('[data-tab="overview"]').click(); await page.locator('[data-tab="settings"]').click();
    await page.locator('#refresh').click();
    assert.equal(await page.locator('#configured-groups article').count(), 3);
    await clickTab('general');assert.equal(await page.locator('#general-advanced').getAttribute('open'),null); await page.locator('#general-page_size').fill('15');
    await page.locator('#general-game_broadcast').uncheck();
    await clickTab('commands'); await page.locator('#command-add').fill('add,申请');
    await page.locator('#message-help').fill('欢迎！{prefix} {add} <玩家名>');
    await page.locator('#preview-help').click();
    assert.equal(await page.locator('#help-preview').textContent(), '欢迎！/mc add <玩家名>');
    await clickTab('messages');assert.equal(await page.locator('#message-fields textarea').first().getAttribute('name'),'help');assert.equal(await page.locator('#message-fields textarea').count(),8);await page.locator('#message-more').click();assert.equal(await page.locator('#message-fields textarea').count(),16); await page.locator('#message-category').selectOption('restart'); await page.locator('#message-restart_success').fill('服务器 {server} 已恢复，用时 {seconds} 秒。');
    await page.locator('#message-category').selectOption('errors');
    await page.locator('#message-search').fill('error_entry_owner');
    assert.equal(await page.locator('#message-fields textarea').count(), 1);
    await page.locator('#message-error_entry_owner').fill('请联系管理员处理其他人的条目。');
    await page.locator('#message-category').selectOption('');
    await page.locator('#message-search').fill('messages.this_key_does_not_exist');
    assert.equal(await page.locator('#message-fields textarea').count(), 0);
    await page.locator('#message-search').fill('sync_success');
    await page.locator('#message-sync_success').fill('已更新 ');
    await page.locator('#message-fields .variable-token').filter({hasText:'{server}'}).click();
    assert.match(await page.locator('#message-sync_success').inputValue(), /\{server\}/);
    await page.locator('#message-sync_success').locator('..').getByRole('button', {name:'恢复此项默认'}).click();
    await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(value=>document.getElementById('message-sync_success').value===value,defaults.messages.sync_success);
    await page.locator('#message-sync_success').fill('已更新 {server}');
    await page.locator('#message-search').fill('help');
    await page.locator('#all-message-help').fill('你好 {prefix} {add}');
    assert.equal(await page.locator('#message-help').inputValue(), '你好 {prefix} {add}');
    await page.locator('#message-search').fill('');
    await page.locator('#message-category').selectOption('common');
    await save();
    let latest = await page.evaluate(() => window.latestConfig);
    assert.equal(latest.messages.error_entry_owner, '请联系管理员处理其他人的条目。');
    assert.equal(latest.messages.help, '你好 {prefix} {add}');
    assert.match(latest.messages.sync_success, /\{server\}/);

    assert.equal(latest.servers[0].rcon_password, '__KEEP__');
    assert.equal(latest.servers[0].future_option, 'preserved');
    assert.equal(latest.unknown_setting, 'preserved');
    assert.equal(latest.servers[0].name, '生存服');
    assert.equal(latest.servers[0].whitelist_overwrite, true);
    assert.equal(latest.servers[5].whitelist_overwrite, false);
    assert.deepEqual(latest.groups[2].server_ids, ['server1', 'server6']);
    assert.equal(latest.groups[2].group_id, '333333333,333333334');
    assert.equal(await page.locator('#group-count').textContent(), '4');
    assert.deepEqual(latest.groups[2].admin_ids, ['12345', '67890']);
    assert.equal(latest.page_size, 15); assert.equal(latest.game_broadcast, false);
    assert.equal(latest.commands.add, 'add,申请');
    assert.equal(await page.locator('#config-state').textContent(), '配置已保存');
    await clickTab('connections');
    await page.locator('#configured-groups [data-id="group1"] button').filter({ hasText: '编辑群聊' }).click();
    assert.equal(await page.locator('#entity-id').getAttribute('readonly'), '');
    assert.equal(await page.locator('#entity-group_id').isEditable(), true);
    assert.equal(await page.locator('#entity-platform_id').isEnabled(), true);
    assert.equal(await page.locator('#entity-fields input[value="server1"]').isDisabled(), false);
    assert.equal(await page.locator('#entity-fields input[value="server4"]').isDisabled(), false);
    await page.locator('#entity-group_id').fill('111111112,222222222');
    await page.locator('#entity-save').click();
    assert.match(await page.locator('#entity-error').textContent(), /已经绑定/);
    await page.locator('#entity-group_id').fill('111111112,111111113');
    await submitEntity();
    await save();
    assert.equal(await page.evaluate(() => window.latestConfig.groups.find(g => g.id === 'group1').group_id), '111111112,111111113');

    await clickTab('spark');
    assert.equal(await page.locator('#tps-server1-output').inputValue(),'template');
    await page.locator('#tps-server1-output').selectOption('template');
    assert.equal(await page.locator('#tps-template-editor').isEnabled(),true);
    assert.equal(await page.locator('#tps-server1-tps_source').count(),0);
    assert.equal(await page.locator('#tps-server1-spark_file').count(),0);
    assert.equal(await page.locator('#spark-spark_file_max_age').count(),0);
    assert.equal(await page.locator('#tps-variables button').filter({hasText:'{tps_file_age_seconds}'}).count(),0);
    await page.locator('.spark-log-options summary').first().click();
    assert.equal(await page.locator('#tps-server1-spark_log_fallback').isChecked(),true);
    assert.equal(await page.locator('#tps-server1-spark_log_path').inputValue(),'logs/latest.log');
    await page.locator('#tps-server1-spark_log_fallback').uncheck();
    await page.locator('#tps-server1-spark_log_wait_seconds').fill('4');
    await page.locator('#tps-server1-spark_log_encoding').selectOption('gb18030');

    await save();
    assert.equal(await page.evaluate(() => Object.hasOwn(window.latestConfig.servers[0],'tps_source')),false);
    assert.equal(await page.evaluate(() => Object.hasOwn(window.latestConfig.servers[0],'spark_file')),false);
    assert.equal(await page.evaluate(() => window.latestConfig.servers[0].spark_log_fallback),false);
    assert.equal(await page.evaluate(() => window.latestConfig.servers[0].spark_log_wait_seconds),4);
    assert.equal(await page.evaluate(() => window.latestConfig.servers[0].spark_log_encoding),'gb18030');
    await page.locator('.spark-log-options summary').first().click();
    await page.locator('#tps-server1-spark_log_fallback').check();
    await page.locator('#tps-server1-spark_log_encoding').selectOption('utf-8');

    assert.equal(await page.locator('#tps-variables tr').count(), tpsVariables.length);
    const presetCommand=await page.locator('#tps-server1-command').inputValue();
    await page.locator('#tps-server-fields .tps-server-card').first().locator('.command-presets button').filter({hasText:'forge tps'}).click();
    assert.equal(await page.locator('#tps-server1-command').inputValue(),'forge tps');
    await page.locator('#tps-server1-command').fill(presetCommand);
    await page.locator('#tps-template-editor').fill('{server}: {tps_5s} TPS / {cpu_process_1m}%');
    const commandsBeforeTemplateReset=await page.locator('#tps-server-fields input[name="tps_command"]').evaluateAll(nodes=>nodes.map(node=>node.value));
    await page.locator('#spark-defaults').click();
    await clickTab('variables');
    await page.locator('#variable-group').selectOption('CPU');
    assert.equal(await page.locator('#tps-variables tr').count(),6);
    await page.locator('#variable-search').fill('cpu_system_1m');
    assert.equal(await page.locator('#tps-variables tr').count(),1);
    await page.locator('#variable-search').fill('');await page.locator('#variable-group').selectOption('');
    await page.locator('#variable-scope').selectOption('status');
    assert.equal(await page.locator('#tps-variables button').filter({hasText:'{players_online}'}).count(),1);
    assert.equal(await page.locator('#tps-variables button').filter({hasText:'{tps_5s}'}).count(),0);
    await page.locator('#variable-preview-kind').selectOption('status');
    await page.locator('#variable-preview-template').fill('在线：');
    await page.locator('#tps-variables button').filter({hasText:'{players_online}'}).click();
    assert.equal(await page.locator('#variable-preview-template').inputValue(),'在线：{players_online}');
    assert.equal(await page.locator('#variable-preview-result').textContent(),'在线：2');
    await page.locator('#variable-preview-template').fill('{query_command}|{query_source}');
    assert.equal(await page.locator('#variable-preview-result').textContent(),'list|RCON');
    await page.locator('#variable-preview-template').fill('{status');
    assert.match(await page.locator('#variable-preview-warning').textContent(),/花括号不完整/);
    await page.locator('#variable-preview-template').fill('<img src=x onerror="alert(1)">{status}');
    assert.equal(await page.locator('#variable-preview-result img').count(),0);
    await page.locator('#variable-example').click();
    assert.match(await page.locator('#variable-preview-result').textContent(),/2\/20/);
    await saveTemplate();
    await page.locator('#variable-scope').selectOption('');
    await page.locator('#variable-preview-kind').selectOption('tps');
    assert.equal(await page.locator('#variable-example-kind option').count(),6);
    await page.locator('#variable-format').selectOption('forge');
    assert.equal(await page.locator('#tps-variables button').filter({hasText:'{forge_nether_tps}'}).count(),1);
    assert.equal(await page.locator('#tps-variables button').filter({hasText:'{cpu_system_1m}'}).count(),0);
    await page.locator('#variable-format').selectOption('');
    await page.locator('#variable-example-kind').selectOption('forge');
    assert.match(await page.locator('#variable-preview-result').textContent(),/总体 TPS：19.98/);
    await page.locator('#variable-preview-template').fill('{tps_overall}|{tps_1m}');
    assert.equal(await page.locator('#variable-preview-result').textContent(),'19.98|暂无数据');
    await page.locator('#variable-example-kind').selectOption('tick');
    assert.match(await page.locator('#variable-preview-result').textContent(),/22.4/);
    await page.locator('#variable-preview-template').fill('{perf_target_rate}|{perf_tps}');
    assert.equal(await page.locator('#variable-preview-result').textContent(),'20|暂无数据');
    await page.locator('#variable-example-kind').selectOption('universal');
    await page.locator('#variable-example').click();
    assert.match(await page.locator('#variable-preview-result').textContent(),/📈 服务器性能/);
    assert.match(await page.locator('#variable-preview-result').textContent(),/⚡ TPS/);
    await saveTemplate();
    await page.locator('#tps-variable-details').evaluate(details=>{details.open=false;});
    await page.evaluate(()=>{window.scrollTo(0,0);document.activeElement?.blur();});
    await screenshot('variable-docs.png');
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
    await page.locator('#settings-variables').evaluate(section=>window.scrollTo(0,section.getBoundingClientRect().top+window.scrollY-16));
    await screenshot('variable-docs-mobile.png',false);
    await page.setViewportSize({width:1280,height:1000});
    await clickTab('spark');
    assert.equal(await page.locator('#tps-template-editor').inputValue(), defaults.messages.tps);
    await page.locator('.spark-log-options').first().evaluate(details=>{details.open=true;});
    await screenshot('spark-settings.png');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'mobile settings overflow');
    await screenshot('web-settings-mobile.png');
    await page.setViewportSize({ width: 1280, height: 1000 });
    await save();
    latest = await page.evaluate(() => window.latestConfig);
    assert.deepEqual(latest.servers.map(server=>server.tps_command),commandsBeforeTemplateReset);
    assert.equal(latest.commands.add, 'add,申请');

    // A command now defines the query; the retired selector is absent everywhere.
    assert.equal(await page.locator('[name="tps_mode"]').count(),0);
    assert.equal(await page.locator('#tps-server1-mode').count(),0);
    assert.equal(await page.locator('#tps-server1-tps_source').count(),0);
    assert.equal(await page.locator('#tps-server1-spark_file').count(),0);
    await page.locator('#tps-server1-auto-detect').check();
    await page.locator('#tps-server1-output').selectOption('raw');
    await page.locator('#tps-server1-command').fill('mycore:health detail');
    await page.locator('#spark-defaults').click();
    assert.equal(await page.locator('#tps-server1-command').inputValue(), 'mycore:health detail');
    await save();
    latest = await page.evaluate(() => window.latestConfig);
    assert.equal(latest.servers.every(server=>!('tps_mode' in server)),true);
    assert.equal(latest.servers[0].tps_output,'raw');
    assert.equal(latest.servers[0].tps_auto_detect, true);
    assert.equal(latest.servers[0].tps_command, 'mycore:health detail');

    await clickTab('connections');
    await page.locator('#configured-servers [data-id="server1"] button').filter({ hasText: '编辑服务器' }).click();
    assert.equal(await page.locator('#entity-tps_mode').count(),0);
    assert.equal(await page.locator('#entity-spark_file').count(),0);
    assert.equal(await page.locator('#entity-tps_auto_detect').isEnabled(),true);
    assert.equal(await page.locator('#entity-tps_command').inputValue(), 'mycore:health detail');
    await submitEntity();
    await save();
    const mixedTps = await page.evaluate(() => window.latestConfig);
    const legacyTps = JSON.parse(JSON.stringify(mixedTps));
    for (const server of legacyTps.servers) { server.tps_mode = 'native'; if (server.id !== 'server1') delete server.tps_command; }
    legacyTps.servers[2].tps_mode='spark';
    legacyTps.servers[1].tps_output='template';
    await clickTab('advanced');
    await page.locator('#config-editor').fill(JSON.stringify(legacyTps));
    await page.locator('#apply-json').click();
    await clickTab('spark');
    assert.equal(await page.locator('#tps-server1-command').inputValue(), 'mycore:health detail');
    assert.equal(await page.locator('#tps-server2-command').inputValue(), 'tps');
    assert.equal(await page.locator('#tps-server3-command').inputValue(), 'spark tps');
    assert.equal(await page.locator('#tps-template-editor').isEnabled(), true);
    assert.equal(await page.locator('#spark-defaults').isEnabled(), true);
    assert.equal(await page.locator('[name="tps_mode"]').count(),0);
    await save();
    assert.equal(await page.evaluate(()=>window.latestConfig.servers.every(server=>!('tps_mode' in server))),true);
    await clickTab('variables');
    assert.equal(await page.locator('#variable-preview-kind').isVisible(), true);
    await clickTab('spark');
    await page.locator('#tps-server2-command').fill('tick query');
    await page.locator('#spark-defaults').click();
    assert.equal(await page.locator('#tps-server2-command').inputValue(),'tick query');
    await clickTab('advanced');
    await page.locator('#config-editor').fill(JSON.stringify(mixedTps));
    await page.locator('#apply-json').click();
    await save();

    await clickTab('advanced');
    await page.locator('#config-editor').fill('{broken'); await page.locator('#save-config').click();
    await page.waitForFunction(() => document.getElementById('notice').textContent.includes('有效的 JSON'));
    assert.equal(await page.locator('#config-state').textContent(), '有未保存的修改');
    const advanced = await page.evaluate(() => window.latestConfig); advanced.commands.prefix = 'minecraft';
    await page.locator('#config-editor').fill(JSON.stringify(advanced)); await page.locator('#apply-json').click();
    await clickTab('commands'); assert.equal(await page.locator('#command-prefix').inputValue(), 'minecraft');
    await save();
    // Backend conflicts retain the current input and never pretend to save it.
    await page.locator('#command-prefix').fill('newprefix');
    await page.evaluate(() => { window.mockConflict = true; });
    await save();
    assert.match(await page.locator('#notice').textContent(), /其他页面/);
    assert.equal(await page.locator('#command-prefix').inputValue(), 'newprefix');
    assert.equal(await page.evaluate(() => window.latestConfig.commands.prefix), 'minecraft');
    await page.evaluate(() => { window.mockConflict = false; });
    await page.evaluate(() => { window.mockStorageFull = true; });
    await save();
    assert.match(await page.locator('#notice').textContent(), /可用空间不足/);
    assert.equal(await page.locator('#command-prefix').inputValue(), 'newprefix');
    assert.equal(await page.evaluate(() => window.latestConfig.commands.prefix), 'minecraft');
    assert.equal(await page.locator('#config-state').textContent(), '有未保存的修改');
    await page.evaluate(() => { window.mockStorageFull = false; });
    await page.locator('#reload-config').click(); await page.locator('#confirm-dialog button[value="cancel"]').click();
    assert.equal(await page.locator('#command-prefix').inputValue(), 'newprefix');
    await page.locator('#reload-config').click(); await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(() => document.getElementById('command-prefix').value === 'minecraft');

    await clickTab('connections');
    await page.locator('#configured-servers [data-id="server6"] button').filter({ hasText: '移除服务器' }).click();
    await page.locator('#confirm-dialog').waitFor({state:'visible'});
    assert.match(await page.locator('#confirm-text').textContent(),/同时从/);
    await page.locator('#confirm-dialog button[value="cancel"]').click();
    await page.locator('#configured-groups [data-id="group3"] button').filter({ hasText: '移除群绑定' }).click();
    await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#configured-groups article').length === 2);
    await page.locator('#add-group').click();
    assert.equal(await page.locator('#entity-id').inputValue(), 'group4');
    await page.locator('#entity-group_id').fill('444444444');
    assert.equal(await page.locator('#entity-fields input[value="server6"]').isDisabled(), false);
    await page.locator('#entity-fields input[value="server6"]').check();
    await submitEntity(); await save();
    assert.deepEqual(await page.evaluate(() => window.latestConfig.groups.find(g => g.id === 'group4').server_ids), ['server6']);
    await page.locator('#configured-groups [data-id="group4"] button').filter({ hasText: '移除群绑定' }).click();
    await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#configured-groups article').length === 2);
    await page.locator('#configured-servers [data-id="server6"] button').filter({ hasText: '移除服务器' }).click();
    await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#configured-servers article').length === 5);
    await save();
    await page.locator('#add-group').click();
    assert.equal(await page.locator('#entity-fields input[value="server1"]').isDisabled(), false);
    await page.locator('#entity-cancel').click();

    await page.locator('[data-tab="overview"]').click();
    await page.locator('#group').selectOption('group1');
    await page.locator('.card button').filter({ hasText: '重启' }).first().click();
    await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(() => window.apiCalls.some(c => c.body?.action === 'restart'));
    const restart = await page.evaluate(() => window.apiCalls.find(c => c.body?.action === 'restart'));
    assert.equal(restart.body.group, 'group1'); assert.deepEqual(restart.body.args, ['server1']);
    await page.setViewportSize({ width: 390, height: 844 }); await screenshot('mobile.png');
    await page.locator('[data-tab="settings"]').click();
    await clickTab('connections');
    await page.locator('#configured-servers [data-id="server1"] button').filter({ hasText: '编辑服务器' }).click();
    await page.locator('#entity-clear-password').check();
    await page.locator('#entity-advanced summary').click();
    await page.locator('#entity-rcon_password_env').fill('MC_RCON_CLEAR_TEST');
    await submitEntity(); await save();
    assert.equal(await page.evaluate(() => window.latestConfig.servers[0].rcon_password), '');
    assert.equal(await page.evaluate(() => window.latestConfig.servers[0].rcon_password_env), 'MC_RCON_CLEAR_TEST');

    // New operations share real page controls and the official bridge contract.
    await page.locator('[data-tab="whitelist"]').click();
    await page.locator('#entry-search').fill('Steve');await page.locator('#entry-filter button[type="submit"]').click();
    await page.waitForFunction(()=>window.apiCalls.some(call=>call.endpoint==='entries' && call.params.search==='Steve'));
    const whitelistDownload=page.waitForEvent('download');await page.locator('#entry-export').click();
    assert.equal((await whitelistDownload).suggestedFilename(),'whitelist.json');
    await page.locator('#bulk-open').click();await page.locator('#bulk-data').fill('NewPlayer');await page.locator('#bulk-preview-button').click();
    await page.locator('#bulk-apply').waitFor({state:'visible'});await page.waitForFunction(()=>!document.getElementById('bulk-apply').disabled);
    await page.locator('#bulk-apply').click();await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(()=>window.apiCalls.some(call=>call.body?.action==='bulk_apply' && call.body.token==='bulk-token'));
    await page.locator('#entries input[data-entry-id]').first().check();await page.locator('#bulk-transfer').click();await page.locator('#bulk-owner').fill('new-owner');await page.locator('#bulk-owner-form button[type="submit"]').click();await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(()=>window.apiCalls.some(call=>call.body?.action==='bulk_transfer' && call.body.confirmed===true && call.body.owner==='new-owner'));
    await page.locator('[data-tab="audit"]').click();await page.locator('#log-player').fill('Steve');const logDownload=page.waitForEvent('download');await page.locator('#log-export').click();assert.equal((await logDownload).suggestedFilename(),'logs.csv');
    await page.locator('[data-tab="operations"]').click();await page.locator('#ops-health .ops-card').first().waitFor();
    await page.locator('[data-ops-tab="backups"]').click();await page.locator('#ops-backup-create').click();await page.locator('#ops-backup-list .ops-card').first().waitFor();
    const backupDownload=page.waitForEvent('download');await page.locator('#ops-backup-list button').filter({hasText:'下载'}).click();assert.match((await backupDownload).suggestedFilename(),/^mc-backup-.*\.zip$/);
    await page.locator('#ops-backup-list button').filter({hasText:'预览恢复'}).click();await page.locator('#ops-restore-preview').waitFor({state:'visible'});await page.locator('#ops-restore-apply').click();await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(()=>window.apiCalls.some(call=>call.body?.action==='restore_apply' && call.body.confirmed===true));
    // Wait for restoration and the follow-up config load to finish before changing tabs.
    await page.waitForFunction(() => document.getElementById('notice').textContent === '插件数据与配置已恢复。' && !document.getElementById('refresh').disabled);
    await page.locator('[data-ops-tab="notifications"]').click();await page.locator('#ops-notification-list button').click();await page.waitForFunction(()=>window.apiCalls.some(call=>call.body?.action==='notification_retry'));
    await page.locator('[data-ops-tab="requests"]').click();await page.locator('#ops-request-list button').filter({hasText:'通过'}).click();await page.locator('#confirm-dialog button[value="confirm"]').click();await page.waitForFunction(()=>window.apiCalls.some(call=>call.body?.action==='approve'));
    await page.locator('[data-ops-tab="cleanup"]').click();await page.locator('#ops-cleanup-preview').click();await page.waitForFunction(()=>!document.getElementById('ops-cleanup-apply').disabled);await page.locator('#ops-cleanup-apply').click();await page.locator('#confirm-dialog button[value="confirm"]').click();await page.waitForFunction(()=>window.apiCalls.some(call=>call.body?.action==='cleanup_apply'));

    // Check every main/config view at narrow phones, tablets and desktop widths.
    for (const width of [320, 390, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: width < 600 ? 844 : 1000 });
      for (const view of ['overview', 'whitelist', 'audit', 'operations', 'settings']) {
        await page.locator(`[data-tab="${view}"]`).click();
        await page.locator('#' + view).waitFor({ state: 'visible' });
        await page.waitForFunction(()=>!document.getElementById('refresh').disabled);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, `${view} overflow at ${width}`);
        if (width === 390 && view === 'whitelist') await screenshot('whitelist-mobile.png');
        if (width === 1440 && view === 'whitelist') await screenshot('whitelist.png');
        if (width === 1440 && view === 'audit') await screenshot('audit.png');
        if(view==='operations') for(const section of ['diagnostics','backups','notifications','requests','cleanup']){await page.locator('[data-ops-tab="'+section+'"]').click();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'operations '+section+' overflow at '+width);if(width===1440 && section==='diagnostics')await screenshot('operations.png');}
        if (view === 'settings') for (const section of ['connections', 'general', 'commands', 'bridge', 'messages', 'spark', 'variables', 'advanced']) {
          await clickTab(section);
          if(section==='messages' && [390,1440].includes(width)) await screenshot(width===390 ? 'message-settings-mobile.png' : 'message-settings.png', false);
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, `${section} overflow at ${width}`);
        }
      }
    }
    await page.setViewportSize({ width: 1280, height: 1000 });
    await clickTab('connections');

    // Recover a native list item containing two comma-separated server IDs.
    await page.evaluate(() => window.prepareCompactServerIds());
    await page.reload();
    await page.waitForFunction(() => document.getElementById('config-state').textContent === '配置待修正');
    assert.deepEqual(JSON.parse(await page.locator('#config-editor').inputValue()).groups[0].server_ids,['server1','server2']);
    assert.equal((await page.locator('#configured-servers [data-id="server1"]').innerText()).includes('尚未绑定群'),false);
    assert.equal((await page.locator('#configured-servers [data-id="server2"]').innerText()).includes('尚未绑定群'),false);
    if(!await page.locator('#manual-settings').evaluate(detail=>detail.open))await page.locator('#manual-settings>summary').click();
    const compactGroup = await page.evaluate(() => window.latestConfig.groups[0].id);
    await page.locator('#configured-groups [data-id="'+compactGroup+'"] button').filter({hasText:'编辑群聊'}).click();
    assert.equal(await page.locator('#entity-fields input[value="server1"]').isChecked(),true);
    assert.equal(await page.locator('#entity-fields input[value="server2"]').isChecked(),true);
    assert.equal(await page.locator('#entity-fields input[value="server3"]').isChecked(),false);
    await submitEntity();
    await save();
    assert.deepEqual(await page.evaluate(() => window.latestConfig.groups[0].server_ids),['server1','server2']);
    assert.equal(await page.locator('#configuration-error').isHidden(),true);

    // Quick setup reads connection metadata without password inputs or separate server/group saves.
    await page.evaluate(()=>window.prepareFreshConfiguration());
    await page.reload();
    await page.waitForFunction(()=>!document.getElementById('settings').hidden && !document.getElementById('reload-config').disabled);
    assert.equal(await page.locator('#quick-root').evaluate(input=>input===document.activeElement),true);
    assert.equal(await page.locator('#configured-servers article').count(),0);
    if(await page.locator('#manual-settings').evaluate(detail=>detail.open))await page.locator('#manual-settings>summary').click();
    assert.equal(await page.locator('#quick-platform-field').isHidden(),true);
    assert.equal(await page.locator('#quick-setup-card input[type=password]').count(),0);
    assert.equal(await page.locator('#save-config').isHidden(),true);
    await page.locator('#quick-root').fill('D:/AutoServers');
    await page.locator('#quick-group').fill('777777777');
    const savesBeforeScan=await page.evaluate(()=>window.apiCalls.filter(call=>call.endpoint==='config' && call.body).length);
    await page.locator('#quick-scan').click();
    await page.waitForFunction(()=>document.querySelectorAll('#quick-server-list input').length===3 && !document.getElementById('quick-scan').disabled);
    assert.equal(await page.locator('#quick-server-list input:checked').count(),2);
    assert.equal(await page.locator('#quick-server-list input[value="needs-rcon"]').isDisabled(),true);
    assert.equal(await page.evaluate(()=>window.apiCalls.filter(call=>call.endpoint==='config' && call.body).length),savesBeforeScan);
    await page.evaluate(()=>window.scrollTo(0,0));
    await screenshot('quick-setup.png',false);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
    await page.evaluate(()=>window.scrollTo(0,0));
    await screenshot('quick-setup-mobile.png',false);
    await page.setViewportSize({width:1280,height:1000});
    await page.locator('#quick-root').fill('D:/AutoServersChanged');
    assert.equal(await page.locator('#quick-results').isHidden(),true);
    await page.locator('#quick-root').fill('D:/AutoServers');
    await page.locator('#quick-scan').click();
    await page.waitForFunction(()=>!document.getElementById('quick-save').disabled);
    await page.evaluate(()=>{window.mockStorageFull=true;});
    await page.locator('#quick-save').click();
    await page.waitForFunction(()=>document.getElementById('quick-status').textContent.includes('空间不足'));
    assert.equal(await page.evaluate(()=>window.latestConfig.servers.length),0);
    await page.evaluate(()=>{window.mockStorageFull=false;});
    await page.locator('#quick-save').click();
    await page.waitForFunction(()=>document.getElementById('notice').textContent.includes('快速配置已完成') && !document.getElementById('refresh').disabled);
    assert.equal(await page.locator('#overview').isVisible(),true);
    assert.equal(await page.locator('#group').inputValue(),'auto-group');
    assert.equal(await page.locator('#server-cards .card').count(),2);
    assert.equal(await page.evaluate(()=>window.latestConfig.groups.length),1);
    assert.equal(await page.evaluate(()=>window.latestConfig.servers.every(server=>server.tps_auto_detect)),true);
    const quickRequest=await page.evaluate(()=>window.apiCalls.filter(call=>call.body?.quick_setup).at(-1).body);
    assert.deepEqual(quickRequest.quick_setup.selected,['alpha','beta']);
    assert.equal('config' in quickRequest,false);
    assert.equal(JSON.stringify(quickRequest).includes('rcon_password'),false);
    await page.locator('[data-tab="settings"]').click();
    await clickTab('connections');
    // First install: the guide and direct-save buttons need no JSON or second save.
    await page.evaluate(() => window.resetEmptyConfig());
    await page.locator('#reload-config').click();
    await page.waitForFunction(() => document.querySelectorAll('#configured-servers article').length === 0);
    if(!await page.locator('#manual-settings').evaluate(detail=>detail.open))await page.locator('#manual-settings>summary').click();
    assert.equal(await page.locator('#add-group').isDisabled(), true);
    assert.equal(await page.locator('#setup-next').textContent(),'填写服务端目录');
    await page.locator('#setup-next').click();
    assert.equal(await page.locator('#setting-server-root').evaluate(input=>input===document.activeElement),true);
    await screenshot('setup-guide.png',false);
    await page.locator('#setting-server-root').fill('D:/Minecraft');
    await page.locator('#setup-next').click();
    assert.equal(await page.locator('#entity-advanced').getAttribute('open'),null);
    await page.locator('#entity-rcon_password').fill('new-private-password');
    await page.locator('#entity-test').click();
    await page.waitForFunction(()=>document.getElementById('entity-test-result').textContent.includes('连接正常'));
    await page.locator('#config-dialog').screenshot({path:path.join(root,'docs/screenshots/server-basic.png')});
    await page.locator('#entity-save').click();
    await page.waitForFunction(()=>window.latestConfig.servers.length===1 && document.getElementById('config-state').textContent==='配置已保存');
    assert.equal(await page.locator('#setup-next').textContent(),'绑定群聊');
    await page.locator('#setup-next').click();
    await page.locator('#entity-platform_id').fill('manual-bot-instance');
    await page.locator('#entity-group_id').fill('999999999');
    await page.locator('#entity-admin_ids').fill('123456789');
    assert.equal(await page.locator('#entity-fields input[value="server1"]').isChecked(),true);
    await page.locator('#entity-fields input[value="server1"]').uncheck();
    await page.locator('#entity-save').click();
    assert.match(await page.locator('#entity-error').textContent(), /至少勾选/);
    await page.locator('#entity-fields input[value="server1"]').check();
    await page.locator('#entity-save').click();
    await page.waitForFunction(()=>window.latestConfig.groups.length===1 && document.getElementById('config-state').textContent==='配置已保存');
    assert.equal(await page.evaluate(() => window.latestConfig.groups[0].platform_id), 'manual-bot-instance');
    assert.equal(await page.evaluate(() => window.latestConfig.groups[0].group_id), '999999999');
    assert.deepEqual(await page.evaluate(() => window.latestConfig.groups[0].server_ids), ['server1']);
    assert.equal(await page.locator('#setup-title').textContent(),'配置已就绪');
    await screenshot('setup-complete.png',false);
    // A broken native template must still open the authenticated repair form.
    await page.evaluate(() => window.prepareRecovery(false));
    await page.reload();
    await page.waitForFunction(() => !document.getElementById('settings').hidden && document.getElementById('config-state').textContent === '配置待修正');
    assert.match(await page.locator('#configuration-error').textContent(), /platform_id.*group_id/);
    assert.equal(await page.locator('#server-count').textContent(), '0');
    if(!await page.locator('#manual-settings').evaluate(detail=>detail.open))await page.locator('#manual-settings>summary').click();
    await page.locator('#configured-groups button').filter({ hasText: '编辑群聊' }).click();
    assert.equal(await page.locator('#entity-id').isEditable(), true);
    await page.locator('#entity-advanced summary').click();
    await page.locator('#entity-id').fill('fixed_group');
    await page.locator('#entity-platform_id').selectOption('qq-bot');
    await page.locator('#entity-group_id').fill('999999999');
    await page.locator('#entity-fields input[value="server1"]').check();
    await submitEntity();
    await save();
    assert.equal(await page.locator('#configuration-error').isHidden(), true);
    assert.equal(await page.locator('#config-state').textContent(), '配置已保存');
    assert.equal(await page.evaluate(() => window.latestConfig.groups[0].id), 'fixed_group');
    // A missing persisted identity restores values from its own historical
    // binding and cannot be changed to another group's identity.
    await page.evaluate(() => window.prepareRecovery(true));
    await page.reload();
    await page.waitForFunction(() => document.getElementById('config-state').textContent === '配置待修正');
    if(!await page.locator('#manual-settings').evaluate(detail=>detail.open))await page.locator('#manual-settings>summary').click();
    await page.locator('#configured-groups button').filter({ hasText: '编辑群聊' }).click();
    assert.equal(await page.locator('#entity-platform_id').isEnabled(), true);
    assert.equal(await page.locator('#entity-platform_id').inputValue(), 'qq-bot');
    assert.equal(await page.locator('#entity-group_id').inputValue(), '999999999');
    await submitEntity(); await save();
    assert.equal(await page.locator('#configuration-error').isHidden(), true);
    assert.deepEqual(errors, []);
    // Save a template directly and remove a server together with its final binding.
    await clickTab('variables');
    await page.locator('#variable-preview-kind').selectOption('tps');
    await page.locator('#variable-preview-template').fill('简洁 {server}：{perf_tps} TPS');
    await page.locator('#variable-save').click();
    await page.waitForFunction(()=>window.latestConfig.messages.tps==='简洁 {server}：{perf_tps} TPS' && document.getElementById('config-state').textContent==='配置已保存');
    await clickTab('connections');
    await page.locator('#configured-servers [data-id="server1"] button').filter({hasText:'移除服务器'}).click();
    await page.locator('#confirm-dialog').waitFor({state:'visible'});
    assert.match(await page.locator('#confirm-text').textContent(),/一并移除绑定/);
    await page.evaluate(()=>{window.mockStorageFull=true;});
    await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(()=>document.getElementById('notice').textContent.includes('空间不足'));
    assert.equal(await page.locator('#configured-servers article').count(),1);
    assert.equal(await page.evaluate(()=>window.latestConfig.servers.length),1);
    await page.evaluate(()=>{window.mockStorageFull=false;});
    await page.locator('#configured-servers [data-id="server1"] button').filter({hasText:'移除服务器'}).click();
    await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(()=>window.latestConfig.servers.length===0 && window.latestConfig.groups.length===0 && document.getElementById('config-state').textContent==='配置已保存');
    console.log('Browser checks passed (' + (officialSdkRoot || 'mock SDK') + '): multi-chat/shared-server forms, overlap validation, passwords, admins, common settings, commands/menu/broadcasts, blue responsive layout, provided logo, diagnostics, backup/download/restore, queue retry, approval, retention, bulk preview/transfer and exports, command-based TPS queries and templates, message bridge switches/directions/prefix/color templates and mobile layout, per-server whitelist overwrite switch, TPS capability detection, query metadata, searchable variable docs/insertion/preview, retired collector config cleanup, storage-full error with preserved draft, spark fresh-log fallback settings and encoding, cross-core numeric variables, retired TPS selector migration and six editable starting templates, all-message category/search/variable insertion/single reset/editor synchronization and persistence, automatic server.properties onboarding with credential-free scanning/one-save group binding/default TPS detection and selected-group navigation, guided setup, save/cancel-only dialogs with no draft actions, direct template saving, canceled edits discarded, failed saves/retries and failed deletes without changing saved records, successful-save refresh failure with credential masking, read-only connection probes, advanced field folding, command presets, message paging, automatic bridge master switch and cascade unbinding, JSON, compact server-ID list repair, unsaved-input error recovery, conflicts, removal, desktop/mobile and existing operations.');
  } catch (error) {
    const failureDir = path.join(root, '.research', 'ui-failures'); fs.mkdirSync(failureDir, { recursive: true });
    await page.screenshot({ path: path.join(failureDir, 'browser-failure.png'), fullPage: true });
    throw error;
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
