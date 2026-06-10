# Automated AI & Developer Homelab Stack

A fully automated, zero-friction local deployment tailored for Windows. This stack automates the deployment of local LLMs, agentic developer workflows, Model Context Protocol (MCP) servers, and private search tools, all routed cleanly through a local reverse proxy.

## The Model Context Protocol (MCP) Arsenal

This homelab includes three robust MCP servers, allowing your AI agents (like Claude Desktop or OpenCode) to interact directly with your system, code, private data, and images securely.

### 1. Coding Assistant MCP
A comprehensive system and codebase integration tool designed for local developer agents. It bridges the gap between the AI and your operating system.
* **System Execution:** Allows the AI to run raw shell commands, execute isolated Python code, manage Docker containers, and query SQLite databases.
* **Codebase Management:** Grants the AI read/write access to your file system, the ability to search directories via regex/ripgrep, and full Git repository control (status, diff, log).
* **Code Quality & Build Tools:** The AI can autonomously run tests (pytest), trigger builds (npm, yarn, poetry), and enforce formatting/linting using tools like Prettier, ESLint, Black, and Flake8.
* **Web & Document Parsing:** Includes integrated web searching via your local SearXNG instance, DOM/HTML/CSS parsing, and raw text extraction from PDFs using textract.

### 2. Proton Privacy Suite MCP
A massive 31-tool integration for the Proton privacy ecosystem, allowing your AI to interact securely with Mail, Pass, Drive, and VPN. 
* **Capabilities:** The AI can read/send emails, search Proton Pass vaults, download/upload files to Proton Drive via rclone, and check VPN status.
* **Format & Compatibility:** Ships as a convenient `.mcpb` bundle for instant configuration within Claude Desktop, or can be run completely standalone via Python or Node for native OpenCode integration.
* **Secure Credential Storage:** Credentials are never committed to version control. They are kept as .env variables, or stored locally and securely in either a `.env` file (excluded via gitignore) or a `bridge.json` file located in your user profile at `~/.proton-mcp/`.

### 3. Vision MCP
Gives the local coding model **vision**, which OpenCode's OpenAI-compatible provider otherwise can't deliver (it never forwards pasted images). Two cooperating pieces:
* **`opencode-vision` plugin** (`plugins/opencode-vision.ts`) — intercepts a pasted image, saves it to a temp file, and rewrites the message to instruct the model to call the `vision_analyze` tool with the path. Loaded raw by OpenCode's Bun runtime — **no build step**.
* **`vision` MCP server** (`opencode-vision/mcp/vision_mcp.py`) — exposes `vision_analyze`. On first use it **lazily spawns a dedicated `llama-server` on port 8083** running a small VLM (Qwen2.5-VL-3B), forwards the image, returns the text description, and idles the server out after 5 minutes (reaped via a Windows Job Object on exit).
* **Why a separate server:** the `:8081` coding server runs `--spec-type mtp`, which is incompatible with multimodal input in llama.cpp. Vision is isolated on `:8083` with no MTP. See `opencode-vision/docs/ARCHITECTURE.md`.

### 4. Skills (.agents/skills/)
Local-AI skills auto-loaded by OpenCode, Claude Code, and other MCP-aware runners. Each skill is a SKILL.md plus optional helper files loaded on demand.

* **brainstorming:** Gates implementation skills behind a written, user-approved design spec; turns vague ideas into actionable specs via collaborative dialogue.
find-skills — discovers and installs skills from the skills.sh registry with install-count + security-audit vetting.
* **Frontend-design:** Opinionated frontend builder that pushes for distinctive aesthetic choices and explicitly avoids generic "AI slop" defaults.
* **Proton-mail:** Drives the proton-mcp server: 31 tools for Proton Mail, Pass, Drive, and VPN status.
requesting-code-review — dispatches a code-reviewer subagent with crafted context after tasks complete or before merges.
* **Systematic-debugging:** Enforces root-cause investigation before any fix; iron law: no fixes without diagnosis first.
* **Ui-ux-pro-max:** Deep design reference: 50+ styles, 161 palettes, 57 font pairings, 99 UX guidelines across 10 frameworks.
* **using-superpowers:** Bootstrap skill that establishes the skill-discovery protocol at session start.

See .agents/README.md for the full table and skill-authoring guide.

## Core Infrastructure

