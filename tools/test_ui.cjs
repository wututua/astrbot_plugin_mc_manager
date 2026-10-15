const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const tpsDefaultTemplate = JSON.parse(fs.readFileSync(path.join(root, 'config.default.json'), 'utf8')).messages.tps;
const tpsVariables = JSON.parse(fs.readFileSync(path.join(root, 'tps.variables.json'), 'utf8')).variables;
const { chromium } = require(path.join(root, '.research/browser/node_modules/playwright'));
const chromiumExecutable = process.env.MC_TEST_CHROMIUM || path.join(process.env.LOCALAPPDATA, 'ms-playwright/chromium-1243/chrome-win64/chrome.exe');

(async () => {
  const server = http.createServer((req,res) => {
    const pathname = decodeURIComponent(req.url.split('?')[0]);
    const target = path.resolve(root,'pages/console',pathname === '/' ? 'index.html' : '.'+pathname);
    if (!target.startsWith(path.resolve(root,'pages/console')+path.sep) || !fs.existsSync(target)) {
      res.writeHead(404);res.end();return;
    }
    const types = { '.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8' };
    res.writeHead(200,{'Content-Type':types[path.extname(target)] || 'application/octet-stream'});
    res.end(fs.readFileSync(target));
  });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  const browser = await chromium.launch({headless:true,executablePath:chromiumExecutable});
  const page = await browser.newPage({viewport:{width:1280,height:1000}});
  const errors=[];page.on('pageerror',err=>errors.push(err.message));
  await page.addInitScript(({ tpsDefaultTemplate, tpsVariables }) => {
    const groups=[{id:'group1',platform_id:'qq-bot',group_id:'111111111',server_ids:['server1','server2','server3'],entries:2,broadcast_ready:true},
                  {id:'group2',platform_id:'qq-bot',group_id:'222222222',server_ids:['server4','server5'],entries:0,broadcast_ready:false}];
    const servers=Array.from({length:5},(_,i)=>({id:'server'+(i+1),name:'服务器 '+(i+1),status:i===2?'离线或 RCON 不可用':'在线',pending:i===2?'RCON 连接不可用，等待自动同步':'',directory:'D:/Minecraft/server'+(i+1)}));
    const entries=[{id:1,name:'Steve',owner:'123456789',uuid:'5627dd98-e6be-3c21-b8a8-e92344183641'}, {id:4,name:'Alex',owner:'987654321',uuid:'36532b5e-c442-3dbb-a24c-c7e55d0f979a'}];
    window.apiCalls=[];
    let config={server_root:'D:/Minecraft',servers:[{id:'server1',tps_command:'tps'},{id:'server2',tps_command:'custom tps'}],groups:[],commands:{prefix:'mc',add:'添加,add'},messages:{help:'欢迎使用 {prefix}'}};
    window.AstrBotPluginPage={ready:async()=>({plugin_name:'astrbot_plugin_mc_manager'}),
      apiGet:async(endpoint,params)=>{
        window.apiCalls.push({endpoint,params});
        if(endpoint==='snapshot')return {groups,servers,jobs:[],notifications_pending:0};
        if(endpoint==='entries')return {entries:params.group==='group1'?entries:[]};
        if(endpoint==='logs')return {logs:[{time:'2026-10-13T14:30:00+00:00',action:'add',group_id:'111111111',user:'123456789',user_name:'群成员',player:'Steve',detail:'添加 #1；同步 server1,server2,server3'}],page:1,total:1};
        if(endpoint==='config')return {config,data_directory:'data/plugin_data/astrbot_plugin_mc_manager',tps_default_template:tpsDefaultTemplate,tps_variables:tpsVariables};
      },
      apiPost:async(endpoint,body)=>{
        window.apiCalls.push({endpoint,body});
        if(endpoint==='config'){config=body.config;return {message:'配置已保存并立即生效。'}}
        return {message:body.action==='restart'?'正在重启，等待广播。':'测试操作完成。'};
      }
    };
  }, { tpsDefaultTemplate, tpsVariables });
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForFunction(()=>document.getElementById('server-count').textContent==='5');
    await page.locator('#refresh').isEnabled();
    assert.equal(await page.locator('.card').count(),3);
    fs.mkdirSync(path.join(root,'docs/screenshots'),{recursive:true});
    await page.screenshot({path:path.join(root,'docs/screenshots/desktop.png'),fullPage:true});
    await page.locator('#group').selectOption('group2');
    await page.waitForFunction(()=>document.querySelectorAll('.card').length===2);
    assert.equal(await page.locator('.card h3').first().textContent(),'服务器 4');
    await page.locator('#group').selectOption('group1');
    await page.waitForFunction(()=>document.querySelectorAll('.card').length===3);
    await page.locator('[data-tab="whitelist"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('#entries tr').length===2);
    await page.locator('#player').fill('Player123');await page.locator('#add-form button').click();
    await page.waitForFunction(()=>window.apiCalls.some(c=>c.body?.action==='add'));
    await page.locator('#entries button').filter({hasText:'修改'}).first().click();
    await page.locator('#edit-player').fill('Player456');await page.locator('#edit-form button[type="submit"]').click();
    await page.waitForFunction(()=>window.apiCalls.some(c=>c.body?.action==='modify'));
    await page.locator('#entries button').filter({hasText:'移除'}).first().click();
    await page.locator('#confirm-dialog button[value="cancel"]').click();
    assert.equal(await page.evaluate(()=>window.apiCalls.filter(c=>c.body?.action==='remove').length),0);
    await page.locator('#entries button').filter({hasText:'移除'}).first().click();
    await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(()=>window.apiCalls.some(c=>c.body?.action==='remove'));
    await page.locator('[data-tab="audit"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('#logs tr').length===1);
    assert.match(await page.locator('#logs').textContent(),/Steve/);
    await page.locator('#log-user').fill('123456789');await page.locator('#log-filter button').click();
    await page.waitForFunction(()=>window.apiCalls.some(c=>c.endpoint==='logs'&&c.params.user==='123456789'));
    await page.locator('[data-tab="settings"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('#tps-variables tr').length===27);
    await page.locator('#tps-template-editor').fill('{server}: {tps_5s} TPS / {mspt_10s_p95}ms / {cpu_process_1m}%');
    assert.match(await page.locator('#config-editor').inputValue(), /cpu_process_1m/);
    await page.locator('#spark-defaults').click();
    await page.waitForFunction(()=>JSON.parse(document.getElementById('config-editor').value).servers.every(s=>s.tps_command==='spark tps'));
    assert.equal(JSON.parse(await page.locator('#config-editor').inputValue()).messages.tps,tpsDefaultTemplate);
    assert.equal(JSON.parse(await page.locator('#config-editor').inputValue()).commands.add,'添加,add');
    await page.locator('#tps-variable-details summary').click();
    await page.screenshot({path:path.join(root,'docs/screenshots/spark-settings.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'spark settings mobile layout overflow');
    await page.setViewportSize({width:1280,height:1000});
    await page.locator('#save-config').click();
    await page.waitForFunction(()=>window.apiCalls.some(c=>c.endpoint==='config'&&c.body?.config.messages?.tps.includes('{tps_5s}')));
    await page.locator('#config-editor').fill('{broken');await page.locator('#save-config').click();
    await page.waitForFunction(()=>document.getElementById('notice').textContent.includes('有效的 JSON'));
    await page.locator('#config-editor').fill(JSON.stringify({server_root:'D:/Minecraft',servers:[],groups:[],commands:{prefix:'mc',add:'add'}}));
    await page.locator('#save-config').click();
    await page.waitForFunction(()=>window.apiCalls.some(c=>c.endpoint==='config'&&c.body));
    await page.locator('[data-tab="overview"]').click();
    await page.locator('.card button').filter({hasText:'重启'}).first().click();
    await page.locator('#confirm-dialog button[value="confirm"]').click();
    await page.waitForFunction(()=>window.apiCalls.some(c=>c.body?.action==='restart'));
    const restart=await page.evaluate(()=>window.apiCalls.find(c=>c.body?.action==='restart'));
    assert.equal(restart.body.group,'group1');assert.deepEqual(restart.body.args,['server1']);
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(root,'docs/screenshots/mobile.png'),fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'mobile layout overflow');
    assert.deepEqual(errors,[]);
    console.log('Browser checks passed: group isolation, add/edit/remove, confirmation, logs, config validation/save, restart target, spark variables/template editor/default command and desktop/mobile.');
  } finally {
    await browser.close();await new Promise(resolve=>server.close(resolve));
  }
})().catch(err=>{console.error(err);process.exitCode=1;});
