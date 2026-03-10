#!/usr/bin/env python3
"""
MQTT管理工具测试脚本
覆盖关键命令构建、配置生成和CLI参数校验逻辑
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import click
from click.testing import CliRunner

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent))

import mqtt_manager
from mqtt_manager import CommandExecutor, MosquittoConfig, MosquittoMonitor, MosquittoUserManager, cli


class MQTTManagerTestCase(unittest.TestCase):
    """MQTT管理工具行为测试"""

    def setUp(self):
        """准备临时配置环境"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.original_conf = mqtt_manager.MOSQUITTO_CONF
        self.original_passwd = mqtt_manager.MOSQUITTO_PASSWD_FILE

        mqtt_manager.MOSQUITTO_CONF = str(self.temp_path / "mosquitto.conf")
        mqtt_manager.MOSQUITTO_PASSWD_FILE = str(self.temp_path / "passwd")

    def tearDown(self):
        """恢复原始配置"""
        mqtt_manager.MOSQUITTO_CONF = self.original_conf
        mqtt_manager.MOSQUITTO_PASSWD_FILE = self.original_passwd
        self.temp_dir.cleanup()

    def test_command_executor_runs_simple_command(self):
        """命令执行器应返回子进程输出"""
        result = CommandExecutor.run([sys.executable, "-c", "print('test')"])
        self.assertEqual(result.stdout.strip(), "test")

    def test_generate_config_creates_password_file_for_authenticated_mode(self):
        """禁用匿名访问时应确保密码文件存在"""
        config_mgr = MosquittoConfig()

        with patch.object(CommandExecutor, "check_root", return_value=None):
            config_mgr.generate_config(allow_anonymous=False, persistence=True, websocket=False)

        passwd_path = Path(mqtt_manager.MOSQUITTO_PASSWD_FILE)
        conf_path = Path(mqtt_manager.MOSQUITTO_CONF)

        self.assertTrue(passwd_path.exists())
        self.assertIn("password_file", conf_path.read_text(encoding="utf-8"))

    def test_generate_config_secures_password_file_permissions(self):
        """生成鉴权配置时应收紧密码文件权限"""
        config_mgr = MosquittoConfig()

        with patch.object(CommandExecutor, "check_root", return_value=None), \
             patch("mqtt_manager.pwd.getpwnam", return_value=SimpleNamespace(pw_uid=1883)), \
             patch("mqtt_manager.grp.getgrnam", return_value=SimpleNamespace(gr_gid=1883)), \
             patch("mqtt_manager.os.chown") as mocked_chown:
            config_mgr.generate_config(allow_anonymous=False, persistence=True, websocket=False)

        passwd_path = Path(mqtt_manager.MOSQUITTO_PASSWD_FILE)
        self.assertEqual(passwd_path.stat().st_mode & 0o777, 0o700)
        mocked_chown.assert_called_with(passwd_path, 1883, 1883)

    def test_command_executor_reports_missing_system_command(self):
        """缺少系统命令时应返回友好错误"""
        with self.assertRaises(click.ClickException) as ctx:
            CommandExecutor.run(["definitely_missing_binary_for_test"])

        self.assertIn("未找到系统命令", str(ctx.exception))

    def test_add_user_uses_create_flag_for_first_user(self):
        """首次添加用户时应使用-c创建密码文件"""
        user_mgr = MosquittoUserManager()

        with patch.object(CommandExecutor, "check_root", return_value=None), \
             patch.object(CommandExecutor, "run") as mocked_run:
            user_mgr.add_user("alice", "secret")

        mocked_run.assert_called_once()
        cmd = mocked_run.call_args.args[0]
        self.assertEqual(cmd[:2], ["mosquitto_passwd", "-b"])
        self.assertIn("-c", cmd)
        self.assertEqual(cmd[-3:], [mqtt_manager.MOSQUITTO_PASSWD_FILE, "alice", "secret"])

    def test_add_user_uses_create_flag_when_password_file_is_empty(self):
        """空密码文件场景下首次添加用户仍应传递-c"""
        Path(mqtt_manager.MOSQUITTO_PASSWD_FILE).touch()
        user_mgr = MosquittoUserManager()

        with patch.object(CommandExecutor, "check_root", return_value=None), \
             patch.object(CommandExecutor, "run") as mocked_run:
            user_mgr.add_user("alice", "secret")

        cmd = mocked_run.call_args.args[0]
        self.assertIn("-c", cmd)

    def test_add_user_skips_create_flag_when_password_file_has_content(self):
        """已有非空密码文件时不应重复传递-c"""
        Path(mqtt_manager.MOSQUITTO_PASSWD_FILE).write_text(
            "alice:$7$existing-hash\n",
            encoding="utf-8"
        )
        user_mgr = MosquittoUserManager()

        with patch.object(CommandExecutor, "check_root", return_value=None), \
             patch.object(CommandExecutor, "run") as mocked_run:
            user_mgr.add_user("bob", "secret")

        cmd = mocked_run.call_args.args[0]
        self.assertNotIn("-c", cmd)

    def test_add_user_secures_password_file_after_update(self):
        """添加用户后应收紧密码文件权限"""
        user_mgr = MosquittoUserManager()
        passwd_path = Path(mqtt_manager.MOSQUITTO_PASSWD_FILE)

        def create_password_file(_cmd):
            passwd_path.write_text("alice:$7$new-hash\n", encoding="utf-8")

        with patch.object(CommandExecutor, "check_root", return_value=None), \
             patch.object(CommandExecutor, "run", side_effect=create_password_file), \
             patch("mqtt_manager.pwd.getpwnam", return_value=SimpleNamespace(pw_uid=1883)), \
             patch("mqtt_manager.grp.getgrnam", return_value=SimpleNamespace(gr_gid=1883)), \
             patch("mqtt_manager.os.chown") as mocked_chown:
            user_mgr.add_user("alice", "secret")

        self.assertEqual(passwd_path.stat().st_mode & 0o777, 0o700)
        mocked_chown.assert_called_with(passwd_path, 1883, 1883)

    def test_add_user_prints_service_reload_hint(self):
        """添加用户后应提示重新加载或重启服务"""
        user_mgr = MosquittoUserManager()

        with patch.object(CommandExecutor, "check_root", return_value=None), \
             patch.object(CommandExecutor, "run"), \
             patch("click.echo") as mocked_echo:
            user_mgr.add_user("alice", "secret")

        echoed_messages = [call.args[0] for call in mocked_echo.call_args_list if call.args]
        self.assertTrue(any("重新加载或重启 mosquitto 服务" in msg for msg in echoed_messages))
        self.assertTrue(any("service restart" in msg for msg in echoed_messages))

    def test_delete_user_prints_service_reload_hint(self):
        """删除用户后应提示重新加载或重启服务"""
        Path(mqtt_manager.MOSQUITTO_PASSWD_FILE).write_text(
            "alice:$7$existing-hash\n",
            encoding="utf-8"
        )
        user_mgr = MosquittoUserManager()

        with patch.object(CommandExecutor, "check_root", return_value=None), \
             patch.object(CommandExecutor, "run"), \
             patch("click.echo") as mocked_echo:
            user_mgr.delete_user("alice")

        echoed_messages = [call.args[0] for call in mocked_echo.call_args_list if call.args]
        self.assertTrue(any("重新加载或重启 mosquitto 服务" in msg for msg in echoed_messages))
        self.assertTrue(any("service restart" in msg for msg in echoed_messages))

    def test_monitor_uses_detected_port_and_credentials(self):
        """监控命令应带上检测出的端口和鉴权参数"""
        Path(mqtt_manager.MOSQUITTO_CONF).write_text(
            "listener 1884\nallow_anonymous false\n",
            encoding="utf-8"
        )
        monitor = MosquittoMonitor()

        with patch("mqtt_manager.subprocess.run") as mocked_run:
            monitor.monitor(username="alice", password="secret")

        mocked_run.assert_called_once()
        cmd = mocked_run.call_args.args[0]
        self.assertEqual(
            cmd,
            ["mosquitto_sub", "-h", "localhost", "-p", "1884", "-v", "-t", "$SYS/#", "-u", "alice", "-P", "secret"]
        )

    def test_monitor_reports_missing_mosquitto_sub(self):
        """缺少mosquitto_sub时应返回友好错误"""
        monitor = MosquittoMonitor()

        with patch("mqtt_manager.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(click.ClickException) as ctx:
                monitor.monitor(host="example.com")

        self.assertIn("mosquitto_sub", str(ctx.exception))

    def test_monitor_requires_credentials_for_local_authenticated_broker(self):
        """本机关闭匿名访问时应拒绝无凭据监控"""
        Path(mqtt_manager.MOSQUITTO_CONF).write_text(
            "listener 1884\nallow_anonymous false\n",
            encoding="utf-8"
        )
        monitor = MosquittoMonitor()

        with self.assertRaisesRegex(Exception, "禁止匿名访问"):
            monitor.monitor()

    def test_cli_rejects_conflicting_anonymous_flags(self):
        """CLI应拒绝互斥匿名参数"""
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "generate", "--allow-anonymous", "--no-anonymous"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("不能同时使用", result.output)

    def test_cli_rejects_conflicting_persistence_flags(self):
        """CLI应拒绝互斥持久化参数"""
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "generate", "--persistence", "--no-persistence"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("不能同时使用", result.output)


if __name__ == '__main__':
    unittest.main(verbosity=2)
