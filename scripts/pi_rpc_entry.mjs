/** Pi startup policy only. OAR owns RPC, events, sessions and process cleanup. */
const mode = process.argv[2];
if (!['news_chat', 'chat_archive', 'material_summary', 'web_research', 'personal_assistant', 'report', 'translation', 'connection_test'].includes(mode)) throw new Error('oar_invalid_request');
const args = process.argv.slice(3);
if (['news_chat', 'web_research'].includes(mode)) {
  // OAR disables native mcp.json because its common MCP option supplies its own
  // servers. News Chat instead inherits Pi's full native config: filters,
  // exposure, authentication, codemode, and extension-registered servers.
  // Never combine this with OAR SessionOptions.mcpServers.
  process.argv = [...process.argv.slice(0, 2), ...args.filter(arg => arg !== '--no-mcp')];
  if (mode === 'web_research') process.argv.push('--no-session');
} else if (mode === 'report') {
  const {fileURLToPath} = await import('node:url');
  process.argv = [...process.argv.slice(0, 2), ...args, '--no-session', '--no-extensions', '--no-skills',
    '--no-prompt-templates', '--no-context-files', '--tools', 'mcp__newsreader_report__*',
    '--extension', fileURLToPath(new URL('./pi_report_extension.mjs', import.meta.url))];
} else if (mode === 'personal_assistant') {
  process.argv = [...process.argv.slice(0, 2), ...args, '--no-extensions', '--no-skills',
    '--no-prompt-templates', '--no-context-files', '--tools', 'mcp__nr_assistant__*'];
} else {
  if (['translation', 'connection_test'].includes(mode)) args.push('--no-extensions');
  process.argv = [...process.argv.slice(0, 2), ...args, '--no-session',
    '--no-tools', '--exclude-tools', '*', '--no-skills', '--no-prompt-templates', '--no-context-files'];
}
await import('@earendil-works/pi-coding-agent/rpc-entry');
