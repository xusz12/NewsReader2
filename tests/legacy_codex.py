import json

import codex_client


def command(config, *, cwd, live_search=False):
    config = codex_client.normalize_config(config)
    args = [codex_client.executable(), 'exec', '--cd', str(cwd), '--sandbox', 'read-only',
            '-c', 'approval_policy="never"', '-c', 'sandbox_mode="read-only"', '--json', '--color', 'never',
            '-c', 'service_tier="default"', '-c', 'features.fast_mode=false']
    if config.get('model'):
        args += ['--model', config['model']]
    if config.get('reasoning_effort'):
        args += ['-c', 'model_reasoning_effort=' + json.dumps(config['reasoning_effort'])]
    if live_search:
        args += ['-c', 'web_search="live"']
    return [*args, '-']


def result(stdout):
    records = [json.loads(line) for line in stdout.splitlines()]
    thread = next(row['thread_id'] for row in records if row.get('type') == 'thread.started')
    answer = [row['item']['text'] for row in records if row.get('type') == 'item.completed'
              and row.get('item', {}).get('type') == 'agent_message'
              and row['item'].get('phase') != 'commentary'][-1]
    assert any(row.get('type') == 'turn.completed' for row in records)
    return thread, answer
