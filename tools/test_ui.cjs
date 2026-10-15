const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const schema = JSON.parse(fs.readFileSync(path.join(root, '_conf_schema.json'), 'utf8'));
const defaults = JSON.parse(fs.readFileSync(path.join(root, 'config.default.json'), 'utf8'));
const tpsVariables = JSON.parse(fs.readFileSync(path.join(root, 'tps.variables.json'), 'utf8')).variables;
const officialSdkRoot = process.env.MC_TEST_ASTRBOT_RELEASE;
const officialSdk = officialSdkRoot ? fs.readFileSync(path.join(officialSdkRoot, 'dashboard/plugin_page_bridge.js'), 'utf8') : '';
const { chromium } = require(path.join(root, '.research/browser/node_modules/playwright'));
const chromiumExecutable = process.env.MC_TEST_CHROMIUM || path.join(process.env.LOCALAPPDATA, 'ms-playwright/chromium-1243/chrome-win64/chrome.exe');

(async () => {
  const server = http.createServer((req, res) => {
    const pathname = decodeURIComponent(req.url.split('?')[0]);
    if (pathname === '/bridge-sdk.js' && officialSdk) { res.writeHead(200, { 'Content-Type': 'text/javascript; charset=utf-8' }); res.end(officialSdk); return; }
    const target = path.resolve(root, 'pages/console', pathname === '/' ? 'index.html' : '.' + pathname);
    if (!target.startsWith(path.resolve(root, 'pages/console') + path.sep) || !fs.existsSync(target)) { res.writeHead(404); res.end(); return; }
    const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' };
    res.writeHead(200, { 'Content-Type': types[path.extname(target)] || 'application/octet-stream' });
    let content = fs.readFileSync(target);
    if (officialSdk && path.extname(target) === '.html') content = content.toString('utf8').replace('<script type="module" src="./app.js">', '<script src="/bridge-sdk.js"></script><script type="module" src="./app.js">');
    res.end(content);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true, executablePath: chromiumExecutable });
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(({ schema, defaults, tpsVariables, useOfficialSdk }) => {
    const clone = data => JSON.parse(JSON.stringify(data));
    let config = { ...clone(defaults), server_root: 'D:/Minecraft', unknown_setting: 'preserved',
      servers: Array.from({ length: 5 }, (_, i) => ({ id: 'server' + (i + 1), name: '服务器 ' + (i + 1), directory: 'server' + (i + 1), rcon_host: '127.0.0.1', rcon_port: 25575 + i,
        rcon_password: i === 0 ? '__KEEP__' : '', rcon_password_env: i === 0 ? '' : 'MC_RCON_' + (i + 1), online_mode: false, tps_command: 'tps', players_command: 'list', tps_source: 'auto', spark_file: '.astrbot-spark-tps.txt', future_option: 'preserved' })),
      groups: [{ id: 'group1', platform_id: 'qq-bot', group_id: '111111111', server_ids: ['server1', 'server2', 'server3'], admin_ids: ['123456789'], max_entries_per_user: 1, broadcast_origin: '' },
               { id: 'group2', platform_id: 'qq-bot', group_id: '222222222', server_ids: ['server4', 'server5'], admin_ids: ['987654321'], max_entries_per_user: 1, broadcast_origin: '' }] };
    let revision = 'revision-1';
    const entries = [{ id: 1, name: 'Steve', owner: '123456789', uuid: '5627dd98-e6be-3c21-b8a8-e92344183641' }, { id: 4, name: 'Alex', owner: '987654321', uuid: '36532b5e-c442-3dbb-a24c-c7e55d0f979a' }];
    const bindings = { groups: config.groups.map(g => ({ id: g.id, platform: g.platform_id, group_id: g.group_id })), servers: config.groups.flatMap(g => g.server_ids.map(s => ({ server: s, scope: g.id }))) };
    window.apiCalls = []; window.mockConflict = false; window.latestConfig = clone(config);
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
    const backend = {
      ready: async () => ({ plugin_name: 'astrbot_plugin_mc_manager' }),
      apiGet: async (endpoint, params) => {
        window.apiCalls.push({ endpoint, params });
        if (endpoint === 'snapshot' && configError) return { groups: [], servers: [], jobs: [], notifications_pending: 0, config_error: configError, configuration_required: true };
        if (endpoint === 'snapshot') return { groups: config.groups.map(g => ({ ...clone(g), entries: g.id === 'group1' ? 2 : 0, broadcast_ready: g.id === 'group1' })),
          servers: config.servers.map((s, i) => ({ ...clone(s), directory: 'D:/Minecraft/' + s.directory, status: i === 2 ? '离线或 RCON 不可用' : '在线', pending: i === 2 ? 'RCON 不可用，等待自动同步' : '' })), jobs: [], notifications_pending: 0 };
        if (endpoint === 'entries') return { entries: params.group === 'group1' ? clone(entries) : [] };
        if (endpoint === 'logs') return { logs: [{ time: '2026-10-13T14:30:00+00:00', action: 'add', group_id: '111111111', user: '123456789', user_name: '群成员', player: 'Steve', detail: '添加 #1；同步 server1,server2,server3' }], page: 1, total: 1 };
        if (endpoint === 'config') return { config: clone(config), config_error: configError, schema, platforms: window.noPlatforms ? [] : [{ id: 'qq-bot', name: 'aiocqhttp', display_name: 'QQ / OneBot' }], bindings: clone(bindings), revision,
          data_directory: 'data/plugin_data/astrbot_plugin_mc_manager', tps_default_template: defaults.messages.tps, tps_variables: tpsVariables };
      },
      apiPost: async (endpoint, body) => {
        window.apiCalls.push({ endpoint, body: clone(body) });
        if (endpoint === 'config') {
          if (window.mockConflict) return { status: 'error', message: '配置已被其他页面修改，请先重新载入配置后再保存。' };
          if (body.revision !== revision) throw new Error('配置版本冲突');
          config = clone(body.config); configError = ''; sessionStorage.removeItem('mc-test-recovery');
          for (const server of config.servers) if (server.rcon_password) server.rcon_password = '__KEEP__';
          for (const group of config.groups) {
            if (!bindings.groups.some(g => g.id === group.id)) bindings.groups.push({ id: group.id, platform: group.platform_id, group_id: group.group_id });
            for (const id of group.server_ids) if (!bindings.servers.some(s => s.server === id)) bindings.servers.push({ server: id, scope: group.id });
          }
          revision = 'revision-' + (Number(revision.split('-')[1]) + 1);
          window.latestConfig = clone(config);
          return { message: '配置已保存并立即生效。' };
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
  }, { schema, defaults, tpsVariables, useOfficialSdk: Boolean(officialSdk) });
  const clickTab = name => page.locator(`[data-settings-tab="${name}"]`).click();
  async function save() {
    const count = await page.evaluate(() => window.apiCalls.filter(c => c.endpoint === 'config' && c.body).length);
    await page.locator('#save-config').click();
    await page.waitForFunction(previous => window.apiCalls.filter(c => c.endpoint === 'config' && c.body).length > previous, count);
    await page.waitForFunction(() => !document.getElementById('save-config').disabled);
  }
  async function screenshot(name) { await page.screenshot({ path: path.join(root, 'docs/screenshots', name), fullPage: true }); }
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForFunction(() => document.getElementById('server-count').textContent === '5');
    fs.mkdirSync(path.join(root, 'docs/screenshots'), { recursive: true });
    assert.equal(await page.locator('.card').count(), 3);
    await screenshot('desktop.png');
    await page.locator('#group').selectOption('group2');
    await page.waitForFunction(() => document.querySelectorAll('.card').length === 2);
    await page.locator('#group').selectOption('group1');
    await page.waitForFunction(() => document.querySelectorAll('.card').length === 3);
    await page.locator('[data-tab="whitelist"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#entries tr').length === 2);
    await page.locator('#player').fill('Player123'); await page.locator('#add-form button').click();
    await page.waitForFunction(() => window.apiCalls.some(c => c.body?.action === 'add'));
    await page.locator('#entries button').filter({ hasText: '修改' }).first().click();
    await page.locator('#edit-player').fill('Player456'); await page.locator('#edit-form button[type="submit"]').click();
    await page.waitForFunction(() => window.apiCalls.some(c => c.body?.action === 'modify'));
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
    await page.waitForFunction(() => document.querySelectorAll('#configured-servers article').length === 5);
    assert.equal(await page.locator('#configured-groups article').count(), 2);
    assert.equal(await page.locator('#config-state').textContent(), '配置已保存');
    await screenshot('web-settings.png');
    await page.locator('#configured-servers [data-id="server1"] button').filter({ hasText: '编辑服务器' }).click();
    assert.equal(await page.locator('#entity-id').getAttribute('readonly'), '');
    assert.equal(await page.locator('#entity-rcon_password').inputValue(), '');
    await page.locator('#entity-name').fill('生存服');
    await page.locator('#entity-apply').click();
    assert.match(await page.locator('#config-state').textContent(), /未保存/);
    await page.locator('#add-server').click();
    assert.equal(await page.locator('#entity-id').inputValue(), 'server6');
    await page.locator('#entity-name').fill('群三专用服');
    await page.locator('#entity-rcon_password').fill('new-private-password');
    await page.locator('#entity-apply').click();
    assert.equal(await page.locator('#configured-servers article').count(), 6);
    await page.locator('#add-group').click();
    assert.equal(await page.locator('#entity-platform_id').inputValue(), 'qq-bot');
    await page.locator('#entity-group_id').fill('333333333');
    await page.locator('#entity-admin_ids').fill('12345\n67890');
    await page.locator('#entity-max_entries_per_user').fill('2');
    assert.equal(await page.locator('#entity-fields input[value="server1"]').isDisabled(), true);
    assert.equal(await page.locator('#entity-fields input[value="server4"]').isDisabled(), true);
    await page.locator('#entity-fields input[value="server6"]').check();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'mobile dialog overflow');
    await screenshot('add-group-mobile.png');
    await page.setViewportSize({ width: 1280, height: 1000 });
    await page.locator('#config-dialog').evaluate(dialog => { dialog.scrollTop = 0; });
    await page.locator('#config-dialog').screenshot({ path: path.join(root, 'docs/screenshots/add-group.png') });
    await page.locator('#entity-apply').click();
    assert.equal(await page.locator('#configured-groups article').count(), 3);
    // Switching/refreshing operational views must preserve the unsaved draft.
    await page.locator('[data-tab="overview"]').click(); await page.locator('[data-tab="settings"]').click();
    await page.locator('#refresh').click();
    assert.equal(await page.locator('#configured-groups article').count(), 3);
    await clickTab('general'); await page.locator('#general-page_size').fill('15');
    await page.locator('#general-game_broadcast').uncheck();
    await clickTab('commands'); await page.locator('#command-add').fill('add,申请');
    await page.locator('#message-help').fill('欢迎！{prefix} {add} <玩家名>');
    await page.locator('#preview-help').click();
    assert.equal(await page.locator('#help-preview').textContent(), '欢迎！/mc add <玩家名>');
    await clickTab('messages'); await page.locator('#message-restart_success').fill('服务器 {server} 已恢复，用时 {seconds} 秒。');
    await save();
    let latest = await page.evaluate(() => window.latestConfig);
    assert.equal(latest.servers[0].rcon_password, '__KEEP__');
    assert.equal(latest.servers[0].future_option, 'preserved');
    assert.equal(latest.unknown_setting, 'preserved');
    assert.equal(latest.servers[0].name, '生存服');
    assert.deepEqual(latest.groups[2].server_ids, ['server6']);
    assert.deepEqual(latest.groups[2].admin_ids, ['12345', '67890']);
    assert.equal(latest.page_size, 15); assert.equal(latest.game_broadcast, false);
    assert.equal(latest.commands.add, 'add,申请');
    assert.equal(await page.locator('#config-state').textContent(), '配置已保存');
    await clickTab('connections');
    await page.locator('#configured-groups [data-id="group1"] button').filter({ hasText: '编辑群聊' }).click();
    for (const id of ['entity-id', 'entity-group_id', 'entity-platform_id']) assert.equal(await page.locator('#' + id).getAttribute('readonly'), '');
    assert.equal(await page.locator('#entity-fields input[value="server1"]').isDisabled(), false);
    assert.equal(await page.locator('#entity-fields input[value="server4"]').isDisabled(), true);
    await page.locator('#entity-cancel').click();

    await clickTab('spark');
    assert.equal(await page.locator('#tps-variables tr').count(), 27);
    await page.locator('#tps-template-editor').fill('{server}: {tps_5s} TPS / {cpu_process_1m}%');
    await page.locator('#spark-defaults').click();
    await page.locator('#tps-variable-details summary').click();
    assert.equal(await page.locator('#tps-template-editor').inputValue(), defaults.messages.tps);
    await screenshot('spark-settings.png');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'mobile settings overflow');
    await screenshot('web-settings-mobile.png');
    await page.setViewportSize({ width: 1280, height: 1000 });
    await save();
    latest = await page.evaluate(() => window.latestConfig);
    assert.equal(latest.servers.every(s => s.tps_command === 'spark tps'), true);
    assert.equal(latest.commands.add, 'add,申请');

    await clickTab('advanced');
    await page.locator('#config-editor').fill('{broken'); await page.locator('#save-config').click();
    await page.waitForFunction(() => document.getElementById('notice').textContent.includes('有效的 JSON'));
    assert.equal(await page.locator('#config-state').textContent(), '有未保存的修改');
    const advanced = await page.evaluate(() => window.latestConfig); advanced.commands.prefix = 'minecraft';
    await page.locator('#config-editor').fill(JSON.stringify(advanced)); await page.locator('#apply-json').click();
    await clickTab('commands'); assert.equal(await page.locator('#command-prefix').inputValue(), 'minecraft');
    await save();
    // Backend conflicts keep the draft visible and never pretend to save it.
    await page.locator('#command-prefix').fill('newprefix');
    await page.evaluate(() => { window.mockConflict = true; });
    await save();
    assert.match(await page.locator('#notice').textContent(), /其他页面/);
    assert.equal(await page.locator('#command-prefix').inputValue(), 'newprefix');
    assert.equal(await page.evaluate(() => window.latestConfig.commands.prefix), 'minecraft');
    await page.evaluate(() => { window.mockConflict = false; });
    await page.locator('#reload-config').click(); await page.locator('#confirm-dialog button[value="cancel"]').click();
    assert.equal(await page.locator('#command-prefix').inputValue(), 'newprefix');
    await page.locator('#reload-config').click(); await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(() => document.getElementById('command-prefix').value === 'minecraft');

    await clickTab('connections');
    await page.locator('#configured-servers [data-id="server6"] button').filter({ hasText: '移除服务器' }).click();
    assert.match(await page.locator('#notice').textContent(), /仍绑定/);
    await page.locator('#configured-groups [data-id="group3"] button').filter({ hasText: '移除群绑定' }).click();
    await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#configured-groups article').length === 2);
    await page.locator('#configured-servers [data-id="server6"] button').filter({ hasText: '移除服务器' }).click();
    await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#configured-servers article').length === 5);
    await save();
    await page.locator('#add-group').click();
    assert.equal(await page.locator('#entity-fields input[value="server1"]').isDisabled(), true);
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
    await page.locator('#entity-rcon_password_env').fill('MC_RCON_CLEAR_TEST');
    await page.locator('#entity-apply').click(); await save();
    assert.equal(await page.evaluate(() => window.latestConfig.servers[0].rcon_password), '');
    assert.equal(await page.evaluate(() => window.latestConfig.servers[0].rcon_password_env), 'MC_RCON_CLEAR_TEST');

    // First install: setup uses form controls with no JSON knowledge required.
    await page.evaluate(() => window.resetEmptyConfig());
    await page.locator('#reload-config').click();
    await page.waitForFunction(() => document.querySelectorAll('#configured-servers article').length === 0);
    assert.equal(await page.locator('#add-group').isDisabled(), true);
    await page.locator('#setting-server-root').fill('D:/Minecraft');
    await page.locator('#add-server').click();
    await page.locator('#entity-rcon_password_env').fill('MC_RCON_NEW');
    await page.locator('#entity-apply').click();
    await page.locator('#add-group').click();
    await page.locator('#entity-platform_id').fill('manual-bot-instance');
    await page.locator('#entity-group_id').fill('999999999');
    await page.locator('#entity-admin_ids').fill('123456789');
    await page.locator('#entity-apply').click();
    assert.match(await page.locator('#entity-error').textContent(), /至少勾选/);
    await page.locator('#entity-fields input[value="server1"]').check();
    await page.locator('#entity-apply').click();
    await save();
    assert.equal(await page.evaluate(() => window.latestConfig.groups[0].platform_id), 'manual-bot-instance');
    assert.equal(await page.evaluate(() => window.latestConfig.groups[0].group_id), '999999999');
    assert.deepEqual(await page.evaluate(() => window.latestConfig.groups[0].server_ids), ['server1']);
    assert.equal(await page.locator('#configured-groups article').count(), 1);
    // A broken native template must still open the authenticated repair form.
    await page.evaluate(() => window.prepareRecovery(false));
    await page.reload();
    await page.waitForFunction(() => !document.getElementById('settings').hidden && document.getElementById('config-state').textContent === '配置待修正');
    assert.match(await page.locator('#configuration-error').textContent(), /platform_id.*group_id/);
    assert.equal(await page.locator('#server-count').textContent(), '0');
    await page.locator('#configured-groups button').filter({ hasText: '编辑群聊' }).click();
    assert.equal(await page.locator('#entity-id').isEditable(), true);
    await page.locator('#entity-id').fill('fixed_group');
    await page.locator('#entity-platform_id').selectOption('qq-bot');
    await page.locator('#entity-group_id').fill('999999999');
    await page.locator('#entity-fields input[value="server1"]').check();
    await page.locator('#entity-apply').click();
    await save();
    assert.equal(await page.locator('#configuration-error').isHidden(), true);
    assert.equal(await page.locator('#config-state').textContent(), '配置已保存');
    assert.equal(await page.evaluate(() => window.latestConfig.groups[0].id), 'fixed_group');
    // A missing persisted identity restores values from its own historical
    // binding and cannot be changed to another group's identity.
    await page.evaluate(() => window.prepareRecovery(true));
    await page.reload();
    await page.waitForFunction(() => document.getElementById('config-state').textContent === '配置待修正');
    await page.locator('#configured-groups button').filter({ hasText: '编辑群聊' }).click();
    assert.equal(await page.locator('#entity-platform_id').getAttribute('readonly'), '');
    assert.equal(await page.locator('#entity-platform_id').inputValue(), 'qq-bot');
    assert.equal(await page.locator('#entity-group_id').inputValue(), '999999999');
    await page.locator('#entity-apply').click(); await save();
    assert.equal(await page.locator('#configuration-error').isHidden(), true);
    assert.deepEqual(errors, []);
    console.log('Browser checks passed (' + (officialSdkRoot || 'mock SDK') + '): server/group forms, historical isolation, passwords, admins, common settings, commands/menu/broadcasts, spark templates, JSON, draft persistence, conflicts, removal, desktop/mobile and existing operations.');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