* **Prettified Local DNS Routing:** No more typing IP addresses or port numbers. The deployment script automatically configures your Windows hosts file and a Caddy reverse proxy.
* **Local LLM Server (Llama.cpp):** Clones [llamacpp-turboquant-mtp-executables-for-cuda-12.8](https://github.com/bankenichi/llamacpp-turboquant-mtp-executables-for-cuda-12.8) to `C:\Program Files\llamacpp` by default, pulls the [llama-config-ui](https://github.com/bankenichi/llama-config-ui) submodule, downloads pinned GGUF weights via Hugging Face, and installs a global `run-llama` command (OpenAI API on port **8081**). The install location is overridable via the `LLAMACPP_ROOT` environment variable (see optional install step below) — useful if you'd rather keep the 20+ GB of GGUF weights off your system drive. See `llama/README.md` for the full map.
* **Agentic CLI (OpenCode):** Seamlessly installs Node.js and the `opencode-ai` CLI. The script automatically creates robust symlinks, mapping your local `.agents` and `opencode` configurations directly into the repository for safe version control. It also sets a machine-scope `HOMELAB_ROOT` environment variable pointing at the deployed repo, which `opencode.json` references via `{env:HOMELAB_ROOT}` so MCP server paths stay portable across machines (no hard-coded user profiles or drive letters).
* **SearXNG & Daily Logo Rotator:** A private, local search engine that works out of the box (no VPN required). A lightweight background container picks a random image from your `logos` folder and applies it via an atomic file swap every 24 hours (think Google Doodles). **Optional:** if Google starts returning `403` on image search, you can route SearXNG's outbound traffic through a Proton VPN US node by running `Enable-SearxngVpn.ps1` — it switches the stack to a `gluetun`-gated variant with an hourly exit-IP rotator. See "Route SearXNG through Proton VPN (optional)" below.
* **Local Vision:** The local coding model gains image understanding via the `opencode-vision` plugin + `vision` MCP server, which lazily spawns a dedicated Qwen2.5-VL server on port 8083 when you paste an image. See the Vision MCP section above and `opencode-vision/`.

## Runtime layout (how the pieces connect)

```
Deploy-Homelab.ps1
        │
        ├─ git clone/pull → %LLAMACPP_ROOT%             (default: C:\Program Files\llamacpp)
        ├─ submodule     → …\llama-config-ui          (added to PATH)
        ├─ installs      → run-llama / llama-ui       (CLI wrappers)
        ├─ downloads     → *.gguf + mmproj.gguf       (into install dir)
        └─ writes        → llama-args.txt             (server flags)

run-llama  →  llama-server.exe  :8081  (OpenAI-compatible /v1)
llama-ui   →  opens the llama-config-ui WebUI (edits llama-args.txt)

OpenCode (opencode.json)  →  http://127.0.0.1:8081/v1
                          →  {env:HOMELAB_ROOT}/mcp-server       (coding-assistant MCP)
                          →  {env:HOMELAB_ROOT}/proton-mcp       (proton suite MCP)
                          →  {env:HOMELAB_ROOT}/opencode-vision  (vision MCP)
vision MCP (on demand)    →  spawns llama-server :8083 (Qwen2.5-VL-3B) → idles out after 300s
mcp-server web_search     →  http://localhost:8080 (SearXNG via http://find; VPN egress optional)
```

The script publishes two machine-scope environment variables you can use to locate things from anywhere on the system: `HOMELAB_ROOT` (forward-slashed path to this repo, referenced by `opencode.json` for MCP server paths) and `LLAMACPP_ROOT` (path to the llama.cpp install dir, default `C:\Program Files\llamacpp`). `LLAMACPP_ROOT` is overridable — set it before deploy to choose a non-default install location.

## Installation & Deployment

This stack is designed to be highly portable and deployable on completely bare-metal Windows installations.

1. Download the `Deploy-Homelab.ps1` script to your desired machine and place it in the folder where you want your Homelab to live.
2. Right-click the script and select **Run with PowerShell**.
3. Accept any Administrator prompts, which are required to configure your `hosts` file and install system-level dependencies.
4. **PLEASE SAVE ALL WORK.** The script will automatically trigger a system reboot halfway through the process to apply required Docker permission changes. It will seamlessly resume exactly where it left off once you log back in. You will receive one final UAC prompt upon login to allow the script to finish.
5. Sit back while the script carves out its directory, pulls the latest code, downloads the GGUF models, and launches the container stack.
6. Once complete, you can access your newly deployed tools immediately:
   * **SearXNG:** Open your browser and navigate to `http://find/`
   * **Excalidraw:** Open your browser and navigate to `http://draw/`
   * **Llama.cpp Server:** Open a new terminal window and type `run-llama`
   * **OpenCode CLI:** Open a new terminal window and type `opencode`

### Optional: Installing llama.cpp to a different drive

By default, `Deploy-Homelab.ps1` installs llama.cpp and downloads the GGUF model weights to `C:\Program Files\llamacpp`. The default model alone is ~20 GB and lives next to the executables — combined with any future models you stash there, this can fill a smaller system drive fast. If you'd rather put it on a different drive (e.g. a large data drive), the script honors a pre-set `LLAMACPP_ROOT` environment variable and uses it as the install root for all llama.cpp operations: clone, PATH registration, model downloads, and the generated `run-llama` wrapper.

**This must be done BEFORE you run `Deploy-Homelab.ps1`.** The override has to be persisted at User or Machine scope, not as a transient variable in your current shell — the deploy script triggers a UAC self-elevation, and the elevated process reads environment variables fresh from the registry. A `$env:LLAMACPP_ROOT = "..."` set in your shell will not survive the elevation prompt.

Open an **Administrator PowerShell** prompt and run:

```powershell
[Environment]::SetEnvironmentVariable("LLAMACPP_ROOT", "D:\AI\llamacpp", [EnvironmentVariableTarget]::Machine)
```

Replace `D:\AI\llamacpp` with your desired path. The parent directory must exist (or be creatable), but the leaf folder itself will be created by the script if it doesn't exist yet. Then **close that PowerShell window**, open a **new** terminal, and proceed with `Deploy-Homelab.ps1` as normal. The script will detect the override, install llama.cpp at your chosen location, register that path on the system PATH, download the GGUF weights into it, and persist the value into machine scope (so subsequent re-runs pick it up automatically — you don't need to re-set it before every deploy).

