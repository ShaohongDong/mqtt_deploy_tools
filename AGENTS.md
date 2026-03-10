# Repository Guidelines

## Project Structure & Module Organization
This repository is a small Python CLI for installing and managing a Mosquitto broker on Ubuntu. The main entrypoint is `mqtt_manager.py`, which contains the Click CLI plus installer, config, user, service, monitor, and log classes. Tests live in `test_mqtt_manager.py` and cover command construction, config generation, and CLI validation. Default broker settings are stored in `config/default_config.json`. Keep new code in the existing module unless a clear split improves readability.

## Build, Test, and Development Commands
Create an isolated environment before editing:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Useful commands:

```bash
python3 mqtt_manager.py --help        # inspect CLI commands
python3 -m unittest -q                # run the test suite
sudo ./mqtt_manager.py config generate --no-anonymous
```

Use `sudo` only for commands that touch system packages, `/etc/mosquitto`, or `systemctl`.

## Coding Style & Naming Conventions
Follow existing Python style: 4-space indentation, `snake_case` for functions and variables, `CamelCase` for classes, and type hints for non-trivial interfaces. Prefer small methods over dense inline logic. Keep user-facing CLI text, README updates, and comments consistent with the current Chinese-language output. There is no configured formatter or linter, so match the surrounding style closely and keep imports limited to the standard library plus `click`.

## Testing Guidelines
Tests use `unittest` with `click.testing.CliRunner` and `unittest.mock`. Name new tests `test_<behavior>` and keep them deterministic. Avoid real system changes in tests: patch `CommandExecutor.check_root`, mock subprocess calls, and redirect config paths to `tempfile` directories as `test_mqtt_manager.py` already does. Add coverage for both success paths and CLI argument conflicts when changing command behavior.

## Commit & Pull Request Guidelines
The current Git history uses short descriptive subjects in Chinese, for example `初始提交：MQTT服务器自动化部署和管理工具`. Keep commit messages concise and focused on one change. Pull requests should explain the operational impact, list commands used for verification, and mention any behavior that requires `sudo` or affects `/etc/mosquitto`. Include sample CLI output when changing user-facing commands.

## Security & Configuration Tips
Do not commit real passwords, broker credentials, or machine-specific `/etc/mosquitto` files. Treat `config/default_config.json` as safe defaults only, and verify changes that alter authentication, listener ports, or persistence behavior with tests before merging.
