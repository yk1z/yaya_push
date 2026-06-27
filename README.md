# 简介
通过 [Qmsg](https://github.com/1244453393/QmsgNtClient-NapCatQQ) 推送成员口袋房间消息到QQ

## 免责声明
本项目为Python学习交流的开源非营利项目，仅作相互学习交流之用。

严禁用于商业用途，禁止使用本项目进行任何盈利活动。

## 使用教程
### 1. 基础环境安装
根据系统自动选择安装 Docker

```bash
sudo yum install -y docker wget unzip || (sudo apt update && sudo apt install -y docker.io wget unzip)
sudo systemctl enable --now docker
sudo docker --version
```

### 2. 下载并解压源码

```bash
cd ~
wget -O qmsgnt.zip https://github.com/1244453393/QmsgNtClient-NapCatQQ/releases/download/v1.0.23/QmsgNtClient-NapCatQQ-Linux-Docker_amd64.zip
unzip qmsgnt.zip -d qmsgnt
cd qmsgnt
```

### 3. 修复 Dockerfile 路径并编译镜像

原项目 Dockerfile 中的解压路径与常规不符，直接在此处进行修复并完成构建：

```bash
sudo docker rm -f qmsgnt 2>/dev/null

sed -i 's#-d ./QmsgNtClient-NapCatQQ#-d /usr/src/app#' Dockerfile
sed -i 's#-d /tmp/QmsgNtClient-NapCatQQ#-d /tmp#' Dockerfile

mkdir -p QQ config logs

sudo docker build --no-cache -t qmsgnt -f Dockerfile .
```

### 4. 运行容器（请替换你的 QQ 号）

将下方命令中的 `123456789` 替换为你要登录的机器人 QQ 号：

```bash
sudo docker run --restart=always -d --name qmsgnt \
  -e ACCOUNT=123456789 \
  -p 6099:6099 \
  -v ${PWD}/QQ:/root/.config/QQ \
  -v ${PWD}/config:/usr/src/app/config \
  -v ${PWD}/logs:/usr/src/app/logs \
  qmsgnt
```

### 5. 获取 Token 与 WebUI 登录

运行容器后，执行以下命令查看日志，获取用于登录 WebUI 的 Token：

```bash
sudo docker logs qmsgnt | grep -i token
```

### 6. 打开浏览器访问

```text
http://你的服务器公网IP:6099/webui
```

输入刚才获取到的 Token，即可进入 WebUI 并登录。

### 7. 后台运行推送脚本

确保你的 `push.py` 脚本在当前目录下，然后使用 `nohup` 挂起：

```bash
nohup python3 push.py > push.log 2>&1 &
tail -f push.log
```