To verify the override took effect in a fresh terminal before deploying:

```powershell
[Environment]::GetEnvironmentVariable("LLAMACPP_ROOT", "Machine")
```

This should print the path you set. If it's empty, the variable wasn't persisted — repeat the `SetEnvironmentVariable` call above, making sure you're in an Administrator PowerShell session and not a regular one. The same is true if you want to **change** the location later: re-run `SetEnvironmentVariable` with the new path from an admin PowerShell, then re-run `Deploy-Homelab.ps1` (note: this will leave the old install dir in place; remove it manually if you want to reclaim the space).

### Optional: Installing the Proton MCP in Claude Desktop

If you use Claude Desktop and want to grant it access to the Proton Privacy Suite:
1. Ensure Claude Desktop is installed and closed.
2. Locate the `.mcpb` bundle file provided in the Proton MCP directory.
3. Double-click the `.mcpb` file. Claude Desktop will open and walk you through a brief configuration wizard.
If this does not open like it should go to Settings > Extensions > Advanced Settings > Install Extensions and select the `.mcpb` file manually.
4. When prompted, input your Proton Bridge credentials along with any other information. Note: This requires the Bridge app password, not your primary Proton account password.
5. The MCP is now permanently installed and ready to be called by Claude in your conversations.

### Optional: Proton Suite Optional Settings
The tool can be configured to use a different sender address than your main proton email in the "From" field.

You can also configure it to append a custome HTML signature by having a file with a valid file name in the same folder ("html_signature.txt","html signature.txt","signature.html").

### Optional: Adding Logos to SearXNG
To add more images to the rotation, simply place any `.png` files into the `searxng/logos/` folder. The rotator script will automatically include them in the pool during its next 24-hour cycle (or the next time the stack is restarted).

