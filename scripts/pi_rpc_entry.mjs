/** Pi startup policy only. OAR owns RPC, events, sessions and process cleanup. */
const mode = process.argv[2];
if (!['news_chat', 'chat_archive', 'material_summary', 'web_research', 'personal_assistant', 'report', 'translation', 'connection_test'].includes(mode)) throw new Error('oar_invalid_request');
const args = process.argv.slice(3);
if (['news_chat', 'personal_assistant', 'web_research'].includes(mode)) {
  const tools = mode === 'personal_assistant' ? 'mcp__nr_web__*,mcp__nr_assistant__*' : 'mcp__nr_web__*';
  process.argv = [...process.argv.slice(0, 2), ...args, '--no-extensions', '--no-skills',
    '--no-prompt-templates', '--no-context-files', '--tools', tools, ...(mode === 'web_research' ? ['--no-session'] : [])];
} else if (mode === 'report') {
  const {fileURLToPath} = await import('node:url');
  process.argv = [...process.argv.slice(0, 2), ...args, '--no-session', '--no-extensions', '--no-skills',
    '--no-prompt-templates', '--no-context-files', '--tools', 'mcp__newsreader_report__*',
    '--extension', fileURLToPath(new URL('./pi_report_extension.mjs', import.meta.url))];
} else {
  args.push('--no-extensions');
  process.argv = [...process.argv.slice(0, 2), ...args, '--no-session',
    '--no-tools', '--exclude-tools', '*', '--no-skills', '--no-prompt-templates', '--no-context-files'];
}
await import('@earendil-works/pi-coding-agent/rpc-entry');
