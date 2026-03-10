#!/usr/bin/env python3
"""
MQTT外网测试工具单元测试
"""

import subprocess
import unittest
from unittest.mock import patch

from click.testing import CliRunner

import mqtt_external_test
from mqtt_external_test import (
    EXIT_AUTH_ERROR,
    EXIT_CONNECTIVITY_ERROR,
    EXIT_DEPENDENCY_ERROR,
    EXIT_MESSAGE_ERROR,
    EXIT_PUBLISH_ERROR,
    EXIT_SUCCESS,
    EXIT_TIMEOUT_ERROR,
    ExternalMQTTTester,
    TestResult,
    cli,
)


class FakeProcess:
    """用于模拟订阅子进程"""

    def __init__(
        self,
        poll_values=None,
        communicate_result=("", ""),
        returncode=0,
        communicate_side_effect=None
    ):
        self.poll_values = list(poll_values or [None])
        self.default_poll = self.poll_values[-1] if self.poll_values else returncode
        self.communicate_result = communicate_result
        self.returncode = returncode
        self.communicate_side_effect = communicate_side_effect
        self.terminated = False
        self.killed = False
        self.communicate_calls = []

    def poll(self):
        if self.poll_values:
            return self.poll_values.pop(0)
        if self.terminated or self.killed:
            return self.returncode
        return self.default_poll

    def communicate(self, timeout=None):
        self.communicate_calls.append(timeout)
        if self.communicate_side_effect and not (self.terminated or self.killed):
            raise self.communicate_side_effect
        return self.communicate_result

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


class ExternalMQTTTesterTestCase(unittest.TestCase):
    """外网测试器行为测试"""

    def setUp(self):
        self.tester = ExternalMQTTTester()

    def test_build_subscribe_command_includes_auth_options(self):
        """订阅命令应包含认证参数和单条消息退出控制"""
        cmd = self.tester.build_subscribe_command(
            host="broker.example.com",
            port=1884,
            topic="test/topic",
            username="alice",
            password="secret"
        )

        self.assertEqual(
            cmd,
            [
                "mosquitto_sub",
                "-h",
                "broker.example.com",
                "-p",
                "1884",
                "-C",
                "1",
                "-v",
                "-t",
                "test/topic",
                "-u",
                "alice",
                "-P",
                "secret"
            ]
        )

    def test_build_publish_command_skips_auth_for_anonymous(self):
        """匿名模式下发布命令不应携带认证参数"""
        cmd = self.tester.build_publish_command(
            host="broker.example.com",
            port=1883,
            topic="test/topic",
            message="hello"
        )

        self.assertEqual(
            cmd,
            [
                "mosquitto_pub",
                "-h",
                "broker.example.com",
                "-p",
                "1883",
                "-t",
                "test/topic",
                "-m",
                "hello"
            ]
        )

    def test_run_loopback_test_succeeds_when_message_matches(self):
        """订阅收到匹配的回环消息时应返回成功"""
        subscriber = FakeProcess(
            poll_values=[None],
            communicate_result=("test/topic hello", ""),
            returncode=0
        )
        publish_result = subprocess.CompletedProcess(args=["mosquitto_pub"], returncode=0, stdout="", stderr="")

        with patch("mqtt_external_test.time.sleep"), \
             patch("mqtt_external_test.subprocess.Popen", return_value=subscriber) as mocked_popen, \
             patch("mqtt_external_test.subprocess.run", return_value=publish_result) as mocked_run:
            result = self.tester.run_loopback_test(
                host="broker.example.com",
                port=1883,
                topic="test/topic",
                message="hello",
                username="alice",
                password="secret",
                timeout=5,
                startup_wait=0
            )

        self.assertTrue(result.success)
        self.assertEqual(result.exit_code, EXIT_SUCCESS)
        mocked_popen.assert_called_once()
        mocked_run.assert_called_once_with(
            [
                "mosquitto_pub",
                "-h",
                "broker.example.com",
                "-p",
                "1883",
                "-t",
                "test/topic",
                "-m",
                "hello",
                "-u",
                "alice",
                "-P",
                "secret"
            ],
            capture_output=True,
            text=True,
            timeout=5
        )

    def test_run_loopback_test_reports_missing_subscriber_binary(self):
        """缺少 mosquitto_sub 时应提示安装客户端"""
        with patch("mqtt_external_test.subprocess.Popen", side_effect=FileNotFoundError):
            result = self.tester.run_loopback_test(
                host="broker.example.com",
                port=1883,
                topic="test/topic",
                message="hello"
            )

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, EXIT_DEPENDENCY_ERROR)
        self.assertIn("mosquitto_sub", result.message)

    def test_run_loopback_test_reports_auth_failure_on_subscriber_startup(self):
        """订阅启动即鉴权失败时应返回认证错误"""
        subscriber = FakeProcess(
            poll_values=[1],
            communicate_result=("", "Connection Refused: not authorised."),
            returncode=1
        )

        with patch("mqtt_external_test.time.sleep"), \
             patch("mqtt_external_test.subprocess.Popen", return_value=subscriber):
            result = self.tester.run_loopback_test(
                host="broker.example.com",
                port=1883,
                topic="test/topic",
                message="hello",
                username="alice",
                password="wrong",
                startup_wait=0
            )

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, EXIT_AUTH_ERROR)
        self.assertIn("认证失败", result.message)

    def test_run_loopback_test_reports_publish_failure_and_cleans_up(self):
        """发布失败时应返回错误并终止订阅进程"""
        subscriber = FakeProcess(poll_values=[None], communicate_result=("", ""), returncode=0)
        publish_result = subprocess.CompletedProcess(
            args=["mosquitto_pub"],
            returncode=1,
            stdout="",
            stderr="Error: Connection refused"
        )

        with patch("mqtt_external_test.time.sleep"), \
             patch("mqtt_external_test.subprocess.Popen", return_value=subscriber), \
             patch("mqtt_external_test.subprocess.run", return_value=publish_result):
            result = self.tester.run_loopback_test(
                host="broker.example.com",
                port=1883,
                topic="test/topic",
                message="hello",
                timeout=5,
                startup_wait=0
            )

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, EXIT_CONNECTIVITY_ERROR)
        self.assertTrue(subscriber.terminated)

    def test_run_loopback_test_reports_timeout_and_cleans_up_process(self):
        """超时未收到消息时应返回超时错误并回收进程"""
        subscriber = FakeProcess(
            poll_values=[None, None],
            communicate_result=("", ""),
            returncode=0,
            communicate_side_effect=subprocess.TimeoutExpired(cmd="mosquitto_sub", timeout=5)
        )
        publish_result = subprocess.CompletedProcess(args=["mosquitto_pub"], returncode=0, stdout="", stderr="")

        with patch("mqtt_external_test.time.sleep"), \
             patch("mqtt_external_test.subprocess.Popen", return_value=subscriber), \
             patch("mqtt_external_test.subprocess.run", return_value=publish_result):
            result = self.tester.run_loopback_test(
                host="broker.example.com",
                port=1883,
                topic="test/topic",
                message="hello",
                timeout=5,
                startup_wait=0
            )

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, EXIT_TIMEOUT_ERROR)
        self.assertTrue(subscriber.terminated)

    def test_run_loopback_test_reports_message_mismatch(self):
        """收到其他消息时应返回不匹配错误"""
        subscriber = FakeProcess(
            poll_values=[None],
            communicate_result=("test/topic unexpected", ""),
            returncode=0
        )
        publish_result = subprocess.CompletedProcess(args=["mosquitto_pub"], returncode=0, stdout="", stderr="")

        with patch("mqtt_external_test.time.sleep"), \
             patch("mqtt_external_test.subprocess.Popen", return_value=subscriber), \
             patch("mqtt_external_test.subprocess.run", return_value=publish_result):
            result = self.tester.run_loopback_test(
                host="broker.example.com",
                port=1883,
                topic="test/topic",
                message="hello",
                timeout=5,
                startup_wait=0
            )

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, EXIT_MESSAGE_ERROR)
        self.assertIn("不匹配", result.message)