**Create Your Own Logos**: Want to design perfect, PNG logos to add to the rotation? Use my **[Monogram Logo Generator](https://github.com/bankenichi/Monogram-Logo-Generator)** to instantly create perfectly sized, transparent background graphics. Just generate them and drop them straight into the `logos` folder!

### Route SearXNG through Proton VPN (optional)
By default SearXNG runs **without** a VPN and works fine. If Google starts returning `403` on image search from your IP, you can route SearXNG's outbound traffic through a **Proton VPN US node** (a `gluetun` gateway). This is **opt-in** — the deploy never requires it, and bare-metal installs run the no-VPN base.

Setup (one time, requires a paid Proton plan):

1. Log in at **account.protonvpn.com** → **Downloads** → **WireGuard configuration**.
2. Name it (e.g. `searxng-us`), Platform = **GNU/Linux**, pick any **US** server, click **Create**.
3. Move the downloaded config into the **repo root** (`C:\...\Homelab\`) and rename it **exactly** `searxng-us.conf` so the command below matches. (Or keep your own name and point `-ConfPath` at it.)
4. From the repo root, run the helper:

   ```powershell
   .\Enable-SearxngVpn.ps1 -ConfPath .\searxng-us.conf
   ```

   It extracts the key, writes `searxng/vpn.env` (gitignored), switches the stack to `docker-compose.vpn.yml`, waits for the tunnel, and prints the US exit IP. You can also pass `-Key "<privatekey>"` directly, or run with neither to be prompted. To revert: `.\Enable-SearxngVpn.ps1 -Disable`.

#### Manual (without the helper)
The helper just wraps Docker Compose. To do it by hand — useful after a `docker compose down -v`, or if you'd rather not run the script:

```powershell
# 1. One time: create the secret from your Proton key. The file MUST be named exactly
#    "vpn.env" and live in the searxng folder (it is gitignored).
cd C:\Users\kenic\Documents\Homelab\searxng
Copy-Item vpn.env.example vpn.env          # then edit vpn.env and set WIREGUARD_PRIVATE_KEY=<your key>

# 2. Bring up the VPN-routed stack (the two compose files share the same project,
#    so this reuses your existing volumes).
docker compose -f docker-compose.yml down       # stop the base stack if it's running
docker compose -f docker-compose.vpn.yml up -d

# 3. Verify: gluetun healthy + a US exit IP.
docker ps
docker exec searxng-core wget -qO- https://ipinfo.io/ip
```

If `vpn.env` already exists (e.g. you only ran `down -v`, which keeps files), skip step 1 and just run step 2. To go back to the no-VPN base: `docker compose -f docker-compose.vpn.yml down; docker compose up -d`.

A `vpn-rotator` sidecar restarts the tunnel hourly to cycle the US exit IP (Proton IPs get flagged by Google over time); override the cadence with `-Interval <seconds>` or `VPN_ROTATE_INTERVAL` in `searxng/.env`. The MTU is pinned to `1320` because WSL2's path-MTU discovery is broken — without it the tunnel connects but times out. Full design, verification commands, and troubleshooting: `searxng/VPN-EGRESS.md`.

### Optional: Vision (VISION_* env knobs)
The `vision` MCP entry in `opencode.json` carries the vision server's configuration. Common knobs (full table in `opencode-vision/docs/ARCHITECTURE.md` §4.5):

* `VISION_SPAWN_SERVER` (`1`) — set `0` to disable lazy spawn and use an externally-run `:8083` (e.g. `opencode-vision/vision-server/run-vision.ps1`).
* `VISION_LLAMA_IDLE_TIMEOUT` (`300`) — idle seconds before the `:8083` server auto-shuts down; `0` keeps it alive until OpenCode exits.
* `VISION_LLAMA_MODEL` / `VISION_LLAMA_MMPROJ` — paths to the VLM + mmproj GGUFs (downloaded by the deploy script into `opencode-vision/`); keep these filenames in sync with the deploy script's `$visionModelFile` / `$visionMmprojFile`.
* `VISION_LLAMA_VISIBLE_CONSOLE` (`1`) — `0` spawns the server hidden and logs to `%TEMP%/opencode-vision/llama-server.log`.

## Troubleshooting & Failure Modes

The `Deploy-Homelab.ps1` script is built with strict error checking. If the script halts and outputs a fatal error, locate the corresponding failure mode below:

**WSL2 update failed**
The `wsl --update` command failed, usually due to no internet connection or Windows Update being blocked. Ensure your connection is active and Windows Update is enabled. Run `wsl --update` manually in an elevated PowerShell window, then re-run the deployment.

**Failed to download Docker installer**
The script could not reach the Docker servers to download the setup file. Check your internet connection and ensure your firewall is not blocking outbound connections to `desktop.docker.com`.

**Docker daemon did not start in time**
The script waited 90 seconds, but the Docker engine never came online. If Docker was just installed by the script, it often requires manual intervention for the very first boot. Open the Start Menu, launch Docker Desktop manually, and accept the Service Agreement. Wait for the tray icon to indicate it is running, then run `Deploy-Homelab.ps1` again.

**Git installation failed**
The Winget package manager failed to install Git. Run the script again. If it continues to fail, manually install Git for Windows, ensure it is added to your system PATH, and re-run the deployment.

**Node.js / npm / OpenCode installation failed**
Winget or npm failed to pull the required JavaScript dependencies. If Node.js installed but `opencode-ai` failed, the system PATH likely has not refreshed. Close your terminal, open a fresh Administrator PowerShell prompt, and run `npm install -g opencode-ai@latest` manually.

**OpenCode MCP servers fail to spawn (`HOMELAB_ROOT` unresolved)**
`opencode.json` references `{env:HOMELAB_ROOT}` for the `coding-assistant` and `proton-suite` MCP commands. The deploy script sets this variable at machine scope, but already-open terminals and editors still hold the old empty environment. Close OpenCode and any shell windows that were open before deployment, then relaunch from a fresh terminal. To verify the value is set, run `[Environment]::GetEnvironmentVariable("HOMELAB_ROOT","Machine")` in a new PowerShell window — it should print the forward-slashed path to your Homelab repo. If empty, re-run `Deploy-Homelab.ps1`.

**Python / pip / Hugging Face CLI installation failed**
Winget failed to install Python 3.12, or Python failed to bootstrap pip. If Python is installed but the Hugging Face CLI failed, open a fresh Administrator PowerShell prompt and run `pip install "huggingface_hub[cli,hf_xet]" --break-system-packages` manually. (MCP Python dependency failures are non-fatal — the deploy collects them into a "DEPENDENCY ISSUES" summary at the end with the exact pip command to re-run.)

**Failed to clone or pull repository**
Git cannot reach GitHub, or the local directory is locked. Verify your internet connection. If updating an existing repository fails due to local modifications, stash your changes or delete the `Homelab` directory to allow a fresh clone.

**Failed to write to hosts file**
A strict antivirus (e.g., Windows Defender, Malwarebytes) is actively blocking modifications to the Windows hosts file. Temporarily disable your antivirus's "Hosts file protection" feature, or manually add `127.0.0.1 find` and `127.0.0.1 draw` to `C:\Windows\System32\drivers\etc\hosts` using Notepad running as Administrator.

**Failed to download the main GGUF model**
The Hugging Face CLI failed to pull the model, usually due to a network interruption or insufficient disk space. Ensure you have enough free storage on your primary drive. Open a terminal, navigate to your `llamacpp` installation directory, and run the `huggingface-cli download` command manually to resume the download.

**Expected folder not found**
The `git clone` command technically succeeded, but the files are missing. This usually means the repository structure on GitHub is broken or missing directories. Check the upstream repository to ensure the proxy, searxng, and excalidraw folders exist.

**Failed to start Caddy Proxy / SearXNG / Excalidraw**
Docker compose failed to boot the container stack. If Caddy fails, it is almost always a port collision (Caddy strictly requires ports 80 and 443). Open PowerShell as Administrator, run `netstat -abno | findstr :80`, identify the conflicting Process ID (PID), and stop that service. If other containers fail, check the terminal output for volume mounting errors or missing `.env` files.

**`Network searxng_default Resource is still in use` after `docker compose down`**
You ran `down` with a different compose file than the one you brought the stack up with. SearXNG has two files — `docker-compose.yml` (base, no VPN) and `docker-compose.vpn.yml` (VPN). They share the same project, but `docker compose down` only removes the services defined in *the file you pass*. So if the VPN stack was running and you `down` the base file, `searxng-gluetun` and `searxng-vpn-rotator` are left behind (they're not in the base file) — still attached to `searxng_default`, which can't then be removed.

The fix is to tear down with the VPN file (the superset), or sweep orphans:

```powershell
cd C:\Users\kenic\Documents\Homelab\searxng
docker compose -f docker-compose.vpn.yml down -v   # removes gluetun + vpn-rotator too
# or, regardless of which file:  docker compose down --remove-orphans
```

Rule of thumb: tear down with the **same file you brought up with**, or use `docker-compose.vpn.yml` since it's a superset of the base. `docker ps --filter "name=searxng"` shows exactly which containers are still holding the network.

---

<div align="center">
  <a href="https://ko-fi.com/bankenichi" target="_blank">
    <img src="https://raw.githubusercontent.com/bankenichi/Monogram-Logo-Generator/main/kofi%20logo.png" alt="Support me on Ko-fi" height="120">
  </a>
</div>



All bundled skills are open-source — MIT for brainstorming, using-superpowers, requesting-code-review, systematic-debugging, find-skills, ui-ux-pro-max, and proton-mail; Apache 2.0 for frontend-design (its LICENSE.txt ships in the skill folder). The .agents/.skill-lock.json file records each skill's upstream repo for provenance.