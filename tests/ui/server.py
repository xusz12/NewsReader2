"""Isolated UI fixture server: real SQLite/API, no model calls or background workers."""
import json
import os
import sys
import tempfile
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(prefix='newsreader-ui-')
RUNTIME = Path(TEMP.name)
for name, path in {
    'DB_PATH': RUNTIME / 'news.sqlite3',
    'APP_SETTINGS_PATH': RUNTIME / 'settings.json',
    'AGENT_DB_PATH': RUNTIME / 'agent.sqlite3',
    'AGENT_RUNTIME_DIR': RUNTIME / 'agent',
    'UPDATER_DIR': RUNTIME / 'updater',
    'MEDIA_CACHE_DIR': RUNTIME / 'media',
    'DAILY_NEWS_DIR': RUNTIME / 'daily',
    'DAILY_BRIEFING_DIR': RUNTIME / 'briefings',
}.items():
    os.environ['NEWS_READER_' + name] = str(path)
(RUNTIME / 'daily').mkdir()
(RUNTIME / 'settings.json').write_text(json.dumps({'llm': {'pi_chat': {'provider': 'test', 'model': 'test-model'}}}))
import app as module
module.ensure_db()
# Summary UI exercises real preview/transaction APIs against this temporary DB.
# Only model execution is replaced; never invoke a user's CLI from this server.
summary_mode = {"value": "success"}
module.news_summaries.native_config_stamp = lambda config: []
def fixture_summary(config, prompt, cancel, diagnostics):
    mode = summary_mode["value"]
    end = time.monotonic() + (30 if mode == "wait" else .25)
    while time.monotonic() < end:
        if cancel():
            raise RuntimeError("summary_cancelled")
        time.sleep(.02)
    if mode == "fail":
        raise RuntimeError("summary_runtime_failed")
    ref = re.search(r"^(N\d+) \|", prompt, re.M).group(1)
    return ("## 本期主要变化\n\n准备金变化、先进制程与开源推理模型构成本期关注的三条线索。[[" + ref + "]]\n\n"
            "## 科技与流动性的发展脉络\n\n- **宏观政策**：材料讨论了准备金水平及短期融资压力。[[" + ref + "]]\n"
            "- **科技产业**：报道涉及先进制程试产与推理模型部署。[[" + ref + "]]\n\n"
            "## 继续观察\n\n当前材料不足以判断后续效果，需要继续跟踪披露。[[" + ref + "]]")
module.NEWS_SUMMARY_SERVICE.runner = fixture_summary
# Do not inspect local credentials or call model catalogs in UI tests.
module.provider_secret_value = lambda provider: ''
module.provider_has_configured_key = lambda provider: False
module.deepseek_settings_snapshot = lambda model: {
    'service': {'configured': False, 'status_text': '未配置密钥'},
    'catalog': {'options': []},
}
module.pi_chat_settings_snapshot = lambda *args: {
    'service': {'configured': True},
    'catalog': {'provider_options': [{'value': 'test', 'label': 'Test provider'}], 'options': [{'value': 'test-model', 'label': 'Test model'}], 'model_options_by_provider': {'test': [{'value': 'test-model', 'label': 'Test model'}]}},
}
module.model_catalogs.codex_catalog = lambda config: {
    'source': 'fixture-codex', 'error': '', 'default_label': '沿用本机默认', 'resolved_default_model': '',
    'options': [
        {'value': 'ui-catalog-a', 'label': 'ui-catalog-a', 'reasoning_efforts': ['low', 'high']},
        {'value': 'ui-catalog-b', 'label': 'ui-catalog-b', 'reasoning_efforts': ['medium']},
    ],
}
# Deterministic archive generation; the real archive API still persists and tags the card.
def fixture_archive(**kwargs):
    return {'provider': 'pi', 'model': 'fixture', 'summary': '**流动性传导研读结论**\n\n- 核心判断：关注准备金与短期融资利率的变化。\n- 待核实：政策调整能否缓解季末资金压力。'}
