#!/usr/bin/env python3
"""
MQTT服务器自动化部署和管理工具
用于Ubuntu平台下Mosquitto MQTT服务器的安装、配置和管理
"""

import os
import sys
import subprocess
import json
import getpass
import grp
import pwd
from pathlib import Path
from typing import Dict, List, Optional
import click

# 配置路径
SCRIPT_DIR = Path(__file__).parent
CONFIG_DIR = SCRIPT_DIR / "config"
MOSQUITTO_CONF = "/etc/mosquitto/mosquitto.conf"
MOSQUITTO_PASSWD_FILE = "/etc/mosquitto/passwd"
DEFAULT_CONFIG_FILE = CONFIG_DIR / "default_config.json"


def secure_password_file(passwd_path: Path):
    """收紧密码文件权限，避免Mosquitto因权限问题拒绝加载"""
    if not passwd_path.exists():
        return

    passwd_path.chmod(0o700)

    try:
        uid = pwd.getpwnam("mosquitto").pw_uid
        gid = grp.getgrnam("mosquitto").gr_gid
    except KeyError:
        return

    try:
        os.chown(passwd_path, uid, gid)
    except PermissionError:
        return


class CommandExecutor:
    """命令执行器"""

    @staticmethod
    def run(cmd: List[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
        """执行系统命令"""
        try:
            if capture:
                result = subprocess.run(
                    cmd,
                    check=check,
                    capture_output=True,
                    text=True
                )
            else:
                result = subprocess.run(cmd, check=check)
            return result
        except FileNotFoundError:
            raise click.ClickException(f"未找到系统命令: {cmd[0]}，请先安装相关程序")
        except subprocess.CalledProcessError as e:
            click.echo(f"命令执行失败: {' '.join(cmd)}", err=True)
            if e.stderr:
                click.echo(f"错误信息: {e.stderr}", err=True)
            raise

    @staticmethod
    def check_root():
        """检查是否有root权限"""
        if os.geteuid() != 0:
            click.echo("错误: 此操作需要root权限，请使用sudo运行", err=True)
            sys.exit(1)


class MosquittoInstaller:
    """Mosquitto安装器"""

    def __init__(self):
        self.executor = CommandExecutor()

    def install(self):
        """安装Mosquitto"""
        self.executor.check_root()

        click.echo("开始安装Mosquitto MQTT服务器...")

        # 更新包列表
        click.echo("更新软件包列表...")
        self.executor.run(["apt-get", "update"])

        # 安装mosquitto和客户端工具
        click.echo("安装mosquitto和mosquitto-clients...")
        self.executor.run(["apt-get", "install", "-y", "mosquitto", "mosquitto-clients"])

        # 创建必要的目录
        click.echo("创建配置目录...")
        Path("/etc/mosquitto/conf.d").mkdir(parents=True, exist_ok=True)

        # 启用服务
        click.echo("启用mosquitto服务...")
        self.executor.run(["systemctl", "enable", "mosquitto"])

        click.echo("Mosquitto安装完成")


class MosquittoConfig:
    """Mosquitto配置管理器"""

    def __init__(self):
        self.executor = CommandExecutor()
        self.default_config = self._load_default_config()

    def _load_default_config(self) -> Dict:
        """加载默认配置"""
        if DEFAULT_CONFIG_FILE.exists():
            with open(DEFAULT_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {
            "port": 1883,
            "websocket_port": 9001,
            "allow_anonymous": False,
            "persistence": True,
            "log_dest": "syslog"
        }

    def generate_config(self, port: int = None, allow_anonymous: bool = None,
                       persistence: bool = None, websocket: bool = False):
        """生成配置文件"""
        self.executor.check_root()

        # 使用默认值或用户提供的值
        config = {
            "port": port or self.default_config["port"],
            "allow_anonymous": allow_anonymous if allow_anonymous is not None else self.default_config["allow_anonymous"],
            "persistence": persistence if persistence is not None else self.default_config["persistence"],
            "websocket": websocket
        }

        # 备份现有配置
        if Path(MOSQUITTO_CONF).exists():
            backup_path = f"{MOSQUITTO_CONF}.backup"
            click.echo(f"备份现有配置到: {backup_path}")
            self.executor.run(["cp", MOSQUITTO_CONF, backup_path])

        # 生成新配置
        click.echo("生成新配置文件...")
        if not config["allow_anonymous"]:
            self._ensure_password_file()
        config_content = self._build_config_content(config)

        with open(MOSQUITTO_CONF, 'w', encoding='utf-8') as f:
            f.write(config_content)

        click.echo(f"配置文件已生成: {MOSQUITTO_CONF}")
        click.echo("配置参数:")
        click.echo(f"  监听端口: {config['port']}")
        click.echo(f"  允许匿名: {config['allow_anonymous']}")
        click.echo(f"  持久化: {config['persistence']}")
        click.echo(f"  WebSocket: {config['websocket']}")

    def _ensure_password_file(self):
        """确保鉴权配置引用的密码文件存在"""
        passwd_path = Path(MOSQUITTO_PASSWD_FILE)
        if passwd_path.exists():
            secure_password_file(passwd_path)
            return

        click.echo(f"创建空密码文件: {passwd_path}")
        passwd_path.parent.mkdir(parents=True, exist_ok=True)
        passwd_path.touch()
        secure_password_file(passwd_path)

    def _build_config_content(self, config: Dict) -> str:
        """构建配置文件内容"""
        lines = [
            "# Mosquitto配置文件",
            "# 由mqtt_manager.py自动生成",
            "",
            f"listener {config['port']}",
            f"allow_anonymous {str(config['allow_anonymous']).lower()}",
            ""
        ]

        if not config['allow_anonymous']:
            lines.extend([
                f"password_file {MOSQUITTO_PASSWD_FILE}",
                ""
            ])

        if config['persistence']:
            lines.extend([
                "persistence true",
                "persistence_location /var/lib/mosquitto/",
                ""
            ])

        if config['websocket']:
            lines.extend([
                f"listener {self.default_config['websocket_port']}",
                "protocol websockets",
                ""
            ])

        lines.extend([
            "log_dest syslog",
            "log_type error",
            "log_type warning",
            "log_type notice",
            "log_type information",
            ""
        ])

        return "\n".join(lines)


class MosquittoUserManager:
    """Mosquitto用户管理器"""

    def __init__(self):
        self.executor = CommandExecutor()

    @staticmethod
    def _print_service_reload_hint():
        """提示用户变更需要重新加载或重启服务才能生效"""
        click.echo("提示: 用户变更后需要重新加载或重启 mosquitto 服务才能生效。")
        click.echo("如果未配置 reload，请执行: sudo python3 mqtt_manager.py service restart")

    def add_user(self, username: str, password: str = None):
        """添加用户"""
        self.executor.check_root()

        if not password:
            password = getpass.getpass(f"请输入用户 {username} 的密码: ")
            password_confirm = getpass.getpass("请再次输入密码: ")
            if password != password_confirm:
                click.echo("错误: 两次输入的密码不一致", err=True)
                sys.exit(1)

        click.echo(f"添加用户: {username}")

        # 创建密码文件目录
        passwd_path = Path(MOSQUITTO_PASSWD_FILE)
        passwd_path.parent.mkdir(parents=True, exist_ok=True)

        # 使用mosquitto_passwd添加用户
        cmd = ["mosquitto_passwd", "-b"]
        if not passwd_path.exists() or passwd_path.stat().st_size == 0:
            cmd.append("-c")
        cmd.extend([MOSQUITTO_PASSWD_FILE, username, password])
        self.executor.run(cmd)
        secure_password_file(passwd_path)

        click.echo(f"用户 {username} 添加成功")
        self._print_service_reload_hint()

    def delete_user(self, username: str):
        """删除用户"""
        self.executor.check_root()

        if not Path(MOSQUITTO_PASSWD_FILE).exists():
            click.echo("错误: 密码文件不存在", err=True)
            sys.exit(1)

        click.echo(f"删除用户: {username}")
        cmd = ["mosquitto_passwd", "-D", MOSQUITTO_PASSWD_FILE, username]
        self.executor.run(cmd)

        click.echo(f"用户 {username} 删除成功")
        self._print_service_reload_hint()

    def list_users(self):
        """列出所有用户"""
        if not Path(MOSQUITTO_PASSWD_FILE).exists():
            click.echo("密码文件不存在，没有配置用户")
            return

        click.echo("MQTT用户列表:")
        with open(MOSQUITTO_PASSWD_FILE, 'r') as f:
            for line in f:
                username = line.split(':')[0]
                click.echo(f"  - {username}")


class MosquittoService:
    """Mosquitto服务控制器"""

    def __init__(self):
        self.executor = CommandExecutor()

    def start(self):
        """启动服务"""
        self.executor.check_root()
        click.echo("启动mosquitto服务...")
        self.executor.run(["systemctl", "start", "mosquitto"])
        click.echo("服务已启动")

    def stop(self):
        """停止服务"""
        self.executor.check_root()
        click.echo("停止mosquitto服务...")
        self.executor.run(["systemctl", "stop", "mosquitto"])
        click.echo("服务已停止")

    def restart(self):
        """重启服务"""
        self.executor.check_root()
        click.echo("重启mosquitto服务...")
        self.executor.run(["systemctl", "restart", "mosquitto"])
        click.echo("服务已重启")

    def status(self):
        """查看服务状态"""
        click.echo("mosquitto服务状态:")
        self.executor.run(["systemctl", "status", "mosquitto"], check=False, capture=False)

    def enable(self):
        """启用开机自启"""
        self.executor.check_root()
        click.echo("启用mosquitto开机自启...")
        self.executor.run(["systemctl", "enable", "mosquitto"])
        click.echo("已启用开机自启")

    def disable(self):
        """禁用开机自启"""
        self.executor.check_root()
        click.echo("禁用mosquitto开机自启...")
        self.executor.run(["systemctl", "disable", "mosquitto"])
        click.echo("已禁用开机自启")


class MosquittoMonitor:
    """Mosquitto监控器"""

    def __init__(self):
        self.executor = CommandExecutor()
        self.default_config = MosquittoConfig().default_config

    def _detect_listener_port(self) -> int:
        """检测当前配置使用的TCP监听端口"""
        config_path = Path(MOSQUITTO_CONF)
        if not config_path.exists():
            return int(self.default_config["port"])

        with open(config_path, 'r', encoding='utf-8') as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("listener "):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        return int(parts[1])

        return int(self.default_config["port"])

    def _is_local_auth_required(self) -> bool:
        """检测本机配置是否禁止匿名访问"""
        config_path = Path(MOSQUITTO_CONF)
        if not config_path.exists():
            return bool(not self.default_config["allow_anonymous"])

        with open(config_path, 'r', encoding='utf-8') as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("allow_anonymous "):
                    return line.split(maxsplit=1)[1].lower() == "false"

        return bool(not self.default_config["allow_anonymous"])

    def monitor(
        self,
        host: str = "localhost",
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        topic: str = "$SYS/#"
    ):
        """监控服务"""
        resolved_port = port or self._detect_listener_port()
        if host in {"localhost", "127.0.0.1"} and self._is_local_auth_required() and not username:
            raise click.ClickException("当前配置禁止匿名访问，请通过 --username/--password 提供凭据")

        if username and password is None:
            password = getpass.getpass(f"请输入用户 {username} 的密码: ")

        click.echo("监控mosquitto服务 (按Ctrl+C退出)...")
        click.echo(f"订阅主题: {topic}")
        click.echo(f"连接目标: {host}:{resolved_port}")

        try:
            cmd = ["mosquitto_sub", "-h", host, "-p", str(resolved_port), "-v", "-t", topic]
            if username:
                cmd.extend(["-u", username])
            if password:
                cmd.extend(["-P", password])
            subprocess.run(cmd)
        except FileNotFoundError:
            raise click.ClickException("未找到系统命令: mosquitto_sub，请先安装 mosquitto-clients")
        except KeyboardInterrupt:
            click.echo("\n监控已停止")


class MosquittoLogs:
    """Mosquitto日志查看器"""

    def __init__(self):
        self.executor = CommandExecutor()

    def show_logs(self, follow: bool = False, lines: int = 50):
        """查看日志"""
        click.echo(f"mosquitto服务日志 (最近{lines}行):")

        cmd = ["journalctl", "-u", "mosquitto", "-n", str(lines)]
        if follow:
            cmd.append("-f")
            click.echo("实时跟踪日志 (按Ctrl+C退出)...")

        try:
            self.executor.run(cmd, capture=False)
        except KeyboardInterrupt:
            if follow:
                click.echo("\n日志跟踪已停止")


# CLI命令定义
@click.group()
def cli():
    """MQTT服务器自动化部署和管理工具"""
    pass


@cli.command()
def install():
    """安装Mosquitto MQTT服务器"""
    installer = MosquittoInstaller()
    installer.install()


@cli.group()
def config():
    """配置管理"""
    pass


@config.command(name='generate')
@click.option('--port', type=int, help='监听端口 (默认: 1883)')
@click.option('--allow-anonymous', is_flag=True, help='允许匿名连接')
@click.option('--no-anonymous', is_flag=True, help='禁止匿名连接')
@click.option('--persistence', is_flag=True, help='启用持久化')
@click.option('--no-persistence', is_flag=True, help='禁用持久化')
@click.option('--websocket', is_flag=True, help='启用WebSocket支持')
def config_generate(port, allow_anonymous, no_anonymous, persistence, no_persistence, websocket):
    """生成配置文件"""
    if allow_anonymous and no_anonymous:
        raise click.UsageError("--allow-anonymous 与 --no-anonymous 不能同时使用")
    if persistence and no_persistence:
        raise click.UsageError("--persistence 与 --no-persistence 不能同时使用")

    config_mgr = MosquittoConfig()

    # 处理布尔选项
    allow_anon = None
    if allow_anonymous:
        allow_anon = True
    elif no_anonymous:
        allow_anon = False

    persist = None
    if persistence:
        persist = True
    elif no_persistence:
        persist = False

    config_mgr.generate_config(
        port=port,
        allow_anonymous=allow_anon,
        persistence=persist,
        websocket=websocket
    )


@cli.group()
def user():
    """用户管理"""
    pass


@user.command(name='add')
@click.argument('username')
@click.option('--password', help='用户密码 (不提供则交互式输入)')
def user_add(username, password):
    """添加MQTT用户"""
    user_mgr = MosquittoUserManager()
    user_mgr.add_user(username, password)


@user.command(name='delete')
@click.argument('username')
def user_delete(username):
    """删除MQTT用户"""
    user_mgr = MosquittoUserManager()
    user_mgr.delete_user(username)


@user.command(name='list')
def user_list():
    """列出所有MQTT用户"""
    user_mgr = MosquittoUserManager()
    user_mgr.list_users()


@cli.group()
def service():
    """服务控制"""
    pass


@service.command(name='start')
def service_start():
    """启动mosquitto服务"""
    svc = MosquittoService()
    svc.start()


@service.command(name='stop')
def service_stop():
    """停止mosquitto服务"""
    svc = MosquittoService()
    svc.stop()


@service.command(name='restart')
def service_restart():
    """重启mosquitto服务"""
    svc = MosquittoService()
    svc.restart()


@service.command(name='status')
def service_status():
    """查看服务状态"""
    svc = MosquittoService()
    svc.status()


@service.command(name='enable')
def service_enable():
    """启用开机自启"""
    svc = MosquittoService()
    svc.enable()


@service.command(name='disable')
def service_disable():
    """禁用开机自启"""
    svc = MosquittoService()
    svc.disable()


@cli.command()
@click.option('--host', default='localhost', show_default=True, help='MQTT服务器地址')
@click.option('--port', type=int, help='MQTT服务器端口 (默认自动检测本机配置或使用1883)')
@click.option('--username', help='MQTT用户名')
@click.option('--password', help='MQTT密码 (不提供则交互式输入)')
@click.option('--topic', default='$SYS/#', show_default=True, help='订阅主题')
def monitor(host, port, username, password, topic):
    """监控MQTT服务器"""
    mon = MosquittoMonitor()
    mon.monitor(host=host, port=port, username=username, password=password, topic=topic)


@cli.command()
@click.option('--follow', '-f', is_flag=True, help='实时跟踪日志')
@click.option('--lines', '-n', type=int, default=50, help='显示行数 (默认: 50)')
def logs(follow, lines):
    """查看服务日志"""
    log_viewer = MosquittoLogs()
    log_viewer.show_logs(follow=follow, lines=lines)


if __name__ == '__main__':
    cli()
