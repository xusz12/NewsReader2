"""Isolated UI fixture server: real SQLite/API, no model calls or background workers."""
import json
import os
import sys
import tempfile
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
    'MEDIA_CACHE_DIR': RUNTIME / 'media',
    'DAILY_NEWS_DIR': RUNTIME / 'daily',
    'DAILY_BRIEFING_DIR': RUNTIME / 'briefings',
}.items():
    os.environ['NEWS_READER_' + name] = str(path)
(RUNTIME / 'daily').mkdir()
import app as module
module.ensure_db()
# Do not inspect local credentials or call model catalogs in UI tests.
module.deepseek_settings_snapshot = lambda model: {
    'service': {'configured': False, 'status_text': '未配置密钥'},
    'catalog': {'options': [{'value': 'deepseek-v4-flash', 'label': 'deepseek-v4-flash'}]},
}
module.pi_chat_settings_snapshot = lambda *args: {
    'service': {'configured': True},
    'catalog': {'provider_options': [{'value': 'test', 'label': 'Test provider'}], 'options': [{'value': 'test-model', 'label': 'Test model'}], 'model_options_by_provider': {'test': [{'value': 'test-model', 'label': 'Test model'}]}},
}
fixtures = json.loads((ROOT / 'tests/ui/articles.json').read_text())
with module.db_conn() as conn:
    for index in range(65):
        item = fixtures[index % len(fixtures)]
        ident = f'ui-{index:03}'
        date = f'2026-10-{14 - index // 15:02}'
        published = f'{date} {14 - index % 12:02}:02:18'
        url = item['url'] + f'/ui-{index}'
        title = item['title'] if index < len(fixtures) else f'{item["title"]} · 历史 {index}'
        conn.execute('INSERT INTO items(id,source_file,item_order,published_at,date,time,source,source_type,source_name,title,summary,url,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (ident, f'dailyFreshNews_{date}.md', index, published,date,published[11:16],item['source'],'news',item['source'],title,item['summary'],url,published,published))
        conn.execute('INSERT INTO article_details(url,title,author,published_at,content,content_length,fetched_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                     (url,title,'News desk',published,item['original'],len(item['original']),published,published))
        conn.execute('INSERT INTO article_ai(url,model,key_points_zh,conclusion_zh,body_zh,generated_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                     (url,'deepseek-v4-flash',json.dumps(item['points'],ensure_ascii=False),item['conclusion'],item['body'],published,published))
        if index == 0:
            conn.execute('INSERT INTO item_state(item_id,important_at,updated_at) VALUES (?,?,?)',(ident,published,published))
            conn.execute('INSERT INTO article_notes(url,note,created_at,updated_at) VALUES (?,?,?,?)',(url,'关注准备金变化与流动性传导。',published,published))
        if index == 4:
            conn.execute('INSERT INTO item_state(item_id,favorite_at,updated_at) VALUES (?,?,?)',(ident,published,published))
module.app.run(host='127.0.0.1', port=int(os.environ.get('UI_TEST_PORT', '8765')), debug=False, threaded=True)