module.run_pi_chat_archive = fixture_archive
module.run_codex_chat_archive = fixture_archive
fixtures = json.loads((ROOT / 'tests/ui/articles.json').read_text())
with module.db_conn() as conn:
    for index in range(65):
        item = fixtures[index % len(fixtures)]
        ident = f'ui-{index:03}'
        date = f'2026-10-{14 - index // 15:02}'
        published = f'{date} {14 - index % 12:02}:02:18'
        url = item['url'] + f'/ui-{index}'
        original = item['original']
        if index == 64:
            original += '\n\n📌 **重复判断** 与 [引用](https://example.com/重复判断) 和 `code`。\n\n重复判断。'
        title = item['title'] if index < len(fixtures) else f'{item["title"]} · 历史 {index}'
        conn.execute('INSERT INTO items(id,source_file,item_order,published_at,date,time,source,source_type,source_name,title,summary,url,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (ident, f'dailyFreshNews_{date}.md', index, published,date,published[11:16],item['source'],'news',item['source'],title,item['summary'],url,published,published))
        conn.execute('INSERT INTO article_details(url,title,author,published_at,content,content_length,fetched_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                     (url,title,'News desk',published,original,len(original),published,published))
        conn.execute('INSERT INTO article_ai(url,model,key_points_zh,conclusion_zh,body_zh,generated_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                     (url,'deepseek-v4-flash',json.dumps(item['points'],ensure_ascii=False),item['conclusion'],item['body'],published,published))
        if index == 0:
            conn.execute('INSERT INTO item_state(item_id,important_at,updated_at) VALUES (?,?,?)',(ident,published,published))
            conn.execute('INSERT INTO article_notes(url,note,created_at,updated_at) VALUES (?,?,?,?)',(url,'关注准备金变化与流动性传导。',published,published))
        if index == 4:
            conn.execute('INSERT INTO item_state(item_id,favorite_at,updated_at) VALUES (?,?,?)',(ident,published,published))
with module.db_conn() as conn:
    module.news_classification.enqueue(conn)
    for index in range(65):
        category = 'economy' if index < 40 else ['china', 'ai', 'politics', 'business', 'science', 'other'][(index - 40) % 6]
        conn.execute("UPDATE item_classifications SET status='done',category=?,model='fixture',updated_at=? WHERE item_id=?",
                     (category, time.time(), f'ui-{index:03}'))

@module.app.post('/__test/reset-reading')
def reset_reading():
    with module.db_conn() as conn:
        conn.execute("UPDATE item_state SET read_at=NULL")
    return {"ok": True}

@module.app.post('/__test/summary-mode')
def set_summary_mode():
    summary_mode["value"] = module.request.get_json()["mode"]
    return {"ok": True}

@module.app.post('/__test/reset-summaries')
def reset_summaries():
    service = module.NEWS_SUMMARY_SERVICE
    service.context()
    for event, worker in list(service.workers.values()):
        event.set()
        worker.join(timeout=3)
    with module.db_conn() as conn:
        conn.execute('DELETE FROM news_summaries')
        conn.execute('DELETE FROM news_summary_jobs')
        conn.execute('DELETE FROM news_summary_previews')
        conn.execute('UPDATE item_state SET read_at=NULL')
    summary_mode["value"] = "success"
    return {"ok": True}

# Notification fixtures use real persistence and result transactions, never a model.

agent_article_fixtures = {}

@module.app.post('/__test/agent-article')
def agent_article():
    body = module.request.get_json()
    item, _ = module._load_visible_agent_article(body.get('item_id', 'ui-064'))
    url = item['url']
    with module.db_conn() as conn:
        if url not in agent_article_fixtures:
            agent_article_fixtures[url] = {
                table: dict(row) if (row := conn.execute('SELECT * FROM ' + table + ' WHERE url=?', (url,)).fetchone()) else None
                for table in ['article_details', 'article_ai']
            }
        for table, original in agent_article_fixtures[url].items():
            conn.execute('DELETE FROM ' + table + ' WHERE url=?', (url,))
            if not body.get('missing') and original:
                columns = ','.join(original)
                conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({','.join('?' for _ in original)})", tuple(original.values()))
    return {'ok': True}

@module.app.post('/__test/reset-agent')
def reset_agent():
    with module.agent_db_conn() as conn:
        for table in ['agent_notifications', 'agent_messages', 'agent_jobs', 'agent_sessions']:
            conn.execute('DELETE FROM ' + table)
    return {'ok': True}

@module.app.post('/__test/agent-complete')
def complete_agent():
    body = module.request.get_json()
    item, detail = module._load_visible_agent_article(body.get('item_id', 'ui-064'))
    with module.agent_db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if body.get('job_id'):
            job = conn.execute('SELECT * FROM agent_jobs WHERE id=?', (body['job_id'],)).fetchone()
        else:
            session = module._agent_active_session(conn, item['id'])
            if not session or body.get('new_session'):
                session = module._agent_create_session(conn, item, detail)
            job = module._agent_insert_job(conn, session, question='隔离测试问题')
        stamp = module.now_ts()
        status = body.get('status', 'succeeded')
        answer = body.get('answer', '通知样例：这是一轮已经完成的研究回复。')
        error = 'oar_auth_required' if status == 'failed' else ''
        conn.execute('UPDATE agent_jobs SET status=?, answer_text=?, error=?, finished_at=?, updated_at=? WHERE id=?', (status, answer, error, stamp, stamp, job['id']))
        conn.execute("UPDATE agent_messages SET status=?, content=?, updated_at=? WHERE job_id=? AND role='assistant'", (status, answer, stamp, job['id']))
        module.agent_notifications.record(conn, job['id'])
        if body.get('expire'):
            conn.execute("UPDATE agent_sessions SET expires_at='2000-01-01 00:00:00' WHERE id=?", (job['session_id'],))
    return {'ok': True, 'job_id': job['id'], 'session_id': job['session_id']}


# Topic prototypes exercise actual creation/matching storage with a deterministic
# research runner. No real search, Jev requests, or native agents are used.
topic_fixture_mode = {"value": "success"}
def fixture_topic_research(config, prompt, cancel, diagnostics, *, web):
    time.sleep(.08)
    if topic_fixture_mode["value"] == "fail":
        raise RuntimeError("topic_runtime_failed")
    sources = [{"title": "隔离测试公告", "url": "https://example.com/topic-source"}]
    if web:
        diagnostics.append({"research_tools": 1})
    if "用户事件：" in prompt:
        return json.dumps({"name": "云杉芯片上市进程", "definition": "追踪云杉芯片的上市进程",
            "include_scope": "上市筹备、申请、审核、发行和挂牌", "exclude_scope": "无关产品发布",
            "start_date": "2026-01-01", "sources": sources}, ensure_ascii=False)
    if "1–12 个历史阶段里程碑" in prompt:
        return json.dumps({"overview": "已递交申请，正在等待审核结果。", "sources": sources, "nodes": [
            {"date": "2026-01-01", "title": "启动筹备", "body": "公司启动上市筹备。", "sources": sources},
            {"date": "2026-09-01", "title": "递交申请", "body": "公司递交上市申请。", "sources": sources}
        ]}, ensure_ascii=False)
    if "精简现有事件脉络" in prompt:
        nodes = json.loads(prompt.split("\n已有节点：")[1])
        return json.dumps({"nodes":[{"date":nodes[-1]["date"],"title":"交易进入监管审查阶段",
            "body":"收购公布后进入监管审查，等待最终结论。","node_ids":[n["id"] for n in nodes]}]},ensure_ascii=False)
    if "\n定义对话：" in prompt:
        messages = json.loads(prompt.split("\n定义对话：")[1])
        draft = json.loads(prompt.split("\n当前定义：")[1].split("\n初始研究来源：")[0])
        message = messages[-1]["content"]
        time.sleep(.4)
        if "修订失败" in message and not topic_fixture_mode.get("chat_failed"):
            topic_fixture_mode["chat_failed"] = True
            raise RuntimeError("topic_runtime_failed")
        if "仅追踪审核" in message:
            draft.update(definition="仅追踪云杉芯片的审核进程",include_scope="上市审核及正式决定")
            return json.dumps({"reply":"已缩小到审核进程，保留其他规则。","definition":draft},ensure_ascii=False)
        return json.dumps({"reply":"一般股价波动不属于上市审核进展；当前定义保持不变。","definition":None},ensure_ascii=False)
    return json.dumps({"overview": "新新闻已整理。", "nodes": []}, ensure_ascii=False)
module.TOPIC_SERVICE.runner = fixture_topic_research

@module.app.post('/__test/topics')
def fixture_topics():
    body = module.request.get_json(silent=True) or {}
    topic_fixture_mode["value"] = body.get("mode", "success")
    topic_fixture_mode.pop("chat_failed", None)
    with module.db_conn() as conn:
        for table in ("topic_node_items", "topic_nodes", "topic_checks", "topic_jobs", "topic_definition_messages", "topic_refinements", "event_topics"):
            conn.execute("DELETE FROM " + table)
        module.topic_tracking.seed_ingestions(conn)
        cutoff = conn.execute("SELECT COALESCE(MAX(seq),0) FROM topic_ingestions").fetchone()[0]
        if body.get("empty"):
            return {"ok": True}
        for ident, name in (("test-acquisition", "北辰科技收购远川数据"), ("test-industry", "数据基础设施并购")):
            conn.execute("""INSERT INTO event_topics(id,request_id,query,name,definition,include_scope,exclude_scope,
                start_date,timeline_mode,status,cutoff_seq,overview,created_at,updated_at,activated_at)
                VALUES (?,?,?,?,'追踪交易及监管审查','交易进展','无关产品','2026-01-01',?,'ready',?,
                '交易仍在审查中，等待最终批准。','2026-09-01','2026-10-24','2026-09-01')""",
                (ident,ident,name,name,body.get("timeline_mode","events"),cutoff))
            for number,stamp,title in ((0,"2026-01-01","公布收购"),(1,"2026-10-24","审查进展")):
                node_id=ident+"-node-"+str(number)
                conn.execute("INSERT INTO topic_nodes(id,topic_id,kind,date,title,body,updated_at) VALUES (?,?,'history',?,?,?,'2026-10-24')",
                             (node_id,ident,stamp,title,title+"的详细内容。"))
            if body.get("long_timeline"):
                for number in range(2,14):
                    conn.execute("INSERT INTO topic_nodes(id,topic_id,kind,date,title,body,updated_at) VALUES (?,?,'history',?,?,?,'2026-10-24')",
                        (ident+"-node-"+str(number),ident,f"2026-10-{number:02}",f"阶段进展 {number}","测试历史节点。"))
            for index in range(65 if body.get("many") else 2):
                item=dict(conn.execute("SELECT * FROM items WHERE id=?",(f"ui-{index:03}",)).fetchone())
                snapshot=module.topic_tracking.item_snapshot(item)
                conn.execute("""INSERT INTO topic_checks(topic_id,item_id,revision,fingerprint,item_json,status,matched,processed,updated_at)
                    VALUES (?,?,1,?,?,'done',1,1,?)""",
                    (ident,item["id"],module.topic_tracking.fingerprint(snapshot),json.dumps(snapshot,ensure_ascii=False),time.time()))
                conn.execute("INSERT INTO topic_node_items(node_id,item_id) VALUES (?,?)",(ident+"-node-"+str(index % 2),item["id"]))
            if body.get("refined_recently"):
                stamp=module.topic_tracking.now()
                conn.execute("""INSERT INTO topic_jobs(id,topic_id,kind,status,revision,config_json,created_at,updated_at)
                    VALUES (?,?,'refine','done',1,'{}',?,?)""",(ident+'-refine',ident,stamp,stamp))
        if body.get("many_topics"):
            for index in range(14):
                ident=f"extra-topic-{index}"
                conn.execute("""INSERT INTO event_topics(id,request_id,query,name,status,cutoff_seq,created_at,updated_at)
                    VALUES (?,?,?,?,'ready',?,'2026-09-01','2026-10-24')""",(ident,ident,'其他事件',f'其他追踪主题 {index}',cutoff))
    return {"ok": True}

@module.app.post('/__test/topic-mode')
def fixture_topic_mode():
    topic_fixture_mode["value"] = module.request.get_json()["mode"]
    return {"ok": True}

module.app.run(host='127.0.0.1', port=int(os.environ.get('UI_TEST_PORT', '8765')), debug=False, threaded=True)
