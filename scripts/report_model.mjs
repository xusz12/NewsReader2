import os from 'node:os';
import path from 'node:path';

let dependenciesReady = false;
try {
  const {getSupportedThinkingLevels} = await import('@earendil-works/pi-ai');
  const {ModelRuntime} = await import(new URL('./core/model-runtime.js', import.meta.resolve('@earendil-works/pi-coding-agent')));
  dependenciesReady = true;
  let input = '';
  for await (const chunk of process.stdin) {
    input += chunk;
    if (input.length > 16 * 1024 * 1024) throw new Error();
  }
  const request = JSON.parse(input);
  if (!['catalog', 'metadata'].includes(request.action) ||
      (request.action === 'metadata' && (!request.provider || !request.model))) throw new Error();
  const source = process.env.NEWS_READER_PI_AGENT_DIR || process.env.PI_CODING_AGENT_DIR || path.join(os.homedir(), '.pi', 'agent');
  const models = await ModelRuntime.create({authPath:path.join(source,'auth.json'), modelsPath:path.join(source,'models.json'), allowModelNetwork:false});
  if (request.action === 'catalog') {
    const available = await models.getAvailable();
    process.stdout.write(JSON.stringify({error:'',notice:available.length ? '' : '暂无可用模型，请先登录 Pi 或配置 API 密钥。',options:available.map(model=>({value:model.id, provider:model.provider,
      label:model.id, cost:model.cost, reasoning_efforts:getSupportedThinkingLevels(model)}))})+'\n');
    process.exit(0);
  }
  const model = models.getModel(request.provider,request.model);
  if (!model) throw new Error();
  if (request.action === 'metadata') {
    process.stdout.write(JSON.stringify({contextWindow:model.contextWindow,maxTokens:model.maxTokens,reasoning_efforts:getSupportedThinkingLevels(model)})+'\n');
    process.exit(0);
  }
} catch {
  const error = dependenciesReady ? 'report_model_failed' : 'report_model_dependencies_missing';
  process.stdout.write(JSON.stringify({type:'oar_result',outcome:'failed',error})+'\n');
  process.exitCode = 1;
}
