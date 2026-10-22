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

module.app.run(host='127.0.0.1', port=int(os.environ.get('UI_TEST_PORT', '8765')), debug=False, threaded=True)
