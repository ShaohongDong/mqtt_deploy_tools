#!/usr/bin/env python3
"""
MQTT外网连通性测试工具
通过订阅和发布同一测试主题，验证MQTT服务的公网可达性与消息收发能力
"""

import getpass
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import click

DEFAULT_PORT = 1883
DEFAULT_TIMEOUT = 5
DEFAULT_STARTUP_WAIT = 1.0

EXIT_SUCCESS = 0
EXIT_CONNECTIVITY_ERROR = 1
EXIT_DEPENDENCY_ERROR = 2
EXIT_AUTH_ERROR = 3
EXIT_PUBLISH_ERROR = 4
EXIT_TIMEOUT_ERROR = 5
EXIT_MESSAGE_ERROR = 6


@dataclass
class TestResult:
    """测试结果"""

    success: bool
    exit_code: int
    message: str
    details: Optional[str] = None


class ExternalMQTTTester:
    """MQTT外网回环测试器"""

    def build_subscribe_command(
        self,
        host: str,
        port: int,
        topic: str,
        username: Optional[str] = None,
        password: Optional[str] = None
    ) -> List[str]:
        """构建订阅命令"""
        cmd = [
            "mosquitto_sub",
            "-h",
            host,
            "-p",
            str(port),
            "-C",
            "1",
            "-v",
            "-t",
            topic
        ]
        if username:
            cmd.extend(["-u", username])
        if password:
            cmd.extend(["-P", password])
        return cmd

    def build_publish_command(
        self,
        host: str,
        port: int,
        topic: str,
        message: str,
        username: Optional[str] = None,
        password: Optional[str] = None
    ) -> List[str]:
        """构建发布命令"""
        cmd = [
            "mosquitto_pub",
            "-h",
            host,
            "-p",
            str(port),
            "-t",
            topic,
            "-m",
            message
        ]
        if username:
            cmd.extend(["-u", username])
        if password:
            cmd.extend(["-P", password])
        return cmd

    def run_loopback_test(
        self,
        host: str,
        port: int,
        topic: str,
        message: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        startup_wait: float = DEFAULT_STARTUP_WAIT
    ) -> TestResult:
        """执行发布+订阅回环测试"""
        subscribe_cmd = self.build_subscribe_command(host, port, topic, username, password)
        publish_cmd = self.build_publish_command(host, port, topic, message, username, password)
        subscriber = None

        try:
            try:
                subscriber = subprocess.Popen(
                    subscribe_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
            except FileNotFoundError:
                return TestResult(
                    success=False,
                    exit_code=EXIT_DEPENDENCY_ERROR,
                    message="未找到系统命令: mosquitto_sub，请先安装 mosquitto-clients"
                )

            time.sleep(startup_wait)

            if subscriber.poll() is not None:
                stdout, stderr = subscriber.communicate()
                return self._build_process_error_result(
                    stderr=stderr,
                    stdout=stdout,
                    generic_message="订阅端启动失败",
                    generic_exit_code=EXIT_CONNECTIVITY_ERROR
                )

            try:
                publish_result = subprocess.run(
                    publish_cmd,
                    capture_output=True,
                    text=True,
                    timeout=max(timeout, 1)
                )
            except FileNotFoundError:
                return TestResult(
                    success=False,
                    exit_code=EXIT_DEPENDENCY_ERROR,
                    message="未找到系统命令: mosquitto_pub，请先安装 mosquitto-clients"
                )
            except subprocess.TimeoutExpired:
                return TestResult(
                    success=False,
                    exit_code=EXIT_PUBLISH_ERROR,
                    message="发布测试消息超时"
                )

            if publish_result.returncode != 0:
                return self._build_process_error_result(
                    stderr=publish_result.stderr,
                    stdout=publish_result.stdout,
                    generic_message="发布测试消息失败",
                    generic_exit_code=EXIT_PUBLISH_ERROR
                )

            try:
                stdout, stderr = subscriber.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._stop_process(subscriber)
                return TestResult(
                    success=False,
                    exit_code=EXIT_TIMEOUT_ERROR,
                    message=f"等待回环消息超时，{timeout} 秒内未收到测试消息"
                )

            if subscriber.returncode not in (0, None):
                return self._build_process_error_result(
                    stderr=stderr,
                    stdout=stdout,
                    generic_message="订阅端在等待消息时异常退出",
                    generic_exit_code=EXIT_CONNECTIVITY_ERROR
                )

            expected_output = f"{topic} {message}"
            normalized_output = stdout.strip()
            if expected_output not in normalized_output.splitlines():
                return TestResult(
                    success=False,
                    exit_code=EXIT_MESSAGE_ERROR,
                    message="已收到订阅输出，但测试消息不匹配",
                    details=normalized_output or self._extract_details(stderr)
                )

            return TestResult(
                success=True,
                exit_code=EXIT_SUCCESS,
                message="外网MQTT回环测试通过，已确认可成功订阅并收到发布消息",
                details=expected_output
            )
        finally:
            if subscriber is not None:
                self._stop_process(subscriber)

    def _build_process_error_result(
        self,
        stderr: Optional[str],
        stdout: Optional[str],
        generic_message: str,
        generic_exit_code: int
    ) -> TestResult:
        """根据标准输出和错误输出归类失败原因"""
        details = self._extract_details(stderr, stdout)
        lowered = details.lower()

        if self._is_auth_error(lowered):
            return TestResult(
                success=False,
                exit_code=EXIT_AUTH_ERROR,
                message="认证失败，请检查 MQTT 用户名和密码",
                details=details
            )

        if self._is_connectivity_error(lowered):
            return TestResult(
                success=False,
                exit_code=EXIT_CONNECTIVITY_ERROR,
                message="连接失败，请检查公网地址、端口和网络连通性",
                details=details
            )

        return TestResult(
            success=False,
            exit_code=generic_exit_code,
            message=generic_message,
            details=details
        )

    @staticmethod
    def _extract_details(stderr: Optional[str], stdout: Optional[str] = None) -> Optional[str]:
        """提取可读的错误详情"""
        for content in (stderr, stdout):
            if content and content.strip():
                return content.strip()
        return None

    @staticmethod
    def _is_auth_error(details: str) -> bool:
        """判断是否为认证失败"""
        keywords = [
            "not authorised",
            "not authorized",
            "bad user name or password",
            "bad username or password"
        ]
        return any(keyword in details for keyword in keywords)

    @staticmethod
    def _is_connectivity_error(details: str) -> bool:
        """判断是否为连接问题"""
        keywords = [
            "connection refused",
            "connection error",
            "network is unreachable",
            "host unreachable",
            "name or service not known",
            "temporary failure in name resolution",
            "unknown host",
            "no route to host",
            "server unavailable"
        ]
        return any(keyword in details for keyword in keywords)

    @staticmethod
    def _stop_process(process: subprocess.Popen):
        """确保订阅进程被回收"""
        if process.poll() is not None:
            try:
                process.communicate(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
            return

        process.terminate()
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


def generate_default_topic() -> str:
    """生成默认测试主题"""
    return f"codex/test/{uuid.uuid4().hex[:12]}"


def generate_default_message() -> str:
    """生成默认测试消息"""
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"mqtt-external-test-{timestamp}-{suffix}"


@click.command()
@click.option("--host", required=True, help="MQTT服务器公网地址或域名")
@click.option("--port", default=DEFAULT_PORT, show_default=True, type=click.IntRange(1, 65535), help="MQTT服务器端口")
@click.option("--username", help="MQTT用户名")
@click.option("--password", help="MQTT密码 (不提供则交互式输入)")
@click.option("--topic", help="测试主题 (默认自动生成独立测试主题)")
@click.option("--message", help="测试消息内容 (默认自动生成唯一消息)")
@click.option("--timeout", default=DEFAULT_TIMEOUT, show_default=True, type=click.IntRange(1, 300), help="等待测试消息回环的超时时间(秒)")
def cli(host, port, username, password, topic, message, timeout):
    """从外网环境验证 MQTT 服务可达性和消息收发能力"""
    if username and password is None:
        password = getpass.getpass(f"请输入用户 {username} 的密码: ")

    topic = topic or generate_default_topic()
    message = message or generate_default_message()

    click.echo("开始执行MQTT外网回环测试...")
    click.echo(f"连接目标: {host}:{port}")
    click.echo(f"测试主题: {topic}")
    click.echo(f"超时时间: {timeout} 秒")

    tester = ExternalMQTTTester()
    result = tester.run_loopback_test(
        host=host,
        port=port,
        topic=topic,
        message=message,
        username=username,
        password=password,
        timeout=timeout
    )

    if result.success:
        click.echo(result.message)
        click.echo(f"回环消息: {result.details}")
        sys.exit(EXIT_SUCCESS)

    click.echo(f"测试失败: {result.message}", err=True)
    if result.details:
        click.echo(f"详细信息: {result.details}", err=True)
    sys.exit(result.exit_code)


if __name__ == "__main__":
    cli()
