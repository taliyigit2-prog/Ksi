# KSI Local Studio MCP yapılandırması

Sunucu tamamen yerel stdio kullanır. Yapılandırmadan önce `ksi` komutunun mutlak yolunu
bulun. `KSI_MCP_ROOTS` içine yalnız aracın görmesine izin verdiğiniz kökleri, macOS'ta
iki nokta ile ayrılmış olarak yazın. Ağ varsayılan kapalıdır; URL işleri gerekiyorsa
değeri bilinçli olarak `1` yapın. Parola, çerez veya API anahtarını MCP ayarına koymayın.

Örnekler:

- [Codex](./codex.toml.example)
- [Claude Code](./claude.mcp.json.example)
- [Gemini CLI](./gemini.settings.json.example)
- [Genel MCP stdio](./generic.mcp.json.example)

Codex: <https://developers.openai.com/learn/docs-mcp>  
Claude Code: <https://docs.anthropic.com/en/docs/claude-code/mcp>  
Gemini CLI: <https://geminicli.com/docs/tools/mcp-server/>

