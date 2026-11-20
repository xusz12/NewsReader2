import json
import sys


class StdioTools:
    def respond(self, message):
        if not isinstance(message, dict) or "id" not in message:
            return None
        response = {"jsonrpc": "2.0", "id": message["id"]}
        method = message.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                      "serverInfo": {"name": self.name, "version": "1.0.0"}, "instructions": self.instructions}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": self.tools}
        elif method == "tools/call":
            try:
                params = message.get("params", {})
                value = self.call(params["name"], params.get("arguments", {}))
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}
            except self.errors as error:
                result = {"isError": True, "content": [{"type": "text", "text": str(error)[:500]}]}
        else:
            return response | {"error": {"code": -32601, "message": "Method not found"}}
        return response | {"result": result}

    def serve(self):
        for line in sys.stdin.buffer:
            try:
                if len(line) > 1_000_000:
                    raise ValueError()
                response = self.respond(json.loads(line))
            except (ValueError, UnicodeError):
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
