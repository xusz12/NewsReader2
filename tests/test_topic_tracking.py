import copy
import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import news_classification as nc
import news_summaries
import topic_tracking as tt
from scanner import apply_schema, reindex
from settings import DEFAULT_APP_SETTINGS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = [{"title": "交易公告", "url": "https://example.com/announcement"}]
DRAFT = dict(name="甲收购乙", definition="甲收购乙的交易进程", include_scope="收购及监管审查",
             exclude_scope="一般股价消息", start_date="2026-01-01", timeline_mode="events", rules_revision=1)


def article(conn, ident, title="甲收购乙获批", summary="监管机构已批准本次交易", published="2026-10-24 12:00:00"):
    conn.execute("""INSERT INTO items(id,source_file,item_order,published_at,date,time,source,source_type,source_name,
        title,summary,url,created_at,updated_at) VALUES (?, 'fixture.md',0,?,?,'12:00','Example','news','Example',?,?,?, 'now','now')""",
        (ident,published,published[:10],title,summary,"https://example.com/"+ident))
    tt.seed_ingestions(conn)
    nc.enqueue(conn)
    tt.enqueue_checks(conn)
    conn.commit()


@pytest.fixture
def env(tmp_path, monkeypatch):
    path=tmp_path/"topics.sqlite3"
    def connect():
        conn=sqlite3.connect(path, timeout=10)
        conn.row_factory=sqlite3.Row
        return conn
    with closing(connect()) as conn:
        apply_schema(conn, ROOT/"schema.sql")
    config=copy.deepcopy(DEFAULT_APP_SETTINGS)
    config["agent"]["runtime"]="pi"
    config["llm"]["pi_chat"].update(provider="test", model="test")
    monkeypatch.setattr(news_summaries,"native_config_stamp",lambda _: [])
    calls=[]
    def runner(config,prompt,cancel,diagnostics,*,web):
        calls.append((prompt,web))
        if web:
            diagnostics.append({"research_tools":1})
        if "用户事件：" in prompt:
            return tt.js({**DRAFT,"sources":SOURCE})
        if "1–12 个历史阶段里程碑" in prompt:
            return tt.js({"overview":"交易正在审查","sources":SOURCE,"nodes":[
                {"date":"2026-01-01","title":"公布收购","body":"双方公布交易","sources":SOURCE}]})
        batch=json.loads(prompt.split("\n本批新闻：")[1])
        context=json.loads(prompt.split("\n主题：")[1].split("\n已有概况：")[0]) if "\n主题：" in prompt else {}
        monthly='"timeline_mode":"monthly"' in prompt
        return tt.js({"overview":"交易取得新进展","nodes":[{"id":"","date":"2026-10-01" if monthly else "2026-10-24",
            "title":"十月进展" if monthly else "交易获批","body":"监管已批准本次交易",
            "item_ids":[i["id"] for i in batch]}]})
    service=tt.Service(connect,lambda:config,runner=runner)
    monkeypatch.setattr(service,"start",lambda:None)
    return connect,service,calls


def ready(service, request_id="create-one", mode="events"):
    topic=service.create({"query":"甲收购乙","request_id":request_id})
    assert service.process_one()
    assert service.detail(topic["id"])["topic"]["status"]=="draft"
    service.initialize(topic["id"],DRAFT)
    assert service.process_one()
    assert service.detail(topic["id"])["topic"]["status"]=="ready"
    if mode == "monthly":  # Existing monthly topics survive removal of the creation option.
        with closing(service.connect()) as conn,conn:
            conn.execute("UPDATE event_topics SET timeline_mode='monthly' WHERE id=?",(topic["id"],))
    return topic["id"]


def fake_jev(monkeypatch, choices=None):
    calls=[]
    def urlopen(req,**kwargs):
        request=json.loads(req.data)
        calls.append(request)
        answers={}
        for key in request["questions"]:
            if key=="topic":
                answers[key]={"type":"choice","choice":"商业与产业","confidence":1,"probabilities":{k:float(k=="商业与产业") for k in nc.TOPICS}}
            elif key=="china_focus":
                answers[key]={"type":"noul","noul":0}
            else:
                choice=(choices or {}).get(key,"新增节点" if key=="milestone" else "属于")
                if callable(choice):choice=choice(request)
                answers[key]={"type":"choice","choice":choice,"confidence":1,"probabilities":{k:float(k==choice) for k in request["questions"][key]["criteria"]}}
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps({"model":"fixture-jev","answers":answers,"usage":{}}).encode()
        return Response()
    monkeypatch.setattr(nc,"urlopen",urlopen)
    return calls


def edit_body(service, ident, **changes):
    return {**service.detail(ident)["topic"], **changes}


def draft_topic(service):
    topic=service.create({"query":"甲收购乙", "request_id":"discussion"})
    service.process_one()
    return topic["id"]


def discussion_body(service, ident, message="为什么排除股价？", request_id="turn-one", **changes):
    draft=edit_body(service, ident, **changes)
    return {"message":message,"request_id":request_id,"definition":draft,"rules_revision":draft["rules_revision"]}


def test_definition_discussion_multiturn_persists_and_requires_explicit_current_confirmation(env):
    connect,service,calls=env;ident=draft_topic(service)
    initial=service.detail(ident)
    assert [m['role'] for m in initial['messages']]==['user','assistant']
    assert tt.CONFIRM_DEFINITION in initial['messages'][-1]['content']
    old_draft=initial['topic']
    research_runner=service.runner
    seen=[]
    def runner(config,prompt,cancel,diagnostics,*,web):
        assert web and config['runtime']=='pi'
        seen.append(prompt)
        if len(seen)==1:
            return tt.js({'reply':'股价波动不是交易进程，范围不变。','definition':None})
        assert '股价波动不是交易进程' in prompt
        assert '手动补充的纳入范围' in prompt
        return tt.js({'reply':'已按要求缩小范围。','definition':{**DRAFT,'definition':'只追踪最终审批',
                     'include_scope':'手动补充的纳入范围','horizontal_rule':'只收录最终批准或否决'}})
    service.runner=runner
    service.discuss(ident,discussion_body(service,ident))
    with pytest.raises(ValueError,match='topic_busy'):
        service.initialize(ident,old_draft)
    service.process_one()
    assert service.detail(ident)['topic']['definition']==DRAFT['definition']
    service.discuss(ident,discussion_body(service,ident,'仅保留最终审批','turn-two',include_scope='手动补充的纳入范围'))
    service.process_one()
    # New Service instance reads the same conversation without an in-memory native session.
    restored=tt.Service(connect,service.settings).detail(ident)
    assert restored['topic']['status']=='draft' and restored['nodes']==[]
    assert len(restored['messages'])==6
    assert restored['topic']['definition']=='只追踪最终审批'
    assert '只追踪最终审批' in restored['messages'][-1]['content']
    with pytest.raises(ValueError,match='topic_draft_changed'):
        service.initialize(ident,old_draft)
    assert service.detail(ident)['topic']['status']=='draft'
    service.initialize(ident,restored['topic'])
    assert service.detail(ident)['topic']['status']=='initializing'
    assert service.detail(ident)['job']['kind']=='initialize'
    service.runner=research_runner;service.process_one()
    assert service.detail(ident)['topic']['status']=='ready'
    assert '只追踪最终审批' in calls[-1][0] and '只收录最终批准或否决' in calls[-1][0]


