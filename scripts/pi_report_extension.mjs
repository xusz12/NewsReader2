export function limitPayload(payload, budget, api) {
  if (!payload || typeof payload !== 'object') throw new Error('report_model_failed');
  const result = {...payload};
  if ('max_completion_tokens' in result) result.max_completion_tokens = budget;
  else if ('max_tokens' in result || api === 'anthropic-messages' || api === 'openai-completions') result.max_tokens = budget;
  else if ('max_output_tokens' in result) result.max_output_tokens = budget;
  else if (api === 'openai-responses' || api === 'openai-codex-responses') return result;
  else if (api === 'pi-messages') result.options = {...result.options, maxTokens: budget};
  else if (api === 'mistral-conversations') result.maxTokens = budget;
  else if (api === 'google-generative-ai' || api === 'google-vertex') result.config = {...result.config, maxOutputTokens: budget};
  else if ('inferenceConfig' in result || /bedrock/u.test(api)) result.inferenceConfig = {...result.inferenceConfig, maxTokens: budget};
  else throw new Error('report_model_failed');
  if (result.thinking?.type === 'enabled' && result.thinking.budget_tokens >= budget) {
    result.thinking = {...result.thinking, budget_tokens: Math.max(1, budget - 1024)};
  }
  return result;
}

export default function reportExtension(pi) {
  const budget = Number(process.env.NR_REPORT_OUTPUT_TOKENS);
  if (!Number.isInteger(budget) || budget < 1 || budget > 1000000) throw new Error('oar_invalid_request');
  pi.on('before_provider_request', (event, ctx) => limitPayload(event.payload, Math.min(budget, ctx.model.maxTokens), ctx.model.api));
  pi.on('before_provider_headers', event => {
    if (process.env.NR_REPORT_SESSION_ID && 'x-opencode-session' in event.headers) {
      event.headers['x-opencode-session'] = process.env.NR_REPORT_SESSION_ID;
    }
  });
  pi.on('turn_end', async (event, ctx) => {
    if (event.message?.stopReason === 'length' || event.message?.usage?.output > budget) await ctx.abort();
  });
}
