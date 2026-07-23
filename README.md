# 简介
通过 [Qmsg](https://github.com/1244453393/QmsgNtClient-NapCatQQ) 推送成员口袋房间消息到QQ

## 免责声明
本项目为Python学习交流的开源非营利项目，仅作相互学习交流之用。

严禁用于商业用途，禁止使用本项目进行任何盈利活动。

## 使用教程

### Qmsg部分

### 1. 安装Docker

```bash
sudo yum install -y docker wget unzip || (sudo apt update && sudo apt install -y docker.io wget unzip)
sudo systemctl enable --now docker
```

如果国内服务器拉取镜像超时，可配置镜像加速
```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json > /dev/null <<EOF
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1panel.live"
  ]
}
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker
sudo docker --version
```


### 2. 下载并解压

```bash
cd ~
wget -O qmsgnt.zip https://ghproxy.net/https://github.com/1244453393/QmsgNtClient-NapCatQQ/releases/download/v1.0.23/QmsgNtClient-NapCatQQ-Linux-Docker_amd64.zip
unzip qmsgnt.zip
cd ~/qmsgnt
```

### 3. 修改配置

将下方命令中的 `2187195199` 替换为你要登录的机器人QQ

```bash
sed -i 's/^WEBUI_PORT=.*/WEBUI_PORT=6099/' qmsgnt_install.sh
sed -i 's/^ACCOUNT=.*/ACCOUNT=2187195199/' qmsgnt_install.sh
chmod +x *.sh
```

### 4. 安装依赖

安装完成后需要去防火墙中放行6099端口

```bash
sudo ./qmsgnt_install.sh
```

### 5. 修复路径

```bash
sudo docker rm -f qmsgnt 2>/dev/null

sed -i 's#-d ./QmsgNtClient-NapCatQQ#-d /usr/src/app#' Dockerfile
sed -i 's#-d /tmp/QmsgNtClient-NapCatQQ#-d /tmp#' Dockerfile

sudo docker build --no-cache -t qmsgnt -f Dockerfile .
```


### 6. 启动容器

将下方命令中的 `2187195199` 替换为你要登录的机器人QQ

```bash
mkdir -p QQ config logs

sudo docker run --restart=always -d \
  --name qmsgnt \
  -e ACCOUNT=2187195199 \
  -p 6099:6099 \
  -v /home/admin/qmsgnt/QQ:/root/.config/QQ \
  -v /home/admin/qmsgnt/config:/usr/src/app/QmsgNtClient-NapCatQQ/config \
  -v /home/admin/qmsgnt/logs:/usr/src/app/QmsgNtClient-NapCatQQ/logs \
  qmsgnt
```


### 7. 登录WebUI

获取登录Token

```bash
sudo docker logs qmsgnt | grep -i token
```

进入WebUI登录

```text
http://你的服务器公网IP:6099/webui
```

输入刚才设置的Token，即可进入WebUI并登录

登录后在插件管理中启用QmsgNtClient，并在插件配置中输入Qmsg Key

---

### 牙牙推送部分

### 1. 上传push.py

创建 yaya_push 文件夹
```bash
mkdir -p ~/yaya_push
```
将文件上传到 yaya_push 文件夹中


### 2. 运行push.py

```bash
cd ~/yaya_push
python3 push.py
```

<img width="343" height="301" alt="ScreenShot_2026-07-23_190502_441" src="https://github.com/user-attachments/assets/07a6ad16-530f-4f69-8c02-824e0e6acf89" />

运行后输入 5 配置Qmsg KEY和口袋账号Token

配置完成后输入 6 启动后台推送

启动后可使用菜单配置需要推送的成员
