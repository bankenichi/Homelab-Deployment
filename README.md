# SearXNG Homelab Stack

A fully automated, zero-friction SearXNG local deployment tailored for Windows. This stack utilizes a Caddy reverse proxy for clean local DNS routing and features a robust, automated logo-rotating sidecar that safely bypasses Windows/WSL2 file-syncing bugs using pure Linux native volumes.

## Key Features

* **Prettified Local URL (`find/`)**: No more typing IP addresses, `localhost`, or port numbers. The deployment script automatically configures your local DNS and Caddy reverse proxy so you can access your search engine simply by navigating to `find/` in your browser.
* **Daily Logo Rotator**: Keep your search engine looking fresh. A lightweight background container automatically picks a random image from your `logos` folder and safely applies it via an atomic file swap every 24 hours.
* **Create Your Own Logos**: Want to design perfect, PNG logos to add to the rotation? Use my **[Monogram Logo Generator](https://github.com/bankenichi/Monogram-Logo-Generator)** to instantly create perfectly sized, transparent background graphics. Just generate them and drop them straight into the `logos` folder!
* **Self-Actualizing Deployment**: A single, robust PowerShell script (`Deploy-Homelab.ps1`) handles everything. It checks for dependencies, installs WSL2, Docker, and Git if missing, clones or updates this repository, injects the necessary DNS records, and spins up the entire stack.

---

## Installation & Deployment

This stack is designed to be highly portable and deployable on completely bare-metal Windows installations.

1. Download the `Deploy-Homelab.ps1` script to your desired machine and place it in the folder where you want your Homelab to live.
2. Right-click the script and select **Run with PowerShell**.
3. Accept any Administrator prompts (required to configure your `hosts` file and install dependencies).
4. Sit back. The script will automatically carve out its directory, pull the latest code, and launch the search engine.
5. Once complete, open your browser and go to `find/`.

### Adding New Logos
To add more images to the rotation, simply place any `.png` files into the `searxng/logos/` folder. The rotator script will automatically include them in the pool during its next 24-hour cycle (or the next time the stack is restarted).

---

## Troubleshooting & Failure Modes

The `Deploy-Homelab.ps1` script is built with strict error checking (`$LASTEXITCODE`). If the script halts and outputs a fatal error, find the corresponding failure mode below:

### 1. `WSL2 update failed`
* **The Cause:** The `wsl --update` command failed, usually due to no internet connection or Windows Update being blocked on your machine.
* **The Fix:** Ensure your internet connection is active and Windows Update is not disabled. Run `wsl --update` manually in an elevated PowerShell window, then re-run the deployment script.

### 2. `Failed to download Docker installer`
* **The Cause:** The script couldn't reach the Docker servers to download the setup file.
* **The Fix:** Check your internet connection. Ensure your firewall or network isn't blocking outbound connections to `desktop.docker.com`.

### 3. `Git installation failed`
* **The Cause:** `winget` failed to install Git, or the environment path hasn't refreshed.
* **The Fix:** Run the script again. If it continues to fail, manually install Git for Windows, ensure it is added to your system PATH, and re-run the deployment.

### 4. `Failed to clone repository` or `Failed to pull repository`
* **The Cause:** Git cannot reach GitHub, or the repository URL in the script is incorrect or private.
* **The Fix:** Verify your internet connection. Check line 3 of `Deploy-Homelab.ps1` and ensure `$repoUrl` is pointing to the correct, accessible GitHub repository.

### 5. `Failed to write to hosts file`
* **The Cause:** A strict antivirus (like Windows Defender, Malwarebytes, or Bitdefender) is actively blocking modifications to `C:\Windows\System32\drivers\etc\hosts`.
* **The Fix:** Temporarily disable your antivirus's "Hosts file protection" feature, or manually add `127.0.0.1 find` to the file using Notepad (run as Administrator).

### 6. `Docker daemon did not start in time`
* **The Cause:** The script waited 90 seconds, but the Docker engine never came online. If Docker was just installed by the script, it often requires manual intervention for the very first boot.
* **The Fix:**
  1. Open the Start Menu and launch **Docker Desktop** manually.
  2. Accept the Service Agreement if prompted.
  3. Wait for the Docker icon in your system tray to turn green or say `Docker Desktop running`.
  4. If Docker asks you to log out or restart your computer to apply WSL2 permissions, do so.
  5. Run `Deploy-Homelab.ps1` again.

### 7. `Expected folder [...] not found`
* **The Cause:** The `git clone` command technically succeeded, but the files aren't there. This usually means the repository structure on GitHub is broken or missing the `proxy` or `searxng` directories.
* **The Fix:** Check your GitHub repository to ensure the folders exist exactly as named. If you made a local typo, delete the `Homelab` folder and run the script to pull a fresh copy.

### 8. `Failed to start Caddy Proxy`
* **The Cause:** Docker compose failed to boot the reverse proxy. This is almost always a port collision. Caddy requires ports `80` and `443`.
* **The Fix:** Another application is using web ports on your machine. Common culprits include Skype, VMWare, or Windows IIS. Open PowerShell as Admin, run `netstat -abno | findstr :80`, identify the conflicting Process ID (PID), and stop that service.

### 9. `Failed to start SearXNG`
* **The Cause:** Docker compose failed to boot the search stack. This could be due to a malformed `docker-compose.yml`, a missing `.env` file, or volume mounting errors.
* **The Fix:** Check the red terminal output directly above the fatal error message. Ensure your `.env` file is present in the `searxng` folder if required by your configuration.

---

<div align="center">
  <a href="https://ko-fi.com/bankenichi" target="_blank">
    <img src="https://raw.githubusercontent.com/bankenichi/Monogram-Logo-Generator/main/kofi%20logo.png" alt="Support me on Ko-fi" height="120">
  </a>
</div>
