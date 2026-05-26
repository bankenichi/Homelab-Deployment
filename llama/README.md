# Local LLM stack (external repos)

These components are **not vendored inside Homelab**. `Deploy-Homelab.ps1` clones and configures them on the host at install time.

## Repositories

| Repo | Role | Install location |
| --- | --- | --- |
| [llamacpp-turboquant-mtp-executables-for-cuda-12.8](https://github.com/bankenichi/llamacpp-turboquant-mtp-executables-for-cuda-12.8) | Prebuilt `llama-server.exe`, CUDA 12.8 binaries, `run-llama` wrappers, default `llama-args.txt` | `$env:LLAMACPP_ROOT` (default `C:\Program Files\llamacpp`) |
| [llama-config-ui](https://github.com/bankenichi/llama-config-ui) | Web UI to edit llama.cpp launch flags, save profiles, write `llama-args.txt` | Submodule under `$env:LLAMACPP_ROOT\llama-config-ui` |

The executables repo declares `llama-config-ui` as a git submodule. The bootstrap script runs `git submodule init` / `update` after every clone or pull, then adds both the install root and `llama-config-ui` to the system `PATH`. The install root is `$env:LLAMACPP_ROOT` if pre-set at User or Machine scope before deploy, otherwise `C:\Program Files\llamacpp`; either way the script persists the resolved value to machine scope so downstream tools and re-runs can find it. See the main README "Optional: Installing llama.cpp to a different drive" section for the exact pre-set command.

## Runtime layout

```
Deploy-Homelab.ps1
        │
        ├─ git clone/pull → %LLAMACPP_ROOT%             (default: C:\Program Files\llamacpp)
        ├─ submodule     → …\llama-config-ui          (config WebUI; added to PATH)
        ├─ huggingface-cli → *.gguf + mmproj.gguf     (into install dir)
        └─ writes run-llama.cmd / run-llama.ps1 / llama-args.txt

run-llama  →  llama-server.exe  :8081  (OpenAI-compatible /v1)
                    │
                    ├─ built-in llama.cpp chat Web UI (same port)
                    └─ llama-ui → opens llama-config-ui (edits args file)

OpenCode (opencode.json)  →  http://127.0.0.1:8081/v1
mcp-server web_search     →  http://localhost:8080 (SearXNG via http://find)
```

## Operator commands

- **Start inference:** `run-llama` (reads `$env:LLAMACPP_ROOT\llama-args.txt`)
- **Tune flags:** edit `llama-args.txt`, or use `llama-config-ui` (added to `PATH` from the submodule)
- **Open the config WebUI from CLI:** `llama-ui` (wrapper that launches the UI)
- **Update binaries:** `cd $env:LLAMACPP_ROOT` then `git pull` and `git submodule update`
- **Chat in browser:** with the server running, open `http://127.0.0.1:8081` (default port from deploy script)

## Homelab integration gaps (intentional today)

- Caddy has a **commented** example route for a Llama UI host; nothing is wired to `find` / `draw` yet.
- Model IDs in `opencode and skills/opencode/opencode.json` should stay aligned with the GGUF filename and whatever `llama-server` advertises on `/v1/models`.

See `../AGENTS.md` for the full homelab map and `../Deploy-Homelab.ps1` for pins (model repo, GGUF filenames, default server flags).
