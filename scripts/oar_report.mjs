export function validateReport(request) {
  if (request.task !== 'report') return;
  if (request.resume || request.legacy_native_id || request.web_search === 'live' ||
      !Number.isInteger(request.output_tokens) || request.output_tokens < 1 || request.output_tokens > 1000000 ||
      typeof request.delegation !== 'boolean' || (request.delegation && !request.nr_tools) ||
      (request.report_session_id !== undefined && (typeof request.report_session_id !== 'string' ||
       !/^[A-Za-z0-9_-]{1,150}$/u.test(request.report_session_id))) ||
      (request.report_launch_args !== undefined && (!Array.isArray(request.report_launch_args) ||
       request.report_launch_args.some(arg => typeof arg !== 'string')))) throw new Error('oar_invalid_request');
}

export function reportEvents(session, request, emit) {
  if (request.task !== 'report') return {event() {}, metadata: () => ({})};
  let usage = {};
  session.rawEvents(record => {
    if (record.kind !== 'frame') return;
    const native = record.body.native;
    const item = native?.item;
    if (!['collabAgentToolCall', 'collabToolCall'].includes(item?.type)) return;
    if (!['item/started', 'item/completed'].includes(record.body.type)) return;
    emit({type: 'oar_report_collaboration', id: item.id, tool: item.tool,
      phase: record.body.type === 'item/completed' ? 'completed' : 'started',
      receiver_thread_ids: Array.isArray(item.receiverThreadIds) ? item.receiverThreadIds.filter(id => typeof id === 'string') : [],
      ...(typeof item.status === 'string' ? {status: item.status} : {})});
  });
  return {
    event(event) {
      if (event.kind === 'session_linked' && event.via === 'tool_call') {
        emit({type: 'oar_report_agent', id: event.child});
      }
      if (event.kind === 'usage' && !event.agentPath?.length && event.usage.tokens) {
        usage = Object.fromEntries(Object.entries(event.usage.tokens).filter(([, value]) =>
          typeof value === 'number' && Number.isFinite(value) && value >= 0));
      }
      if (event.kind === 'tool_call_started') {
        const tool = event.server && event.toolName ? `mcp__${event.server}__${event.toolName}` : event.tool;
        emit({type: 'oar_tool', tool});
      }
    },
    metadata: () => ({usage, usage_scope: 'oar_root_session'}),
  };
}

export function completedPiReport(records, sessionId, outputTokens = Infinity) {
  const messages = records.filter(record => record.kind === 'frame' && record.sessionId === sessionId &&
    !record.agentPath?.length && record.body.type === 'message_end' && record.body.native?.message?.role === 'assistant')
    .map(record => record.body.native.message);
  if (messages.some(message => message.stopReason === 'length' || message.usage?.output > outputTokens)) {
    throw new Error('report_output_truncated');
  }
  const last = messages.at(-1);
  if (!last || last.stopReason !== 'stop' || !Array.isArray(last.content) || last.content.some(part => part.type === 'toolCall')) {
    return '';
  }
  return last.content.filter(part => part.type === 'text').map(part => part.text).join('');
}
