"""Acceso al CLI oficial desde un botón. Nunca instala ni envía un prompt."""
import os
from pathlib import Path
import subprocess
from .providers import cli_binary, cli_environment, cli_profile_dir, profiles_for

OFFICIAL_HELP = {
    "gemini-cli": "https://geminicli.com/docs/get-started/authentication/",
    "antigravity-cli": "https://antigravity.google/docs/cli/install/",
    "gemini-api": "https://aistudio.google.com/api-keys",
    "deepseek-api": "https://platform.deepseek.com/api_keys",
    "openai-api": "https://platform.openai.com/api-keys",
    "anthropic-api": "https://platform.claude.com/settings/keys",
    "qwen-api": "https://www.alibabacloud.com/help/en/model-studio/get-api-key",
    "codex-cli": "https://developers.openai.com/codex/auth",
    "claude-cli": "https://code.claude.com/docs/en/authentication",
}


def launch_login(root: Path, provider: str, profile="principal"):
    if provider not in {"gemini-cli", "antigravity-cli", "codex-cli", "claude-cli"}:
        raise ValueError("El acceso interactivo corresponde a un CLI, no a una clave API")
    if os.name != "nt":
        raise ValueError("El botón de acceso interactivo está implementado para Windows")
    command = cli_binary(provider)
    if profile not in profiles_for(provider):
        raise ValueError("Perfil CLI inválido")
    folder = root.resolve() / "acceso_cli" / provider / profile
    folder.mkdir(parents=True, exist_ok=True)
    options = {"cwd": folder, "shell": False, "creationflags": subprocess.CREATE_NEW_CONSOLE}
    if provider == "antigravity-cli" and profile.startswith("fab"):
        # La identidad Windows conserva el keyring. No exportar tokens ni simular HOME.
        from .fab_bridge import verified_binary
        executable, _checksum = verified_binary()
        runas = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/runas.exe")
        command = [runas, "/profile", "/savecred", "/user:" + profile,
                   subprocess.list2cmdline([str(executable)])]
        options["cwd"] = executable.parent
    else:
        environment = cli_environment(provider, profile)
        for variable in ("GEMINI_CLI_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
            if variable in environment:
                Path(environment[variable]).mkdir(parents=True, exist_ok=True)
        options["env"] = environment
        if provider == "codex-cli":
            command += ["login"]
        elif provider == "claude-cli":
            command += ["auth", "login"]
    process = subprocess.Popen(command, **options)
    return {"pid": process.pid, "provider": provider, "prompt_sent": False,
            "profile": profile,
            "notice": "Completa el acceso en la ventana oficial y ciérrala. Abrir no confirma autenticación."}
