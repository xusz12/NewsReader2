/** Pi startup policy only. OAR owns RPC, events, sessions and process cleanup. */
const mode = process.argv[2];
if (!['news_chat', 'chat_archive'].includes(mode)) throw new Error('oar_invalid_request');
const args = process.argv.slice(3);
if (mode === 'news_chat') {
  // OAR disables native mcp.json because its common MCP option supplies its own
  // servers. News Chat instead inherits Pi's full native config: filters,
  // exposure, authentication, codemode, and extension-registered servers.
  // Never combine this with OAR SessionOptions.mcpServers.
  process.argv = [...process.argv.slice(0, 2), ...args.filter(arg => arg !== '--no-mcp')];
} else {
  process.argv = [...process.argv.slice(0, 2), ...args, '--no-session',
    '--no-tools', '--exclude-tools', '*', '--no-skills', '--no-prompt-templates', '--no-context-files'];
}
await import('@earendil-works/pi-coding-agent/rpc-entry');
