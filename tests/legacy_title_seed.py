"""Persist v0.5.2-format fixtures without enabling the retired collector policy."""
import hashlib
import html
import unicodedata
import json
import uuid

from news_identity import canonical_url, identity_key

STAMP = '2026-11-09T10:00:00+08:00'
PUBLISHED = '2026-10-15 09:00:00'


def normalized_title(title):
    text = unicodedata.normalize("NFC", html.unescape(str(title or "")))
    text = text.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "\ufeff": "", "\u200b": ""}))
    return " ".join(text.split())


def title_id(url, title):
    return hashlib.sha256(json.dumps([canonical_url(url), normalized_title(title)],
                                    ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def seed_title(conn, url, title, *, status='done', ident=None, root=None, previous=None, run_id=None, source_id='world'):
    ident = ident or title_id(url,title)
    root = root or ident
    run_id = run_id or uuid.uuid4().hex
    raw = {'url':url,'title':title,'source':'Reuters · World','source_name':'Reuters',
           'source_type':'reuters','translation_policy':'never'}
    translated = {'title':title,'summary':'','quoted_text':''}
    conn.execute('INSERT OR IGNORE INTO news_identities VALUES (?,?,0)',(identity_key(url),root))
    conn.execute('INSERT INTO news_title_versions VALUES (?,?,?,?,?,?,?,?,0)',
                 (title_id(url,title),identity_key(url),ident,title,int(root!=ident),root,previous,STAMP))
    conn.execute("INSERT OR IGNORE INTO collection_runs(id,trigger,status,created_at,publication_status) VALUES (?,'manual','done',?,?)",
                 (run_id,STAMP,'done' if status=='done' else 'pending'))
    conn.execute("""INSERT OR IGNORE INTO collection_run_sources(run_id,source_id,config_json,status,count,added,duplicate_count,filtered_count,ingested_count,started_at)
        VALUES (?,?,'{}','done',0,0,0,0,0,?)""",(run_id,source_id,STAMP))
    conn.execute('UPDATE collection_run_sources SET count=count+1,added=added+1,ingested_count=ingested_count+? WHERE run_id=? AND source_id=?',
                 (int(status=='done'),run_id,source_id))
    conn.execute('''INSERT INTO collection_items(item_id,run_id,source_id,raw_json,translation_json,status,collected_at,published_at,ingested_at)
        VALUES (?,?,?,?,?,?,?,?,?)''', (ident,run_id,source_id,json.dumps(raw),json.dumps(translated) if status in {'ready','done'} else None,
                                      status,STAMP,PUBLISHED,STAMP if status=='done' else None))
    if status == 'done':
        # Old versions froze whatever was actually cached when each variant
        # entered the library. Tests deliberately include uncaptured bodies.
        if previous:
            values = []
            for table in ('article_details','article_ai'):
                row = conn.execute('SELECT * FROM '+table+' WHERE url=?',(url,)).fetchone()
                values.append(json.dumps(dict(row)) if row else None)
            conn.execute('INSERT INTO news_version_snapshots VALUES (?,?,?)',(previous,*values))
            for table in ('article_details','article_ai','detail_jobs','ai_jobs'):
                conn.execute('DELETE FROM '+table+' WHERE url=?',(url,))
        conn.execute('''INSERT INTO items VALUES (?,'',0,?,'2026-10-15','09:00','Reuters · World','reuters','Reuters',?,'',?,?,?)''',
                     (ident,PUBLISHED,title,url,STAMP,STAMP))
        conn.execute('INSERT OR REPLACE INTO news_article_heads VALUES (?,?)',(identity_key(url),ident))
    return ident
