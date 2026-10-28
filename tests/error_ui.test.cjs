const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const ctx={console};vm.createContext(ctx);vm.runInContext(fs.readFileSync('static/error_ui.js','utf8'),ctx);const ui=ctx.NewsErrors;
test('evidence classes and no keyword attribution',()=>{
 for(const code of ['codex_timeout','credentials','capacity','rate_limit_exceeded','insufficient_quota','model_not_found','summary_input_limit','ECONNRESET','summary_validation_failed']) {
  assert.notEqual(ui.explain(code).category,'unknown');
 }
 for(const raw of ['network is bad','capacity maybe','403','429','application bug','<script>boom</script>']) assert.equal(ui.explain(raw).category,'unknown');
 assert.equal(ui.explain({httpStatus:429}).message,'请求过于频繁（HTTP 429）。');
 assert.equal(ui.explain('codex_protocol_error').message,'返回结果格式无效。');
 assert.equal(ui.explain('codex_timeout').message,'请求超时。');
 assert.equal(ui.explain('empty_archive_summary').message,'没有可归档的实质内容。');
 assert.equal(ui.explain('invalid_archive_summary').message,'归档仍超出长度限制，请重试。');
});
test('errors are concise and preserve reported details without diagnosis',()=>{
 for (const context of ['summary','settings','detail','agent']) {
  const error = ui.explain('summary_save_failed', {context});
  assert.equal(error.message, '总结保存失败。');
  assert.equal(error.action, '');
  assert.equal(error.state, '');
 }
 assert.equal(ui.explain(new Error('Failed to fetch')).message, '网络请求失败。');
 assert.equal(ui.explain('BROWSER_CONNECT: Browser Bridge extension not connected').message, '浏览器连接失败。 浏览器桥接扩展未连接。');
 assert.equal(ui.explain('READ_FAILED: 无法读取网页正文').message, 'READ_FAILED: 无法读取网页正文');
 assert.equal(ui.explain('抓取失败：网页未返回正文').message, '抓取失败：网页未返回正文');
 const response = ui.fromResponse({error:'translation_test_failed',message:'模型测试失败（HTTP 401）'}, {status:502});
 assert.equal(ui.explain(response).message, '模型测试失败（HTTP 401）');
 assert.equal(ui.explain(ui.fromResponse({error:'new_error',message:'具体错误信息'}, {status:500})).message, '具体错误信息');
});
test('no arbitrary payload in displayed or copied evidence',()=>{
 const payload={error:'Authorization: Bearer sk-secret',message:'private article',detail:'api_key=abc https://user:pass@example.test',diagnostic:{exit_code:1,seconds:3,body:'article',config:'KEY',stderr:'Authorization'}};
 const e=ui.explain(payload,{context:'summary',stage:'save'});const all=JSON.stringify(e);
 for(const forbidden of ['sk-secret','private article','api_key','user:pass','Authorization','KEY']) assert.ok(!all.includes(forbidden));
 assert.match(e.details,/exit_code：1/);
 assert.equal(ui.explain({error:'codex_secretabcdefghijklmnopqrstuvwxyz'}).code,'unknown');
});
function documentMock(copySuccess=true) {
 let copied='',removed=false,focused=false;const body={appendChild:()=>{}};
 return {body,activeElement:{focus:()=>focused=true},execCommand:()=>copySuccess,
  createElement:()=>({value:'',style:{},setAttribute:()=>{},select(){copied=this.value;},remove:()=>removed=true}),
  state:()=>({copied,removed,focused})};
}
test('HTTP copy fallback when clipboard absent or rejected',async()=>{
 for(const nav of [{},{clipboard:{writeText:async()=>{throw Error('HTTP')}}}]) {
  const doc=documentMock();assert.equal(await ui.copy('错误码：codex_timeout\nAuthorization: bad\nHTTP：500',doc,nav),true);
  assert.ok(doc.state().removed);assert.ok(doc.state().focused);assert.ok(!doc.state().copied.includes('Authorization'));
 }
 const doc=documentMock(false);assert.equal(await ui.copy('错误码：unknown',doc,{}),false);
});
test('plain text rendering with accessible manual fallback',async()=>{
 const nodes=[];const doc=documentMock(false);doc.createElement=(tag)=>{const n={tag,children:[],style:{},setAttribute(){},appendChild(c){this.children.push(c)},addEventListener(_,fn){this.click=fn},remove(){},select(){}};nodes.push(n);return n};
 const root={ownerDocument:doc,children:[],replaceChildren(){this.children=[]},appendChild(c){this.children.push(c)}};
 ui.render(root,'<img src=x onerror=alert(1)>',{context:'detail'});
 assert.equal(root.children[1].tag,'details');assert.ok(nodes.every(n=>!Object.hasOwn(n,'innerHTML')));
 assert.equal(root.children[0].textContent,'<img src=x onerror=alert(1)>');
});
test('shared errors load before modular controllers',()=>{
 const helper=fs.readFileSync('static/ui.js','utf8');
 const index=fs.readFileSync('static/index.html','utf8');
 assert.ok(helper.includes('NewsErrors.fromResponse'));
 assert.ok(helper.includes('NewsErrors.explain'));
 for(const file of ['app.js','agent-panel.js','settings-panel.js']) {
   const source=fs.readFileSync('static/'+file,'utf8');
   assert.ok(source.includes('errorText'));
   assert.ok(source.includes('./ui.js'));
 }
 assert.ok(index.indexOf('/static/error_ui.js')<index.indexOf('/static/app.js'));
 assert.ok(!index.includes('/static/news_summaries.js'));
});
