import fs from 'node:fs';
import path from 'node:path';

export const name = 'newsreader-session-policy';
export const inject = ['tools', 'llm'];

async function outputLimit(llm, info) {
  if (Number.isSafeInteger(info.defaultMaxTokens) && info.defaultMaxTokens > 0) return info.defaultMaxTokens;
  try {
    const route = llm.listConfigurableProviders?.().find(entry => entry.provider === info.provider);
    if (route?.settingsNs !== 'llm-pi-ai' || route.declared !== false) return;
    const models = await llm.discoverModels?.(route.settingsNs, {provider: info.provider});
    return models?.find(model => model.id === info.id)?.maxTokens;
  } catch {
    return;
  }
}

export function apply(ctx, config) {
  ctx.on('agent/created', async ({agent}) => {
    if (config.route) {
      const {provider, model} = config.route;
      const info = await ctx.llm.resolveModelInfo(provider, model);
      fs.writeFileSync(config.metadata, JSON.stringify({
        contextWindow: info.context?.contextWindow,
        maxTokens: await outputLimit(ctx.llm, info),
        reasoning_efforts: info.reasoning?.efforts?.map(level => level.id) ?? [],
      }), {mode: 0o600});
    }
    const tools = agent.scope.ctx.tools;
    const allow = tools.definitions(agent).map(tool => tool.name)
      .filter(name => config.servers.some(server => name.startsWith('mcp__' + server + '__')));
    tools.restrict({allow});
    if (tools.definitions(agent).some(tool => !allow.includes(tool.name)) ||
        config.servers.some(server => !allow.some(name => name.startsWith('mcp__' + server + '__')))) {
      throw new Error('oar_dsh_incompatible');
    }
    fs.writeFileSync(path.join(config.home, 'policy-' + agent.session.id), 'ready', {mode: 0o600});
  });
}
