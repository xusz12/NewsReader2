export function assistantContext(request, runtime, installation, session) {
  if (request.task !== 'personal_assistant') return;
  const capabilities = runtime.sessionCapabilities?.(installation);
  if (capabilities?.manualCompaction?.kind !== 'supported' || !capabilities.contextUsage?.tokens ||
      !capabilities.contextUsage.contextWindow || typeof session.compact !== 'function') return;
  return {
    async prepare(resumed, emit, signal) {
      if (!resumed || !request.compact_before_turn) return;
      emit({type: 'oar_progress', phase: 'compacting'});
      try {
        const result = await session.compact({timeoutMs: 45000, signal});
        if (result.kind !== 'completed') throw new Error('assistant_compaction_failed');
      } catch {
        throw new Error('assistant_compaction_failed');
      } finally {
        emit({type: 'oar_progress', phase: 'thinking'});
      }
    },
    due() {
      const usage = session.contextUsage().value;
      return Number.isFinite(usage?.tokens) && Number.isFinite(usage.contextWindow) &&
        usage.contextWindow > 0 && usage.tokens >= 0.8 * usage.contextWindow;
    },
  };
}

export function decodeSessionMapping(data) {
  try {
    const mapping = JSON.parse(data);
    if (mapping && typeof mapping.id === 'string' && /^[a-zA-Z0-9_-]{1,200}$/.test(mapping.id)) return mapping;
  } catch {}
  throw new Error('oar_invalid_session');
}
