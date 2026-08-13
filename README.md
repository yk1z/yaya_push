# 简介

通过 NapCat 的 OneBot HTTP 接口推送成员口袋房间消息到 QQ。

## 免责声明

本项目为 Python 学习交流的开源非营利项目，仅作相互学习交流之用。

严禁用于商业用途，禁止使用本项目进行任何盈利活动。

## 使用教程

### NapCat 部分

### 1. 准备 NapCat

先准备一个已经登录机器人 QQ 的 NapCat。可以使用你现有的 NapCat WebUI，也可以按 NapCat 官方方式部署。

进入 NapCat WebUI 后，在左侧打开 `网络配置`，添加或启用一个 `HTTP Server`：

```text
启用: 开
Host: 127.0.0.1
Port: 3000
消息格式: Array
Token: 自己设置一个 access_token，或仅本机使用时留空
```

如果牙牙推送脚本和 NapCat 不在同一台机器，Host 可改为 `0.0.0.0`，并且必须用防火墙只允许牙牙推送服务器访问 3000 端口。

### 2. 测试 NapCat 接口

有 Token 时：

```bash
curl -X POST \
  -H "Authorization: Bearer 你的NapCatToken" \
  -H "Content-Type: application/json" \
  -d "{}" \
  http://127.0.0.1:3000/get_status
```

无 Token 时：

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d "{}" \
  http://127.0.0.1:3000/get_status
```

返回 JSON 且 `retcode` 为 `0`，说明 NapCat HTTP 接口可用。

### 牙牙推送部分

### 1. 上传 push.py

创建 yaya_push 文件夹：

```bash
mkdir -p ~/yaya_push
```

将 `push.py` 上传到 `~/yaya_push` 文件夹中。

### 2. 运行 push.py

```bash
cd ~/yaya_push
python3 push.py
```

运行后输入 `5` 配置密钥：

```text
NapCat HTTP API: http://127.0.0.1:3000
NapCat Access Token: 你的 NapCat Token，未设置则留空
口袋账号Token: 你的口袋账号 Token
```

配置完成后输入 `6` 启动后台推送。

启动后可使用菜单配置需要推送的成员。

### 3. 测试推送

推送每个已配置成员目标房间的最新 1 条消息：

```bash
cd ~/yaya_push
python3 push.py --test --test-limit 1
```
