# MQTT服务器自动化部署和管理工具

用于Ubuntu平台下Mosquitto MQTT服务器的自动化安装、配置和管理的Python工具。

## 功能特性

- 自动化安装Mosquitto MQTT服务器
- 配置文件生成和管理
- 用户和权限管理
- 服务启动/停止/重启控制
- 实时监控和日志查看
- 支持WebSocket协议

## 系统要求

- Ubuntu 18.04或更高版本
- Python 3.6+
- sudo权限

## 安装

1. 安装Python依赖:

```bash
pip3 install -r requirements.txt
```

2. 赋予脚本执行权限:

```bash
chmod +x mqtt_manager.py
```

### Windows安装 mosquitto-clients

如果需要在Windows机器上使用 `mqtt_external_test.py` 进行公网连通性验证，先安装 Mosquitto 客户端工具。

方法一：使用官方安装包

1. 打开下载页: `https://mosquitto.org/download/`
2. 下载 Windows 安装包并完成安装
3. 确认安装目录已加入 `Path`，常见目录为 `C:\Program Files\mosquitto\`
4. 打开新的 PowerShell 或 cmd 验证:

```powershell
mosquitto_pub --help
mosquitto_sub --help
```

方法二：使用 `winget`

```powershell
winget search mosquitto
winget install EclipseMosquitto.Mosquitto
```

安装完成后验证:

```powershell
mosquitto_pub --help
mosquitto_sub --help
```

如果提示找不到命令，可以直接使用安装目录中的可执行文件，或将 `C:\Program Files\mosquitto\` 手动加入系统环境变量 `Path`。

## 使用方法

### 安装Mosquitto

```bash
sudo ./mqtt_manager.py install
```

### 配置管理

生成默认配置:

```bash
sudo ./mqtt_manager.py config generate
```

自定义配置:

```bash
# 指定端口
sudo ./mqtt_manager.py config generate --port 1884

# 允许匿名连接
sudo ./mqtt_manager.py config generate --allow-anonymous

# 禁止匿名连接
sudo ./mqtt_manager.py config generate --no-anonymous

# 启用WebSocket支持
sudo ./mqtt_manager.py config generate --websocket

# 组合使用
sudo ./mqtt_manager.py config generate --port 1884 --no-anonymous --websocket
```

### 用户管理

添加用户:

```bash
# 交互式输入密码
sudo ./mqtt_manager.py user add username

# 命令行指定密码
sudo ./mqtt_manager.py user add username --password mypassword
```

删除用户:

```bash
sudo ./mqtt_manager.py user delete username
```

列出所有用户:

```bash
sudo ./mqtt_manager.py user list
```

### 服务控制

启动服务:

```bash
sudo ./mqtt_manager.py service start
```

停止服务:

```bash
sudo ./mqtt_manager.py service stop
```

重启服务:

```bash
sudo ./mqtt_manager.py service restart
```

查看服务状态:

```bash
sudo ./mqtt_manager.py service status
```

启用开机自启:

```bash
sudo ./mqtt_manager.py service enable
```

禁用开机自启:

```bash
sudo ./mqtt_manager.py service disable
```

### 监控服务

实时监控MQTT服务器(订阅$SYS主题):

```bash
./mqtt_manager.py monitor
```

监控带认证或非默认端口的服务:

```bash
./mqtt_manager.py monitor --host localhost --port 1884 --username testuser
```

### 外网回环测试

从外网机器验证 MQTT 服务是否可达、可认证、可收发消息:

```bash
python3 mqtt_external_test.py --host mqtt.example.com
```

使用用户名密码测试公网访问:

```bash
python3 mqtt_external_test.py --host mqtt.example.com --port 1883 --username testuser
```

指定主题、消息和超时时间:

```bash
python3 mqtt_external_test.py \
  --host mqtt.example.com \
  --topic ops/healthcheck \
  --message "hello from internet" \
  --timeout 8
```

说明:
该工具会先订阅测试主题，再发布一条唯一测试消息，并在超时时间内确认是否收到回环消息。
建议在外网机器上执行，用于验证公网地址、端口放通和鉴权配置是否正常。

### 查看日志

查看最近50行日志:

```bash
./mqtt_manager.py logs
```

查看最近100行日志:

```bash
./mqtt_manager.py logs --lines 100
```

实时跟踪日志:

```bash
./mqtt_manager.py logs --follow
```

## 典型工作流程

1. 安装Mosquitto:

```bash
sudo ./mqtt_manager.py install
```

2. 生成配置(禁止匿名访问):

```bash
sudo ./mqtt_manager.py config generate --no-anonymous
```

说明:
工具会自动创建空的 `/etc/mosquitto/passwd`，确保服务在添加首个用户前也能正常加载配置。

3. 添加用户:

```bash
sudo ./mqtt_manager.py user add testuser
```

4. 重启服务使配置生效:

```bash
sudo ./mqtt_manager.py service restart
```

5. 测试连接:

```bash
# 订阅主题
mosquitto_sub -h localhost -t test/topic -u testuser -P password

# 发布消息
mosquitto_pub -h localhost -t test/topic -m "Hello MQTT" -u testuser -P password
```

6. 监控服务:

```bash
./mqtt_manager.py monitor
```

## 配置文件

配置文件位置: `/etc/mosquitto/mosquitto.conf`

密码文件位置: `/etc/mosquitto/passwd`

每次修改配置后需要重启服务使配置生效。

## 注意事项

- 大部分操作需要root权限,请使用sudo运行
- 修改配置前会自动备份原配置文件到 `/etc/mosquitto/mosquitto.conf.backup`
- 用户密码使用mosquitto_passwd工具加密存储,不会明文保存
- 监控功能需要mosquitto服务正在运行
- 当本机配置禁止匿名访问时, `monitor` 命令需要提供 `--username`，密码可通过 `--password` 指定或交互式输入

## 故障排查

### 服务无法启动

1. 检查配置文件语法:

```bash
mosquitto -c /etc/mosquitto/mosquitto.conf -v
```

2. 查看详细日志:

```bash
./mqtt_manager.py logs --lines 100
```

### 客户端无法连接

1. 检查防火墙设置:

```bash
sudo ufw status
sudo ufw allow 1883/tcp
```

2. 检查服务状态:

```bash
sudo ./mqtt_manager.py service status
```

3. 验证用户名密码是否正确

## 许可证

本工具为内部使用工具,配合STM32G474等嵌入式设备进行MQTT通信测试和开发。
