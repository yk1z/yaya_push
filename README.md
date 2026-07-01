# 简介
通过 [Qmsg](https://github.com/1244453393/QmsgNtClient-NapCatQQ) 推送成员口袋房间消息到QQ

## 免责声明
本项目为Python学习交流的开源非营利项目，仅作相互学习交流之用。

严禁用于商业用途，禁止使用本项目进行任何盈利活动。

## 使用教程
### 1. 安装Docker

```bash
sudo yum install -y docker wget unzip || (sudo apt update && sudo apt install -y docker.io wget unzip)
sudo systemctl enable --now docker
sudo docker --version
```

### 2. 下载并解压

```bash
cd ~
wget -O qmsgnt.zip https://github.com/1244453393/QmsgNtClient-NapCatQQ/releases/download/v1.0.23/QmsgNtClient-NapCatQQ-Linux-Docker_amd64.zip
unzip qmsgnt.zip -d qmsgnt
cd ~/qmsgnt/qmsgnt
```

### 3. 修改配置

将下方命令中的 `2187195199` 替换为你要登录的机器人QQ

```bash
sed -i 's/^WEBUI_PORT=.*/WEBUI_PORT=6099/' qmsgnt_install.sh
sed -i 's/^ACCOUNT=.*/ACCOUNT=2187195199/' qmsgnt_install.sh
chmod +x *.sh
```

### 4. 安装依赖

安装完成后需要去安全组中放行6099端口

```bash
sudo ./qmsgnt_install.sh
```

### 5. 修复路径并设置Token

```bash
sudo docker rm -f qmsgnt 2>/dev/null

sed -i 's#-d ./QmsgNtClient-NapCatQQ#-d /usr/src/app#' Dockerfile
sed -i 's#-d /tmp/QmsgNtClient-NapCatQQ#-d /tmp#' Dockerfile

sudo docker build --no-cache -t qmsgnt -f Dockerfile .
```


### 6. 启动容器

将下方命令中的 `2187195199` 替换为你要登录的机器人QQ

```bash
mkdir -p ../QQ ../config ../logs

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

### 8. 配置并上传push.py

配置完 push.py 后，将文件上传到 /home/admin/yaya_push

### 9. 后台运行push.py

```bash
cd /home/admin/yaya_push
nohup python3 push.py > push.log 2>&1 &
tail -f push.log
```