class ExternalMQTTCLITestCase(unittest.TestCase):
    """外网测试工具CLI测试"""

    def test_cli_prompts_for_password_when_username_is_provided(self):
        """提供用户名但未给密码时应交互获取密码"""
        runner = CliRunner()
        success_result = TestResult(
            success=True,
            exit_code=EXIT_SUCCESS,
            message="ok",
            details="topic message"
        )

        with patch("mqtt_external_test.getpass.getpass", return_value="secret") as mocked_getpass, \
             patch("mqtt_external_test.generate_default_topic", return_value="generated/topic"), \
             patch("mqtt_external_test.generate_default_message", return_value="generated-message"), \
             patch.object(ExternalMQTTTester, "run_loopback_test", return_value=success_result) as mocked_run:
            result = runner.invoke(cli, ["--host", "broker.example.com", "--username", "alice"])

        self.assertEqual(result.exit_code, EXIT_SUCCESS)
        mocked_getpass.assert_called_once_with("请输入用户 alice 的密码: ")
        mocked_run.assert_called_once_with(
            host="broker.example.com",
            port=1883,
            topic="generated/topic",
            message="generated-message",
            username="alice",
            password="secret",
            timeout=5
        )

    def test_cli_uses_generated_defaults_and_returns_failure_code(self):
        """CLI应使用默认主题和消息，并透传失败退出码"""
        runner = CliRunner()
        failure_result = TestResult(
            success=False,
            exit_code=EXIT_PUBLISH_ERROR,
            message="发布测试消息失败",
            details="publish error"
        )

        with patch("mqtt_external_test.generate_default_topic", return_value="generated/topic"), \
             patch("mqtt_external_test.generate_default_message", return_value="generated-message"), \
             patch.object(ExternalMQTTTester, "run_loopback_test", return_value=failure_result):
            result = runner.invoke(cli, ["--host", "broker.example.com"])

        self.assertEqual(result.exit_code, EXIT_PUBLISH_ERROR)
        self.assertIn("测试失败", result.output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