def test_discussion_idempotency_retry_and_failed_reply_do_not_duplicate_turns(env):
    connect,service,_=env;ident=draft_topic(service)
    body=discussion_body(service,ident)
    service.discuss(ident,body);service.discuss(ident,body)
    with pytest.raises(ValueError,match='topic_invalid_request'):
        service.discuss(ident,{**body,'message':'另一问题'})
    with pytest.raises(ValueError,match='topic_busy'):
        service.discuss(ident,{**body,'request_id':'parallel'})
    service.runner=lambda *args,**kwargs: tt.js({'reply':'不完整的修订','definition':{'name':'bad'}})
    service.process_one()
    failed=service.detail(ident)
    assert failed['job']['status']=='failed' and len(failed['messages'])==3
    assert failed['topic']['definition']==DRAFT['definition']
    with pytest.raises(ValueError,match='topic_retry_required'):
        service.initialize(ident,failed['topic'])
    prompts=[]
    def successful(*args,**kwargs):
        prompts.append(args[1]);return tt.js({'reply':'无需修改范围。','definition':None})
    service.runner=successful
    service.retry(ident);service.process_one()
    service.discuss(ident,body) # Recover a lost HTTP response even after the turn finishes.
    done=service.detail(ident)
    assert len(done['messages'])==4 and done['job']['status']=='done'
    assert prompts[0].count('为什么排除股价？')==1
    assert done['topic']['status']=='draft'
    with pytest.raises(ValueError,match='topic_draft_changed'):
        service.discuss(ident,{**body,'request_id':'stale'})


