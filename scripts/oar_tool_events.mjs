export function toolEvents(emit, completed = false) {
  const active = new Map();
  return event => {
    if (event.kind === 'tool_call_started') {
      if (completed && event.callId) active.set(event.callId, event.tool);
      emit({type: 'oar_tool', tool: event.tool});
    } else if (completed && event.kind === 'tool_call_ended' && active.has(event.callId)) {
      const tool = active.get(event.callId);
      active.delete(event.callId);
      emit({type: 'oar_tool', tool, phase: 'completed', success: event.result !== 'failed'});
    }
  };
}

export function classifiedFailure(failure) {
  return {quota: 'oar_quota', rate_limited: 'oar_rate_limited', input_too_large: 'oar_input_too_large',
    model_unavailable: 'oar_model_unavailable', overloaded: 'oar_overloaded'}[failure];
}
