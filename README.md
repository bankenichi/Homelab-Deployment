# Automated AI & Developer Homelab Stack

A fully automated, zero-friction local deployment tailored for Windows. This stack automates the deployment of local LLMs, agentic developer workflows, Model Context Protocol (MCP) servers, and private search tools, all routed cleanly through a local reverse proxy.

## The Model Context Protocol (MCP) Arsenal

This homelab includes two incredibly robust MCP servers, allowing your AI agents (like Claude Desktop or OpenCode) to interact directly with your system, code, and private data securely.

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

### 3. Skills (.agents/skills/)
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
* **Local LLM Server (Llama.cpp):** Automatically downloads and configures a highly optimized, local instance of Llama.cpp. The deployment includes pre-configured server flags fine-tuned for high-throughput inference, accessible globally via the `run-llama` command.
* **Agentic CLI (OpenCode):** Seamlessly installs Node.js and the `opencode-ai` CLI. The script automatically creates robust symlinks, mapping your local `.agents` and `opencode` configurations directly into the repository for safe version control.
* **SearXNG & Daily Logo Rotator:** A private, local search engine that stays fresh. A lightweight background container automatically picks a random image from your `logos` folder and applies it via an atomic file swap every 24 hours. (Think Google Doodles)

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

**Python / pip / Hugging Face CLI installation failed**
Winget failed to install Python 3.14, or Python failed to bootstrap pip. If Python is installed but the Hugging Face CLI failed, open a fresh Administrator PowerShell prompt and run `pip install huggingface_hub[cli] --break-system-packages` manually.

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

---

<div align="center">
  <a href="https://ko-fi.com/bankenichi" target="_blank">
    <img src="https://raw.githubusercontent.com/bankenichi/Monogram-Logo-Generator/main/kofi%20logo.png" alt="Support me on Ko-fi" height="120">
  </a>
</div>



All bundled skills are open-source — MIT for brainstorming, using-superpowers, requesting-code-review, systematic-debugging, find-skills, ui-ux-pro-max, and proton-mail; Apache 2.0 for frontend-design (its LICENSE.txt ships in the skill folder). The .agents/.skill-lock.json file records each skill's upstream repo for provenance.