def test_discussion_supersedes_failure_and_recovers_legacy_draft(env):
    connect,service,_=env;ident=draft_topic(service)
    with closing(connect()) as conn,conn:conn.execute('DELETE FROM topic_definition_messages')
    assert len(service.detail(ident)['messages'])==2
    service.discuss(ident,discussion_body(service,ident))
    service.runner=lambda *a,**kw: 'invalid-json'
    service.process_one()
    service.discuss(ident,discussion_body(service,ident,'先按原定义继续','replacement'))
    service.runner=lambda *a,**kw: tt.js({'reply':'请点击确认按钮。','definition':None})
    service.process_one()
    detail=service.detail(ident)
    assert len(detail['messages'])==5 and detail['topic']['status']=='draft'
    with closing(connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM topic_jobs WHERE status='failed'").fetchone()[0]==0
    service.initialize(ident,detail['topic'])


def test_definition_chat_api_validation_and_delete_during_reply(env,api,monkeypatch):
    connect,service,_=env;module,client=api
    monkeypatch.setattr(module,'TOPIC_SERVICE',service);monkeypatch.setattr(module,'db_conn',connect)
    ident=draft_topic(service);path='/api/topics/'+ident
    assert client.post(path+'/messages',json=[]).status_code==400
    assert client.post(path+'/messages',json=discussion_body(service,ident,message=' ')).status_code==400
    assert client.post(path+'/messages',json=discussion_body(service,ident,rules_revision=0)).status_code==409
    assert client.post(path+'/initialize',json={k:v for k,v in DRAFT.items() if k!='rules_revision'}).status_code==409
    assert client.post(path+'/messages',json=discussion_body(service,ident)).status_code==202
    assert client.post(path+'/initialize',json=DRAFT).status_code==409
    def delete_during_reply(*a,**kw):
        assert client.delete(path).status_code==200
        assert a[2]()
        return tt.js({'reply':'迟到的答复','definition':None})
    service.runner=delete_during_reply;service.process_one()
    assert client.get(path).status_code==404
    with closing(connect()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM topic_definition_messages').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM topic_jobs').fetchone()[0]==0


def test_rule_revision_keeps_old_pending_and_completed_news_and_nodes(env,monkeypatch):
    connect,service,_=env
    ident=ready(service)
    with closing(connect()) as conn:article(conn,'already-matched');article(conn,'queued-before-edit')
    fake_jev(monkeypatch)
    worker=nc.Service(connect,lambda:('key','model'),topic_queue=tt.MatchQueue())
    worker.process_one('key','model')
    before=service.detail(ident)
    original=edit_body(service,ident)
    saved=service.revise_definition(ident,{**original,'definition':'只追踪交易终止','horizontal_rule':'仅正式终止公告'})
    assert saved['rules_revision']==original['rules_revision']+1
    assert saved['revision']==original['revision'] and saved['cutoff_seq']==original['cutoff_seq']
    assert service.detail(ident)['items']==before['items'] and service.detail(ident)['nodes']==before['nodes']
    with closing(connect()) as conn:
        article(conn,'arrived-after-edit')
        queue=tt.MatchQueue();old=queue.claim(conn);new=queue.claim(conn)
        assert old['item']['id']=='queued-before-edit' and old['topics'][0]['definition']==DRAFT['definition']
        assert new['item']['id']=='arrived-after-edit' and new['topics'][0]['definition']=='只追踪交易终止'
        assert new['topics'][0]['horizontal_rule']=='仅正式终止公告'
    with pytest.raises(ValueError,match='topic_definition_changed'):
        service.revise_definition(ident,{**original,'definition':'另一个窗口的旧修改'})


def test_edit_captures_preexisting_ingestions_even_before_matching_queue_runs(env):
    connect,service,_=env;ident=ready(service)
    with closing(connect()) as conn,conn:
        article(conn,'between-index-and-queue')
        conn.execute('DELETE FROM topic_checks')
    service.revise_definition(ident,edit_body(service,ident,definition='新定义'))
    with closing(connect()) as conn:
        work=tt.MatchQueue().claim(conn)
        assert work['topics'][0]['definition']==DRAFT['definition']


def test_new_topics_reject_monthly_mode(env):
    _,service,_=env
    ident=service.create({'query':'交易','request_id':'monthly-rejected'})['id'];service.process_one()
    with pytest.raises(ValueError,match='topic_invalid_input'):
        service.initialize(ident,{**DRAFT,'timeline_mode':'monthly'})
    assert service.detail(ident)['topic']['status']=='draft'


def test_jev_milestones_use_snapshot_and_raw_news_without_agent(env,monkeypatch):
    connect,service,agent_calls=env;ident=ready(service)
    with closing(connect()) as conn:article(conn,'first','交易正式获批','监管机构正式批准交易')
    calls=fake_jev(monkeypatch)
    worker=nc.Service(connect,lambda:('key','model'),topic_queue=tt.MatchQueue())
    assert worker.process_one('key','model')
    with closing(connect()) as conn,conn:conn.execute('UPDATE topic_checks SET updated_at=0')
    service.revise_definition(ident,edit_body(service,ident,horizontal_rule='仅交易终止'))
    service.settings()['llm']['pi_chat']['model']=''  # Important progress must not require Agent config.
    assert not service.process_one()
    assert worker.process_one('key','model')
    data=service.detail(ident);node=next(n for n in data['nodes'] if n['kind']=='event')
    assert (node['title'],node['body'])==('交易正式获批','监管机构正式批准交易')
    assert service.detail(ident,node_id=node['id'])['total']==1
    assert len(agent_calls)==2 and all(web for _,web in agent_calls)
    assert calls[-1]['state']=={'title':'交易正式获批','summary':'监管机构正式批准交易'}
    assert tt.DEFAULT_HORIZONTAL_RULE in calls[-1]['questions']['milestone']['instructions']
    assert '仅交易终止' not in calls[-1]['questions']['milestone']['instructions']


@pytest.mark.parametrize('choice',['不收录','信息不足','existing'])
def test_milestone_skip_or_existing_keeps_membership_without_new_node(env,monkeypatch,choice):
    connect,service,_=env;ident=ready(service)
    old=service.detail(ident)['nodes'][0]
    actual='existing:'+old['id'] if choice=='existing' else choice
    fake_jev(monkeypatch,{'milestone':actual})
    worker=nc.Service(connect,lambda:('k','m'),topic_queue=tt.MatchQueue())
    with closing(connect()) as conn:article(conn,'news')
    worker.process_one('k','m')
    with closing(connect()) as conn,conn:conn.execute('UPDATE topic_checks SET updated_at=0')
    service.process_one();assert worker.process_one('k','m')
    detail=service.detail(ident)
    assert detail['nodes']==[{**old,'news_count':int(choice=='existing')}] and detail['total']==1
    assert service.detail(ident,node_id=old['id'])['total']==int(choice=='existing')
    assert detail['job']['status']=='done'


def test_milestone_serialization_uses_new_node_for_duplicate_and_rejects_stale_worker(env,monkeypatch):
    connect,service,_=env;ident=ready(service)
    with closing(connect()) as conn:article(conn,'first');article(conn,'second')
    fake_jev(monkeypatch)
    worker=nc.Service(connect,lambda:('k','m'),topic_queue=tt.MatchQueue())
    worker.process_one('k','m');worker.process_one('k','m')
    with closing(connect()) as conn,conn:conn.execute('UPDATE topic_checks SET updated_at=0')
    service.process_one();queue=tt.MatchQueue()
    with closing(connect()) as conn:
        first=queue.claim_milestone(conn)
        assert queue.claim_milestone(conn) is None
        queue.finish_milestone(conn,first,{'choice':'新增节点','model':'test'})
        second=queue.claim_milestone(conn)
        target=next(n for n in second['nodes'] if n['title']=='甲收购乙获批')
        queue.finish_milestone(conn,second,{'choice':'existing:'+target['id'],'model':'test'})
        queue.finish_milestone(conn,first,{'choice':'新增节点','model':'test'})
    assert len(service.detail(ident)['nodes'])==2
    assert service.detail(ident,node_id=target['id'])['total']==2


@pytest.mark.parametrize('change',['archive','source'])
def test_milestone_rejects_archive_or_changed_source_inflight(env,monkeypatch,change):
    connect,service,_=env;ident=ready(service)
    with closing(connect()) as conn:article(conn,'news')
    fake_jev(monkeypatch);nc.Service(connect,lambda:('k','m'),topic_queue=tt.MatchQueue()).process_one('k','m')
    with closing(connect()) as conn,conn:conn.execute('UPDATE topic_checks SET updated_at=0')
    service.process_one();queue=tt.MatchQueue()
    with closing(connect()) as conn:work=queue.claim_milestone(conn)
    before=service.detail(ident)['nodes']
    if change=='archive':service.set_archived(ident,True)
    else:
        with closing(connect()) as conn,conn:
            conn.execute("UPDATE items SET summary='更正后的摘要' WHERE id='news'");tt.enqueue_checks(conn)
    with closing(connect()) as conn:queue.finish_milestone(conn,work,{'choice':'新增节点','model':'test'})
    assert service.detail(ident)['nodes']==before


def test_milestone_failure_retry_reuses_jev_and_preserves_articles(env,monkeypatch):
    connect,service,_=env;ident=ready(service)
    with closing(connect()) as conn:article(conn,'news')
    fake_jev(monkeypatch);worker=nc.Service(connect,lambda:('k','m'),topic_queue=tt.MatchQueue());worker.process_one('k','m')
    with closing(connect()) as conn,conn:conn.execute('UPDATE topic_checks SET updated_at=0')
    service.process_one();queue=tt.MatchQueue()
    with closing(connect()) as conn:
        work=queue.claim_milestone(conn)
        queue.finish_milestone(conn,work,None,nc.ClassificationError('invalid_response',retryable=False))
    assert service.detail(ident)['job']['status']=='failed' and service.detail(ident)['total']==1
    service.settings()['llm']['pi_chat']['model']=''
    service.retry(ident);assert worker.process_one('k','m')
    assert service.detail(ident)['job']['status']=='done'


@pytest.mark.parametrize("runtime", ["pi", "codex"])
def test_research_uses_global_agent_settings_with_job_snapshot_and_retry(env, runtime):
    _, service, _ = env
    settings = service.settings()
    settings["agent"]["runtime"] = runtime
    recorded = []
    original_runner = service.runner

    def select_model(name):
        if runtime == "pi":
            settings["llm"]["pi_chat"].update(provider="global-provider", model=name)
        else:
            settings["agent"]["codex"].update(provider="global-provider", model=name,
                profile="global-profile", reasoning_effort="high")

    def runner(config, *args, **kwargs):
        recorded.append(copy.deepcopy(config))
        if len(recorded) == 2:
            raise RuntimeError("test-retry")
        return original_runner(config, *args, **kwargs)

    service.runner = runner
    select_model("model-first")
    topic = service.create({"query": "甲收购乙", "request_id": "global-model"})
    select_model("model-second")
    assert service.process_one()
    service.initialize(topic["id"], DRAFT)
    assert service.process_one()
    assert service.detail(topic["id"])["job"]["status"] == "failed"
    select_model("model-retry")
    service.retry(topic["id"])
    assert service.process_one()
    assert service.detail(topic["id"])["topic"]["status"] == "ready"
    for config, model in zip(recorded, ["model-first", "model-second", "model-retry"], strict=True):
        assert config["runtime"] == runtime
        if runtime == "pi":
            assert (config["pi_provider"], config["pi_model"]) == ("global-provider", model)
        else:
            assert config["codex"] == {"model": model, "provider": "global-provider",
                "profile": "global-profile", "reasoning_effort": "high", "sandbox": "read-only"}


def test_creation_boundary_no_backfill_and_idempotence(env):
    connect,service,calls=env
    with closing(connect()) as conn:article(conn,"old")
    first=service.create({"query":"甲收购乙","request_id":"unique"})
    duplicate=service.create({"query":"甲收购乙","request_id":"unique"})
    assert first["id"]==duplicate["id"]
    service.process_one()
    service.initialize(first["id"],DRAFT)
    service.initialize(first["id"],DRAFT)
    with closing(connect()) as conn:
        article(conn,"during-initialization")
        assert conn.execute("SELECT COUNT(*) FROM topic_checks").fetchone()[0]==0
    service.process_one()
    service.initialize(first["id"],DRAFT)
    detail=service.detail(first["id"])
    assert detail["items"]==[] and len(detail["nodes"])==1
    with closing(connect()) as conn:
        article(conn,"after-completion")
        assert [r[0] for r in conn.execute("SELECT item_id FROM topic_checks")]==["after-completion"]
        assert conn.execute("SELECT COUNT(*) FROM topic_jobs").fetchone()[0]==2
    assert all(web for _,web in calls)


def test_one_jev_call_classifies_and_matches_multiple_topics_using_only_title_summary(env,monkeypatch):
    connect,service,_=env
    ids=[ready(service,"topic-one"),ready(service,"topic-two")]
    with closing(connect()) as conn:
        article(conn,"new")
        conn.execute("INSERT INTO article_details(url,title,content,fetched_at,updated_at) VALUES (?,?,'SECRET_BODY','now','now')",
                     ("https://example.com/new","全文"))
        conn.commit()
    calls=fake_jev(monkeypatch)
    worker=nc.Service(connect,lambda:("fixture-key","fixture-model"),topic_queue=tt.MatchQueue())
    assert worker.process_one("fixture-key","fixture-model")
    assert len(calls)==1
    assert set(calls[0]["questions"])=={"topic","china_focus",*["tracked_"+i for i in ids]}
    assert calls[0]["state"]=={"title":"甲收购乙获批","summary":"监管机构已批准本次交易"}
    assert "SECRET_BODY" not in json.dumps(calls)
    for ident in ids:
        data=service.detail(ident)
        assert data["total"]==1
        assert len(data["items"][0]["topics"])==2
    with closing(connect()) as conn:
        assert conn.execute("SELECT category FROM item_classifications").fetchone()[0]=="business"
        assert conn.execute("SELECT COUNT(*) FROM item_state").fetchone()[0]==0


@pytest.mark.parametrize("enabled,unread_only",[(False,False),(True,True)])
def test_matching_is_independent_of_classification_switch_and_read_status(env,monkeypatch,enabled,unread_only):
    connect,service,_=env
    ident=ready(service)
    with closing(connect()) as conn:
        article(conn,"read-news")
        conn.execute("INSERT INTO item_state(item_id,read_at,updated_at) VALUES ('read-news','already-read','now')")
        conn.commit()
    calls=fake_jev(monkeypatch)
    worker=nc.Service(connect,lambda:("key","model"),topic_queue=tt.MatchQueue())
    worker.configure(1,enabled=enabled,unread_only=unread_only)
    assert worker.process_one("key","model")
    assert set(calls[0]["questions"])=={"tracked_"+ident}
    assert service.detail(ident)["total"]==1
    with closing(connect()) as conn:
        assert conn.execute("SELECT status FROM item_classifications").fetchone()[0]=="pending"
        assert conn.execute("SELECT read_at FROM item_state").fetchone()[0]=="already-read"


def test_uncertain_and_unrelated_are_not_added(env,monkeypatch):
    connect,service,_=env
    ids=[ready(service,"one"),ready(service,"two")]
    with closing(connect()) as conn:article(conn,"new")
    fake_jev(monkeypatch,{"tracked_"+ids[0]:"信息不足","tracked_"+ids[1]:"不属于"})
    nc.Service(connect,lambda:("key","model"),topic_queue=tt.MatchQueue()).process_one("key","model")
    assert all(service.detail(ident)["total"]==0 for ident in ids)


def test_matching_lease_fingerprint_and_retry(env):
    connect,service,_=env
    ready(service)
    queue=tt.MatchQueue()
    with closing(connect()) as conn:
        article(conn,"new")
        work=queue.claim(conn)
        assert queue.claim(conn) is None
        conn.execute("UPDATE items SET summary='改写的摘要' WHERE id='new'")
        tt.enqueue_checks(conn)
        conn.commit()
        queue.finish(conn,work,{work["topics"][0]["topic_id"]:{"choice":"属于","model":"fixture"}})
        assert conn.execute("SELECT status FROM topic_checks").fetchone()[0]=="pending"
        work=queue.claim(conn)
        queue.finish(conn,work,{},nc.ClassificationError("http_429",retryable=True,retry_after=60))
        row=conn.execute("SELECT * FROM topic_checks").fetchone()
        assert row["attempts"]==0 and row["status"]=="pending" and row["next_attempt_at"]>time.time()+55
        assert queue.claim(conn) is None


def test_full_reindex_does_not_reenroll_old_news(env,tmp_path):
    connect,service,_=env
    daily=tmp_path/"daily";daily.mkdir()
    path=daily/"dailyFreshNews_2026-10-24.md"
    path.write_text("## Reuters · World（1条）\n### [收购](https://example.com/a)\n- 发布时间：2026-10-24 12:00:00\n")
    with closing(connect()) as conn:reindex(conn,daily)
    ready(service)
    with closing(connect()) as conn:
        assert reindex(conn,daily,full=True).topics_queued==0
        assert conn.execute("SELECT COUNT(*) FROM topic_checks").fetchone()[0]==0
    path.write_text(path.read_text()+"\n### [获批](https://example.com/b)\n- 发布时间：2026-10-24 13:00:00\n")
    with closing(connect()) as conn:
        assert reindex(conn,daily).topics_queued==1
        assert reindex(conn,daily,full=True).topics_queued==0
        assert conn.execute("SELECT COUNT(*) FROM topic_checks").fetchone()[0]==1


def test_legacy_monthly_timeline_has_no_search_and_updates_month_in_place(env,monkeypatch):
    mode="monthly"
    connect,service,calls=env
    ident=ready(service,mode=mode)
    fake_jev(monkeypatch)
    worker=nc.Service(connect,lambda:("key","model"),topic_queue=tt.MatchQueue())
    for n in range(2):
        with closing(connect()) as conn:article(conn,"new"+str(n))
        worker.process_one("key","model")
        with closing(connect()) as conn,conn:
            conn.execute("UPDATE topic_checks SET updated_at=?",(time.time()-10,))
        assert service.process_one()
    data=service.detail(ident)
    assert data["topic"]["overview"]=="交易取得新进展"
    assert len(data["nodes"])==(2 if mode=="monthly" else 3)
    if mode=="monthly":
        node=next(n for n in data["nodes"] if n["kind"]=="month")
        assert service.detail(ident,node_id=node["id"])["total"]==2
    assert [web for _,web in calls]==[True,True,False,False]
    with closing(connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM topic_checks WHERE processed=0 AND matched=1").fetchone()[0]==0


def test_invalid_history_never_activates_or_partially_saves(env):
    connect,service,_=env
    topic=service.create({"query":"事件","request_id":"one"})
    service.process_one();service.initialize(topic["id"],DRAFT)
    def invalid(config,prompt,cancel,diagnostics,*,web):
        diagnostics.append({"research_tools":1})
        return tt.js({"overview":"不能保存","sources":SOURCE,"nodes":[
            {"date":"2026-01-02","title":"有效","body":"有效","sources":SOURCE},
            {"date":"2099-01-01","title":"未来","body":"无效","sources":SOURCE}]})
    service.runner=invalid
    service.process_one()
    detail=service.detail(topic["id"])
    assert detail["job"]["status"]=="failed"
    assert detail["topic"]["status"]=="initializing" and detail["topic"]["cutoff_seq"] is None
    assert detail["nodes"]==[]


def test_historical_research_requires_tool_evidence(env):
    _,service,_=env
    topic=service.create({"query":"事件","request_id":"one"})
    service.runner=lambda *args,**kwargs:tt.js({**DRAFT,"sources":SOURCE})
    service.process_one()
    assert service.detail(topic["id"])["job"]["error"]=="topic_search_required"


def test_schema_migration_preserves_legacy_data(env):
    connect,_,_=env
    with closing(connect()) as conn:
        conn.execute("""INSERT INTO tracked_topics(title,description,keywords_json,exclude_keywords_json,scope,active,created_at,updated_at)
            VALUES ('旧主题','描述','[]','[]','all',1,'now','now')""")
        conn.commit()
        apply_schema(conn,ROOT/"schema.sql")
        assert conn.execute("SELECT title FROM tracked_topics").fetchone()[0]=="旧主题"


def test_request_identity_rejects_changed_query(env):
    _,service,_=env
    service.create({'query':'第一个事件','request_id':'same'})
    with pytest.raises(ValueError,match='topic_invalid_request'):
        service.create({'query':'另一个事件','request_id':'same'})
    assert len(service.listing())==1


def test_read_new_ingestion_is_not_starved_by_category_backlog(env,monkeypatch):
    connect,service,_=env
    with closing(connect()) as conn:article(conn,'old-unread')
    ident=ready(service)
    with closing(connect()) as conn:
        article(conn,'new-read')
        conn.execute("INSERT INTO item_state(item_id,read_at,updated_at) VALUES ('new-read','now','now')")
        conn.commit()
    calls=fake_jev(monkeypatch)
    worker=nc.Service(connect,lambda:('key','model'),topic_queue=tt.MatchQueue())
    worker.configure(1,unread_only=True)
    assert worker.process_one('key','model')
    assert len(calls)==1 and set(calls[0]['questions'])=={'tracked_'+ident}
    assert service.detail(ident)['total']==1
    with closing(connect()) as conn:
        assert conn.execute("SELECT status FROM item_classifications WHERE item_id='old-unread'").fetchone()[0]=='pending'


def test_late_month_preserves_context_outside_recent_nodes(env,monkeypatch):
    connect,service,calls=env
    ident=ready(service,mode='monthly')
    with closing(connect()) as conn:
        conn.execute("INSERT INTO topic_nodes(id,topic_id,kind,date,title,body,updated_at) VALUES ('old-month',?,'month','2026-01-01','旧月总结','保留这段历史','now')",(ident,))
        for n in range(85):
            conn.execute("INSERT INTO topic_nodes(id,topic_id,kind,date,title,body,updated_at) VALUES (?,?,'history','2026-05-01','节点','历史','now')",(str(n),ident))
        article(conn,'late',published='2026-01-23 12:00:00')
    fake_jev(monkeypatch)
    nc.Service(connect,lambda:('k','m'),topic_queue=tt.MatchQueue()).process_one('k','m')
    with closing(connect()) as conn,conn:conn.execute('UPDATE topic_checks SET updated_at=0')
    def update(config,prompt,cancel,diagnostics,*,web):
        assert not web and '保留这段历史' in prompt and 'old-month' in prompt
        return tt.js({'overview':'合并后','nodes':[{'id':'old-month','date':'2026-01-01','title':'一月总结',
            'body':'保留这段历史并加入新报道','item_ids':['late']}]})
    service.runner=update
    service.process_one()
    detail=service.detail(ident)
    assert detail['job']['status']=='done'
    assert next(n for n in detail['nodes'] if n['id']=='old-month')['body']=='保留这段历史并加入新报道'


def test_changed_source_rejects_entire_synthesis(env,monkeypatch):
    connect,service,_=env
    ident=ready(service,mode="monthly")
    with closing(connect()) as conn:article(conn,'changed')
    fake_jev(monkeypatch)
    nc.Service(connect,lambda:('k','m'),topic_queue=tt.MatchQueue()).process_one('k','m')
    with closing(connect()) as conn,conn:conn.execute('UPDATE topic_checks SET updated_at=0')
    def update(config,prompt,cancel,diagnostics,*,web):
        with closing(connect()) as conn,conn:
            conn.execute("UPDATE items SET summary='更正' WHERE id='changed'")
            tt.enqueue_checks(conn)
        return tt.js({'overview':'应拒绝','nodes':[]})
    service.runner=update
    service.process_one()
    detail=service.detail(ident)
    assert detail['job']['error']=='topic_source_changed'
    assert detail['topic']['overview']=='交易正在审查'


def test_deleted_topic_cannot_be_resurrected_by_inflight_result(env):
    connect,service,_=env
    topic=service.create({'query':'交易','request_id':'deleted'})
    def run(config,prompt,cancel,diagnostics,*,web):
        with closing(connect()) as conn,conn:
            conn.execute('DELETE FROM topic_jobs')
            conn.execute('DELETE FROM event_topics')
        diagnostics.append({'research_tools':1})
        return tt.js({**DRAFT,'sources':SOURCE})
    service.runner=run
    assert service.process_one()
    assert service.listing()==[]


def refinement_data(env, mode='events'):
    connect,service,_=env
    ident=ready(service,mode=mode)
    first=service.detail(ident)['nodes'][0]['id']
    with closing(connect()) as conn,conn:
        conn.execute("INSERT INTO topic_nodes(id,topic_id,kind,date,title,body,sources_json,updated_at) VALUES ('second',?,'event','2026-02-01','审查表态','正在推进审查',?,'now')",(ident,tt.js(SOURCE)))
        article(conn,'report-one');article(conn,'report-two')
        queue=tt.MatchQueue()
        for _ in range(2):
            work=queue.claim(conn)
            queue.finish(conn,work,{ident:{'choice':'属于','model':'test'}})
        conn.execute("UPDATE topic_checks SET processed=1")
        conn.executemany('INSERT INTO topic_node_items(node_id,item_id) VALUES (?,?)',[(first,'report-one'),('second','report-two')])
        conn.execute("INSERT INTO item_state(item_id,read_at,updated_at) VALUES ('report-one','now','now')")
        if mode=='monthly':
            conn.execute("INSERT INTO topic_nodes(id,topic_id,kind,date,title,body,updated_at) VALUES ('month',?,'month','2026-02-01','月度回顾','保留本月摘要','now')",(ident,))
            conn.execute("INSERT INTO topic_node_items(node_id,item_id) VALUES ('month','report-two')")
    return ident,first


@pytest.mark.parametrize('completed,available',[
    ('2026-01-31T10:20:00','2026-02-28T10:20:00'),
    ('2028-01-31T10:20:00','2028-02-29T10:20:00'),
    ('2026-12-31T10:20:00','2027-01-31T10:20:00'),
    ('2026-10-26T10:20:00','2026-11-26T10:20:00'),
])
def test_refinement_cooldown_is_one_calendar_month_from_success(env,monkeypatch,completed,available):
    connect,service,_=env
    clock=[completed]
    monkeypatch.setattr(tt,'now',lambda:clock[0])
    ident,_=refinement_data(env)
    assert service.detail(ident)['topic']['last_refined_at'] is None
    service.runner=lambda config,prompt,*a,**kw:tt.js({'nodes':[
        {'date':n['date'],'title':n['title'],'body':n['body'],'node_ids':[n['id']]}
        for n in json.loads(prompt.split('\n已有节点：')[1])]})
    service.refine(ident)
    assert service.process_one()
    preview=service.detail(ident)
    assert preview['job']['status']=='preview'
    assert preview['topic']['last_refined_at'] is None
    service.change_refinement(ident,preview['refinement']['preview']['id'],'apply')
    result=service.detail(ident)
    assert len(result['nodes'])==2
    assert result['topic']['last_refined_at']==completed
    assert result['topic']['refine_available_at']==available
    assert result['topic']['refine_cooldown'] is True
    with pytest.raises(ValueError,match='topic_refine_cooldown'):service.refine(ident)
    service.set_archived(ident,True);service.set_archived(ident,False)
    clock[0]=(datetime.fromisoformat(available)-timedelta(seconds=1)).isoformat()
    assert service.detail(ident)['topic']['refine_cooldown'] is True
    with pytest.raises(ValueError,match='topic_refine_cooldown'):service.refine(ident)
    with closing(connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM topic_jobs WHERE topic_id=? AND kind='refine'",(ident,)).fetchone()[0]==1
    clock[0]=available
    assert service.detail(ident)['topic']['refine_cooldown'] is False
    service.refine(ident);assert service.process_one()
    service.change_refinement(ident,service.detail(ident)['refinement']['preview']['id'],'apply')
    assert service.detail(ident)['topic']['last_refined_at']==available
    assert service.detail(ident)['topic']['refine_cooldown'] is True


@pytest.mark.parametrize('mode',['events','monthly'])
def test_node_news_counts_match_related_reports_independent_of_filter_and_page(env,mode):
    connect,service,_=env
    ident,first=refinement_data(env,mode)
    before=service.detail(ident,limit=1)
    assert before['topic']['news_count']==2
    assert next(n for n in before['nodes'] if n['id']==first)['news_count']==1
    for node in before['nodes']:
        filtered=service.detail(ident,node_id=node['id'],limit=1)
        assert node['news_count']==filtered['total']
        assert filtered['nodes']==before['nodes']
    # A saved link alone does not make a report part of the topic.
    with closing(connect()) as conn,conn:
        conn.execute("UPDATE topic_checks SET matched=0 WHERE topic_id=? AND item_id='report-one'",(ident,))
    after=service.detail(ident)
    assert next(n for n in after['nodes'] if n['id']==first)['news_count']==0
    assert service.detail(ident,node_id=first)['total']==0


def test_topic_news_uses_live_reading_state_without_changing_membership(env):
    connect,service,_=env
    ident,_=refinement_data(env,'events')
    before=service.detail(ident)
    with closing(connect()) as conn,conn:
        conn.execute("""UPDATE item_state SET read_at='read',read_later_at='queued',read_later_done_at=NULL
            WHERE item_id='report-one'""")
    after=service.detail(ident)
    item=next(i for i in after['items'] if i['id']=='report-one')
    assert item['read_at']=='read' and item['read_later_at']=='queued'
    assert item['read_later_done_at'] is None
    assert after['total']==before['total'] and after['nodes']==before['nodes']
    with closing(connect()) as conn,conn:
        conn.execute("UPDATE item_state SET read_later_at=NULL,read_later_done_at='done' WHERE item_id='report-one'")
    item=next(i for i in service.detail(ident)['items'] if i['id']=='report-one')
    assert item['read_later_at'] is None and item['read_later_done_at']=='done'
    assert item['read_at']=='read'


@pytest.mark.parametrize('mode',['events','monthly'])
def test_refinement_preserves_sources_news_links_and_monthly_nodes(env,mode):
    connect,service,calls=env
    ident,first=refinement_data(env,mode)
    before=service.detail(ident)
    def run(config,prompt,cancel,diagnostics,*,web):
        assert not web and '精简现有事件脉络' in prompt
        assert '月度回顾' not in prompt
        return tt.js({'nodes':[{'date':'2026-01-01','title':'公布交易并进入监管审查',
                               'body':'双方公布交易，随后进入监管审查。','node_ids':[first,'second']}]})
    service.runner=run
    service.refine(ident)
    assert service.process_one()
    preview=service.detail(ident)
    assert preview['nodes']==before['nodes']
    assert preview['job']['status']=='preview'
    service.change_refinement(ident,preview['refinement']['preview']['id'],'apply')
    after=service.detail(ident)
    assert after['job']['status']=='done'
    assert after['items']==before['items']
    phases=[n for n in after['nodes'] if n['kind']!='month']
    assert len(phases)==1 and phases[0]['title']=='公布交易并进入监管审查'
    assert service.detail(ident,node_id=phases[0]['id'])['total']==2
    assert [n for n in after['nodes'] if n['kind']=='month']==[n for n in before['nodes'] if n['kind']=='month']
    with closing(connect()) as conn:
        assert json.loads(conn.execute('SELECT sources_json FROM topic_nodes WHERE id=?',(phases[0]['id'],)).fetchone()[0])==SOURCE
        assert conn.execute("SELECT read_at FROM item_state WHERE item_id='report-one'").fetchone()[0]=='now'


@pytest.mark.parametrize('failure',['missing','duplicate','unknown','date','oversize'])
def test_invalid_refinement_keeps_existing_timeline_atomically(env,failure):
    connect,service,_=env
    ident,first=refinement_data(env)
    before=service.detail(ident)
    node={'date':'2026-02-01','title':'进入审查阶段','body':'交易进入审查阶段。','node_ids':[first,'second']}
    if failure=='missing':node['node_ids']=['second']
    elif failure=='duplicate':node['node_ids'].append(first)
    elif failure=='unknown':node['node_ids'].append('other-topic-node')
    elif failure=='date':node['date']='2026-03-01'
    elif failure=='oversize':node['body']='细节'*400
    service.runner=lambda *a,**k:tt.js({'nodes':[node]})
    service.refine(ident);service.process_one()
    after=service.detail(ident)
    assert after['job']['status']=='failed'
    assert after['nodes']==before['nodes'] and after['items']==before['items']
    assert after['topic']['last_refined_at'] is None
    assert after['topic']['refine_cooldown'] is False
    assert service.detail(ident,node_id=first)['total']==1


def test_archive_during_refinement_cannot_replace_nodes(env):
    connect,service,_=env
    ident,first=refinement_data(env)
    before=service.detail(ident)
    def run(config,prompt,cancel,diagnostics,*,web):
        service.set_archived(ident,True)
        assert cancel()
        return tt.js({'nodes':[{'date':'2026-01-01','title':'合并','body':'不应写入','node_ids':[first,'second']}]})
    service.runner=run
    service.refine(ident);service.process_one()
    after=service.detail(ident)
    assert after['job']['status']=='cancelled'
    assert after['nodes']==before['nodes']


def test_non_milestone_news_remains_in_vertical_feed(env,monkeypatch):
    connect,service,_=env
    ident,first=refinement_data(env)
    before=service.detail(ident)
    with closing(connect()) as conn,conn:conn.execute('UPDATE topic_checks SET processed=0,updated_at=0')
    fake_jev(monkeypatch, {'milestone':'不收录'})
    service.process_one()
    worker=nc.Service(connect,lambda:('k','m'),topic_queue=tt.MatchQueue())
    while worker.process_one('k','m'):pass
    after=service.detail(ident)
    assert after['job']['status']=='done'
    assert after['nodes']==before['nodes'] and after['total']==2
    with closing(connect()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM topic_checks WHERE processed=0').fetchone()[0]==0


def test_failed_refinement_stays_visible_until_retry_before_incremental_work(env,monkeypatch):
    connect,service,_=env
    ident,first=refinement_data(env)
    service.runner=lambda *a,**k:tt.js({'nodes':[]})
    service.refine(ident);service.process_one()
    with closing(connect()) as conn,conn:conn.execute('UPDATE topic_checks SET processed=0,updated_at=0')
    assert not service.process_one()
    assert service.detail(ident)['job']['kind']=='refine'
    with pytest.raises(ValueError,match='topic_retry_required'):service.refine(ident)
    service.runner=lambda *a,**k:tt.js({'nodes':[{'date':'2026-01-01','title':'进入审查','body':'阶段里程碑','node_ids':[first,'second']}]})
    service.retry(ident);assert service.process_one()
    fake_jev(monkeypatch, {'milestone':'不收录'})
    service.process_one()
    worker=nc.Service(connect,lambda:('k','m'),topic_queue=tt.MatchQueue())
    while worker.process_one('k','m'):pass
    assert service.detail(ident)['job']['kind']=='milestone'
    assert service.detail(ident)['job']['status']=='done'


def test_overly_detailed_initial_history_is_rejected(env):
    connect,service,_=env
    ident=service.create({'query':'交易','request_id':'dense'})['id']
    service.process_one();service.initialize(ident,DRAFT)
    count=13
    before=service.detail(ident)
    def run(config,prompt,cancel,diagnostics,*,web):
        if web:diagnostics.append({'research_tools':1})
        return tt.js({'overview':'拆分太细','sources':SOURCE,'nodes':[
            {'date':'2026-02-01','title':f'局部细节{i}','body':'碎片进展','sources':SOURCE,'item_ids':['report-one']} for i in range(count)]})
    service.runner=run;assert service.process_one()
    after=service.detail(ident)
    assert after['job']['error']=='topic_invalid_result'
    assert after['nodes']==before['nodes']


from test_news_classification import api

def test_definition_revision_api_validation_and_conflict(env,api,monkeypatch):
    _,service,_=env;module,client=api
    monkeypatch.setattr(module,'TOPIC_SERVICE',service)
    ident=ready(service);body=edit_body(service,ident,definition='调整后的定义')
    url='/api/topics/'+ident+'/definition'
    assert client.patch('/api/topics/missing/definition',json=body).status_code==404
    assert client.patch(url,json=[]).status_code==400
    assert client.patch(url,json={**body,'horizontal_rule':''}).status_code==400
    response=client.patch(url,json=body)
    assert response.status_code==200 and response.json['topic']['definition']=='调整后的定义'
    assert client.patch(url,json=body).status_code==409
    service.set_archived(ident,True)
    assert client.patch(url,json=edit_body(service,ident,definition='恢复后使用')).status_code==200


def test_migration_preserves_legacy_membership_and_converts_only_pending_event_work(env):
    connect,service,_=env;ident=ready(service)
    with closing(connect()) as conn,conn:
        article(conn,'news');queue=tt.MatchQueue();work=queue.claim(conn)
        queue.finish(conn,work,{ident:{'choice':'属于','model':'test'}})
        service.add_job(conn,service.detail(ident)['topic'],'update')
    before=service.detail(ident)
    with closing(connect()) as conn,conn:
        for table,fields in [('event_topics',['horizontal_rule','rules_revision']),('topic_checks',['rules_json']),('topic_jobs',['attempts','next_attempt_at'])]:
            for field in fields:conn.execute(f'ALTER TABLE {table} DROP COLUMN {field}')
        apply_schema(conn,ROOT/'schema.sql');apply_schema(conn,ROOT/'schema.sql')
    after=service.detail(ident)
    assert after['items']==before['items'] and after['nodes']==before['nodes']
    assert after['topic']['horizontal_rule']==tt.DEFAULT_HORIZONTAL_RULE
    assert after['job']['kind']=='milestone' and after['job']['status']=='pending'

def test_refinement_api_state_and_concurrency_validation(env,api,monkeypatch):
    connect,service,_=env
    module,client=api
    monkeypatch.setattr(module,'TOPIC_SERVICE',service)
    ident,first=refinement_data(env)
    url='/api/topics/'+ident+'/refine'
    assert client.post('/api/topics/missing/refine').status_code==404
    service.set_archived(ident,True)
    assert client.post(url).status_code==409
    service.set_archived(ident,False)
    assert client.post(url).status_code==202
    assert client.post(url).status_code==409

def test_archive_freezes_matches_and_restore_only_tracks_future_ingestions(env,monkeypatch):
    connect,service,_=env
    ident=ready(service)
    with closing(connect()) as conn:article(conn,'saved')
    fake_jev(monkeypatch)
    worker=nc.Service(connect,lambda:('key','model'),topic_queue=tt.MatchQueue())
    assert worker.process_one('key','model')
    with closing(connect()) as conn,conn:
        conn.execute("INSERT INTO item_state(item_id,read_at,updated_at) VALUES ('saved','now','now')")
        article(conn,'inflight')
    queue=tt.MatchQueue()
    with closing(connect()) as conn:work=queue.claim(conn)
    before=service.detail(ident)
    service.set_archived(ident,True)
    with closing(connect()) as conn:
        article(conn,'during-archive')
        queue.finish(conn,work,{ident:{'choice':'属于','model':'test'}})
        assert queue.claim(conn) is None
        assert conn.execute("SELECT status FROM topic_checks WHERE item_id='inflight'").fetchone()[0]=='cancelled'
        assert conn.execute("SELECT COUNT(*) FROM topic_checks WHERE item_id='during-archive'").fetchone()[0]==0
        assert conn.execute("SELECT read_at FROM item_state WHERE item_id='saved'").fetchone()[0]=='now'
    archived=service.detail(ident)
    assert archived['topic']['archived_at']
    assert archived['items']==before['items'] and archived['nodes']==before['nodes']
    assert not service.process_one()
    service.set_archived(ident,False)
    with closing(connect()) as conn:
        # Index refresh and changed old summaries still cannot backfill the gap.
        conn.execute("UPDATE items SET summary='补充摘要' WHERE id='saved'")
        assert tt.enqueue_checks(conn)==0
        article(conn,'after-restore')
    # A repeated restore must not move the activation boundary past new arrivals.
    cutoff=service.detail(ident)['topic']['cutoff_seq']
    service.set_archived(ident,False)
    assert service.detail(ident)['topic']['cutoff_seq']==cutoff
    with closing(connect()) as conn:
        next_work=queue.claim(conn)
        assert next_work['item']['id']=='after-restore'
        queue.finish(conn,next_work,{ident:{'choice':'属于','model':'test'}})
    assert {i['id'] for i in service.detail(ident)['items']}=={'saved','after-restore'}


def test_archiving_one_topic_does_not_interrupt_other_memberships(env):
    connect,service,_=env
    ids=[ready(service,'one'),ready(service,'two')]
    with closing(connect()) as conn:article(conn,'shared')
    queue=tt.MatchQueue()
    with closing(connect()) as conn:work=queue.claim(conn)
    service.set_archived(ids[0],True)
    service.set_archived(ids[0],False)
    with closing(connect()) as conn:
        queue.finish(conn,work,{ident:{'choice':'属于','model':'test'} for ident in ids})
    assert service.detail(ids[0])['total']==0
    assert service.detail(ids[1])['total']==1


def test_archive_rejects_inflight_synthesis(env,monkeypatch):
    connect,service,_=env
    ident=ready(service,mode="monthly")
    with closing(connect()) as conn:article(conn,'pending-synthesis')
    fake_jev(monkeypatch)
    nc.Service(connect,lambda:('k','m'),topic_queue=tt.MatchQueue()).process_one('k','m')
    with closing(connect()) as conn,conn:conn.execute('UPDATE topic_checks SET updated_at=0')
    before=service.detail(ident)
    def update(config,prompt,cancel,diagnostics,*,web):
        service.set_archived(ident,True)
        assert cancel()
        return tt.js({'overview':'不应覆盖','nodes':[]})
    service.runner=update
    assert service.process_one()
    after=service.detail(ident)
    assert after['job']['status']=='cancelled'
    assert after['topic']['overview']==before['topic']['overview']
    assert after['nodes']==before['nodes'] and after['items']==before['items']


def test_archive_api_validation_idempotence_and_active_limit(env,api,monkeypatch):
    connect,service,_=env
    module,client=api
    monkeypatch.setattr(module,'TOPIC_SERVICE',service)
    monkeypatch.setattr(tt,'MAX_TOPICS',1)
    ident=ready(service)
    url='/api/topics/'+ident+'/archive'
    for body in ([],{}, {'archived':'false'}, {'archived':1}):
        assert client.post(url,json=body).status_code==400
    assert client.post('/api/topics/missing/archive',json={'archived':True}).status_code==404
    assert client.post(url,json={'archived':True}).status_code==200
    revision=service.detail(ident)['topic']['revision']
    assert client.post(url,json={'archived':True}).status_code==200
    assert service.detail(ident)['topic']['revision']==revision
    assert client.post('/api/topics/'+ident+'/retry').status_code==409
    second=service.create({'query':'第二主题','request_id':'second'})['id']
    assert client.post('/api/topics/'+second+'/archive',json={'archived':True}).status_code==409
    assert client.post(url,json={'archived':False}).status_code==400
    assert service.detail(ident)['topic']['archived_at']
    with closing(connect()) as conn,conn:conn.execute('DELETE FROM event_topics WHERE id=?',(second,))
    assert client.post(url,json={'archived':False}).status_code==200
    assert service.detail(ident)['topic']['archived_at'] is None


def test_existing_topic_database_gets_archive_column(tmp_path):
    with closing(sqlite3.connect(tmp_path/'old-topics.sqlite3')) as conn:
        conn.row_factory=sqlite3.Row
        conn.executescript(tt.SCHEMA.replace(', archived_at TEXT',''))
        conn.execute("INSERT INTO event_topics(id,request_id,query,name,created_at,updated_at) VALUES ('existing','one','事件','事件','now','now')")
        conn.commit()
        apply_schema(conn,ROOT/'schema.sql')
        row=conn.execute("SELECT * FROM event_topics WHERE id='existing'").fetchone()
        assert row['name']=='事件' and row['archived_at'] is None
        apply_schema(conn,ROOT/'schema.sql')
        assert conn.execute('SELECT COUNT(*) FROM event_topics').fetchone()[0]==1


def test_topic_api_validation_and_delete_preserve_news(env,api,monkeypatch):
    connect,service,_=env
    module,client=api
    monkeypatch.setattr(module,'TOPIC_SERVICE',service)
    monkeypatch.setattr(module,'db_conn',connect)
    assert client.post('/api/topics',json=[]).status_code==400
    assert client.post('/api/topics',json={'query':'','request_id':'empty'}).status_code==400
    assert client.get('/api/topics/missing').status_code==404
    ident=ready(service)
    assert client.get('/api/topics/'+ident+'?offset=-1').status_code==400
    assert client.get('/api/topics/'+ident+'?limit=10000').status_code==400
    assert client.get('/api/topics/'+ident+'?node=missing').status_code==400
    with closing(connect()) as conn:article(conn,'untouched')
    response=client.get('/api/topics/'+ident)
    assert response.status_code==200 and response.json['items']==[]
    assert client.post('/api/topics/'+ident+'/seen',json={'observed_at':response.json['observed_at']}).status_code==200
    assert client.post('/api/topics/'+ident+'/seen',json={'observed_at':float('inf')}).status_code==400
    assert client.delete('/api/topics/'+ident).status_code==200
    assert client.get('/api/topics/'+ident).status_code==404
    with closing(connect()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM items').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM topic_checks').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM topic_ingestions').fetchone()[0]==1


@pytest.mark.parametrize('runtime,tool,success',[('codex','web_search',True),('codex','command_execution',False),
                                               ('pi','mcp__tavily__web_search',True),('pi','bash',False)])
def test_research_requires_web_tool_and_reports_it(tmp_path,monkeypatch,runtime,tool,success):
    import sys
    if runtime=='codex':
        events=[{'type':'thread.started','thread_id':'11111111-1111-4111-8111-111111111111'},
                {'type':'turn.started'}, {'type':'item.completed','item':{'id':'tool','type':tool}},
                {'type':'item.completed','item':{'id':'answer','type':'agent_message','text':'answer'}},
                {'type':'turn.completed'}]
    else:
        events=[{'type':'oar_tool','tool':tool},{'type':'oar_result','outcome':'completed','answer':'answer'}]
    path=tmp_path/'model.py'
    path.write_text('import sys,json\nsys.stdin.read()\nfor event in '+repr(events)+': print(json.dumps(event),flush=True)')
    monkeypatch.setattr(news_summaries.codex_client,'require_ready',lambda:None)
    monkeypatch.setattr(news_summaries.codex_client,'command',lambda *a,**k:[sys.executable,str(path)])
    monkeypatch.setattr(news_summaries,'bridge_command',lambda *a:[sys.executable,str(path)])
    config={'runtime':runtime,'codex':{'model':'test','profile':'','sandbox':'read-only'},'pi_provider':'test','pi_model':'test'}
    diagnostics=[]
    if success:
        assert news_summaries.run_native(config,'test',lambda:False,diagnostics,allow_research=True)=='answer'
        assert diagnostics[0]['research_tools']==1
    else:
        with pytest.raises(RuntimeError):
            news_summaries.run_native(config,'test',lambda:False,diagnostics,allow_research=True)


def prepare_preview(env):
    _,service,_=env
    ident,first=refinement_data(env)
    before=service.detail(ident)
    service.runner=lambda *a,**k:tt.js({'nodes':[{'date':'2026-01-01','title':'合并阶段',
        'body':'交易进入审查','node_ids':[first,'second']}]})
    service.refine(ident);assert service.process_one()
    return ident,first,before,service.detail(ident)['refinement']['preview']['id']


def test_refinement_preview_is_durable_discardable_and_does_not_start_cooldown(env):
    connect,service,_=env
    ident,first,before,version=prepare_preview(env)
    after=service.detail(ident)
    assert after['nodes']==before['nodes'] and after['items']==before['items']
    assert not after['topic']['refine_cooldown']
    assert after['refinement']['preview']['groups'][0]['previous']==['公布收购','审查表态']
    reopened=tt.Service(connect,service.settings)
    assert reopened.detail(ident)['refinement']==after['refinement']
    with pytest.raises(ValueError,match='topic_preview_pending'):service.refine(ident)
    service.change_refinement(ident,version,'discard')
    service.change_refinement(ident,version,'discard')
    assert service.detail(ident)['refinement']['preview'] is None
    assert service.detail(ident)['nodes']==before['nodes']
    service.refine(ident)


def test_refinement_apply_starts_cooldown_at_confirmation_and_restore_keeps_it(env,monkeypatch):
    _,service,_=env
    monkeypatch.setattr(tt,'now',lambda:'2026-01-30T08:00:00')
    ident,first,before,version=prepare_preview(env)
    monkeypatch.setattr(tt,'now',lambda:'2026-02-02T09:00:00')
    service.change_refinement(ident,version,'apply')
    service.change_refinement(ident,version,'apply')
    assert service.detail(ident)['topic']['refine_available_at']=='2026-03-02T09:00:00'
    assert len(service.detail(ident)['nodes'])==1
    service.change_refinement(ident,version,'restore')
    service.change_refinement(ident,version,'restore')
    restored=service.detail(ident)
    assert restored['nodes']==before['nodes'] and restored['items']==before['items']
    assert restored['refinement']['restore'] is None
    assert restored['topic']['refine_cooldown']


@pytest.mark.parametrize('change',['node','link','archive'])
def test_refinement_rejects_stale_preview_without_losing_new_content(env,change):
    connect,service,_=env
    ident,first,before,version=prepare_preview(env)
    with closing(connect()) as conn,conn:
        if change=='node':conn.execute("UPDATE topic_nodes SET title='新标题' WHERE id=?",(first,))
        elif change=='link':conn.execute("INSERT INTO topic_node_items(node_id,item_id) VALUES (?,'new-report')",(first,))
    if change=='archive':service.set_archived(ident,True);service.set_archived(ident,False)
    updated=service.detail(ident)
    assert updated['refinement']['preview']['stale']
    with pytest.raises(ValueError,match='topic_preview_changed'):service.change_refinement(ident,version,'apply')
    assert service.detail(ident)['nodes']==updated['nodes']
    assert not service.detail(ident)['topic']['refine_cooldown']


def test_refinement_restore_preserves_later_nodes_and_reports(env):
    connect,service,_=env
    ident,first,before,version=prepare_preview(env)
    service.change_refinement(ident,version,'apply')
    merged=service.detail(ident)['nodes'][0]['id']
    with closing(connect()) as conn,conn:
        conn.execute("INSERT INTO topic_node_items(node_id,item_id) VALUES (?,'later-report')",(merged,))
        conn.execute("INSERT INTO topic_nodes(id,topic_id,kind,date,title,body,updated_at) VALUES ('later-node',?,'event','2026-10-27','后来发生','保留新进展','now')",(ident,))
        conn.execute("INSERT INTO topic_node_items(node_id,item_id) VALUES ('later-node','latest-report')")
    service.change_refinement(ident,version,'restore')
    with closing(connect()) as conn:
        pairs={tuple(row) for row in conn.execute('SELECT node_id,item_id FROM topic_node_items')}
        assert pairs=={(first,'report-one'),('second','report-two'),(merged,'later-report'),('later-node','latest-report')}
        assert conn.execute("SELECT title FROM topic_nodes WHERE id='later-node'").fetchone()[0]=='后来发生'
    assert service.detail(ident)['items']==before['items']


def test_refinement_version_api_is_scoped_and_atomic(env,api,monkeypatch):
    _,service,_=env
    module,client=api
    monkeypatch.setattr(module,'TOPIC_SERVICE',service)
    monkeypatch.setattr(module,'db_conn',service.connect)
    ident,_,before,version=prepare_preview(env)
    other=service.create({'query':'另一个主题','request_id':'another-topic'})['id']
    base='/api/topics/'+ident+'/refinements/'+version
    assert client.post('/api/topics/'+other+'/refinements/'+version+'/apply').status_code==409
    assert client.post(base+'/unknown').status_code==400
    assert client.post(base+'/apply').status_code==200
    assert client.post(base+'/restore').status_code==200
    assert service.detail(ident)['nodes']==before['nodes']
    assert client.delete('/api/topics/'+ident).status_code==200
    with closing(service.connect()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM topic_refinements WHERE topic_id=?',(ident,)).fetchone()[0]==0


def test_topic_update_badge_counts_topics_and_preserves_later_progress(env, api, monkeypatch):
    connect, service, _ = env
    module, client = api
    monkeypatch.setattr(module, 'TOPIC_SERVICE', service)
    def api_connect():
        conn = connect()
        conn.create_function('is_bloomberg_video_url', 1, lambda url: 0)
        return conn
    monkeypatch.setattr(module, 'db_conn', api_connect)
    first, second = ready(service), ready(service, request_id='second')
    summary = lambda: client.get('/api/nav-summary').json['summary']['topics_unseen']
    assert summary() == 2  # Initial history is a new progress update, even without news.
    snapshot = client.get('/api/topics/'+first).json
    assert snapshot['topic']['has_new_progress'] and snapshot['topic']['new_count'] == 0
    # A progress-only change after the read must survive acknowledgment of that read.
    with closing(connect()) as conn, conn:
        conn.execute('UPDATE topic_nodes SET updated_at=? WHERE topic_id=?', (tt.now(), first))
    client.post('/api/topics/'+first+'/seen', json={'observed_at':snapshot['observed_at']})
    assert summary() == 2
    fresh = client.get('/api/topics/'+first).json
    client.post('/api/topics/'+first+'/seen', json={'observed_at':fresh['observed_at']})
    assert summary() == 1
    # New reports restore the badge; two reports in one topic count as one topic.
    with closing(connect()) as conn:
        article(conn, 'badge-one')
        article(conn, 'badge-two')
        conn.execute("UPDATE topic_checks SET status='done',matched=1,updated_at=? WHERE topic_id=?", (time.time(), first))
        conn.commit()
    assert summary() == 2
    listing = {t['id']:t for t in service.listing()}
    assert listing[first]['new_count'] == 2 and not listing[first]['has_new_progress']
    assert listing[first]['has_updates']
    client.post('/api/topics/'+first+'/archive', json={'archived':True})
    assert summary() == 1
    client.delete('/api/topics/'+second)
    assert summary() == 0
    with closing(connect()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM item_state WHERE read_at IS NOT NULL').fetchone()[0] == 0
