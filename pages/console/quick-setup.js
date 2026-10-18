const $=id=>document.getElementById(id);
function el(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}

export function createQuickSetup({api,work,getContext,save,openManual}){
  let plan=null;
  let selected=new Set();
  let busy=false;
  let focusPending=false;
  let context={config:{},platforms:[],revision:''};
  function platformValue(){return context.platforms.length ? $('quick-platform').value : $('quick-platform-manual').value.trim();}
  function status(text,error=false){$('quick-status').textContent=text;$('quick-status').classList.toggle('error',error);}
  function invalidate(text='') {plan=null;selected.clear();$('quick-results').hidden=true;$('quick-server-list').replaceChildren();if(text)status(text);sync();}
  function sync(){
    const rows=plan?.servers.filter(row=>selected.has(row.key)) || [];
    const mixed=new Set(rows.map(row=>row.online_mode)).size>1;
    $('quick-scan').disabled=busy || !$('quick-root').value.trim() || getContext().hasChanges;
    $('quick-save').disabled=busy || !plan || !rows.length || mixed || !$('quick-group').value.trim() || !platformValue();
    $('quick-selected').textContent=mixed?'所选服务器的 UUID 模式不同，请分开绑定。':`已选择 ${rows.length} 台服务器，保存后将关联到所填群聊，现有绑定会保留。`;
    $('quick-selected').classList.toggle('error',mixed);
    for(const id of ['quick-root','quick-group','quick-host','quick-platform','quick-platform-manual'])$(id).disabled=busy;
    if(!busy && focusPending){focusPending=false;$('quick-root').focus();}
    $('quick-all').disabled=busy || !plan;
    $('quick-none').disabled=busy || !plan;
    $('quick-manual').disabled=busy;
    document.querySelectorAll('#quick-server-list input').forEach(input=>{input.disabled=busy || input.dataset.ready!=='true';});
    if(getContext().hasChanges && !busy)status('页面中还有未保存的设置，请先保存或重新载入，再使用快速配置。',true);
  }
  function renderRows(){
    $('quick-warnings').replaceChildren();for(const warning of plan?.warnings || [])$('quick-warnings').append(el('p',warning,'quick-issue'));
    const container=$('quick-server-list');container.replaceChildren();
    for(const row of plan?.servers || []){
      const card=el('label',undefined,'quick-server');
      const check=el('input');check.type='checkbox';check.value=row.key;check.dataset.ready=String(row.ready);check.checked=selected.has(row.key);
      check.addEventListener('change',()=>{if(check.checked)selected.add(row.key);else selected.delete(row.key);sync();});
      const info=el('div');info.append(el('strong',row.name),el('span',row.directory,'field-hint'));
      info.append(el('p',`${row.configured?row.rcon_host:$('quick-host').value || '127.0.0.1'}:${row.rcon_port ?? '端口需设置'} · ${row.online_mode?'正版 UUID':'离线 UUID'} · ${row.configured?'已配置，沿用原参数':row.ready?'已读取 RCON 参数':'需要处理配置'}`));
      for(const issue of row.issues || [])info.append(el('p',issue,'quick-issue'));
      card.append(check,info);container.append(card);
    }
    sync();
  }
  $('quick-root').addEventListener('input',()=>invalidate('目录改变后请重新扫描。'));
  for(const id of ['quick-group','quick-platform','quick-platform-manual'])$(id).addEventListener('input',sync);
  $('quick-platform').addEventListener('change',sync);
  $('quick-host').addEventListener('input',renderRows);
  $('quick-all').addEventListener('click',()=>{selected=new Set((plan?.servers || []).filter(row=>row.ready).map(row=>row.key));renderRows();});
  $('quick-none').addEventListener('click',()=>{selected.clear();renderRows();});
  $('quick-manual').addEventListener('click',()=>{const root=plan?.server_root || $('quick-root').value.trim();invalidate();openManual(root);});
  $('quick-scan').addEventListener('click',()=>work(async()=>{
    const current=getContext();
    if(current.hasChanges){status('请先保存或重新载入当前设置。',true);return;}
    invalidate();status('正在读取目录中的 server.properties…');
    try{
      plan=await api('apiPost','operations',{action:'discover_servers',server_root:$('quick-root').value.trim()});
      const fresh=plan.servers.filter(row=>row.ready && !row.configured && row.whitelist_enabled && !(row.issues || []).length);
      const available=plan.servers.filter(row=>row.ready && row.whitelist_enabled && !(row.issues || []).length);
      selected=new Set((fresh.length?fresh:available.length===1?available:[]).map(row=>row.key));
      $('quick-results').hidden=false;
      $('quick-scan-root').textContent=plan.single_server?`已识别单台服务端。共同父目录：${plan.server_root}`:`共同父目录：${plan.server_root}`;
      renderRows();
      status(plan.servers.length?`找到 ${plan.servers.length} 台服务器。核对勾选范围后，直接保存并完成。`:'没有找到 server.properties。请填写单台服务端目录或更接近服务端的父目录。',!plan.servers.length);
    }catch(error){invalidate();status(error.message || String(error),true);}
  }));
  $('quick-save').addEventListener('click',()=>work(async()=>{
    if(!plan)return;
    try{
      const result=await save({token:plan.token,selected:[...selected],platform_id:platformValue(),group_id:$('quick-group').value.trim(),rcon_host:$('quick-host').value.trim() || '127.0.0.1'},plan.revision);
      invalidate();status(result.message || '配置已保存，在对应群聊发送 /mc 即可使用。');
    }catch(error){status(error.message || String(error),true);}
  }));
  return {
    setBusy(value){busy=value;sync();},
    update(next){
      if(context.revision && context.revision!==next.revision){invalidate('配置已更新，新增服务器时请重新扫描。');$('quick-root').value=next.config.server_root || '';}
      context=next;
      if(!$('quick-root').value && next.config.server_root)$('quick-root').value=next.config.server_root;
      const previous=$('quick-platform').value;
      $('quick-platform').replaceChildren();
      if(next.platforms.length>1){const placeholder=el('option','请选择消息机器人');placeholder.value='';$('quick-platform').append(placeholder);}
      for(const platform of next.platforms){const option=el('option',`${platform.display_name} · ${platform.id}`);option.value=platform.id;$('quick-platform').append(option);}
      if(next.platforms.some(platform=>platform.id===previous))$('quick-platform').value=previous;
      $('quick-platform-field').hidden=next.platforms.length<=1;
      $('quick-platform-manual-field').hidden=next.platforms.length>0;
      $('quick-platform-hint').textContent=next.platforms.length===1?`使用消息机器人：${next.platforms[0].display_name}（${next.platforms[0].id}）`:next.platforms.length?'请选择要绑定的机器人。':'没有可选机器人，请填写群内 /sid 返回的 Bot ID。';
      const groups=$('quick-known-groups');groups.replaceChildren();
      for(const group of next.config.groups || []){const option=el('option');option.label=`现有配置 ${group.id} · ${group.platform_id}`;option.value=Array.isArray(group.group_id)?group.group_id.join(','):String(group.group_id || '');groups.append(option);}
      sync();
    },
    focus(){ $('quick-setup-card').scrollIntoView({block:'start'});if(busy)focusPending=true;else $('quick-root').focus();}
  };
}
