import requests
import json
import datetime
import time
import argparse
from urllib.parse import quote

# =================  配置区域 =================

QMSG_KEY = ""  # 填 Qmsg 的 KEY
USER_TOKEN = ""  # 填口袋账号 token

# 监控间隔 (秒)
CHECK_INTERVAL = 1

# 默认推送配置。room 或 member 没单独配置时，会使用这里。
# group = 推送到群，private = 推送到私聊
DEFAULT_PUSH_MODE = "group"
DEFAULT_TARGET_QQ = ""  # 默认群号或 QQ 号

# 多成员配置：
# 1. 每个房间可以单独设置 target_qq，推送到不同群
# 2. 如果房间不写 push_mode/target_qq，会使用成员级配置；成员也没写则使用默认配置
MEMBERS = [
         {
         "name": "谢晓倩",
         "member_id": 113443136,
         "server_id": "9028788",
         "push_mode": "private",
         "target_qq": "2696222344",
         "rooms": {
             "11191555": {"name": "小牙牙窝", "target_qq": ""},
             "19416583": {"name": "AY", "target_qq": ""},
         },
     },
]

# =================  代码区域 =================

MSG_API_URL = "https://pocketapi.48.cn/im/api/v1/team/last/message/get"
ROOM_MSG_API_URL = "https://pocketapi.48.cn/im/api/v1/team/message/list/homeowner"
ROOM_MSG_ALL_API_URL = "https://pocketapi.48.cn/im/api/v1/team/message/list/all"
LIVE_API_URL = "https://pocketapi.48.cn/live/api/v1/live/getLiveList"

last_msg_cache = {}


def get_headers():
    return {
        "Host": "pocketapi.48.cn",
        "Content-Type": "application/json;charset=utf-8",
        "token": USER_TOKEN,
        "User-Agent": "PocketFans201807/7.0.41 (iPhone; iOS 16.3.1; Scale/2.00)",
        "Accept-Language": "zh-Hans-CN;q=1",
        "appInfo": json.dumps({
            "vendor": "apple",
            "deviceId": "7B93DFD0-472F-4736-A628-E85FAE086486",
            "appVersion": "7.0.41",
            "appBuild": "24011601",
            "osVersion": "16.3.1",
            "osType": "ios",
            "deviceName": "iPhone XR",
            "os": "ios",
        }),
    }


def get_current_datetime():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fix_url(url):
    if not url:
        return ""
    url = str(url)
    if not url.startswith("http"):
        return f"https://source3.48.cn{url if url.startswith('/') else '/' + url}"
    return url


def try_parse_json(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value[0] not in "{[":
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def find_first_media_url(data, media_type="image"):
    if data is None:
        return ""

    image_keys = {
        "url",
        "imageUrl",
        "imgUrl",
        "picUrl",
        "pictureUrl",
        "originalUrl",
        "originUrl",
        "bigUrl",
        "coverUrl",
        "coverPath",
        "cover",
        "roomCover",
        "liveCover",
        "path",
    }
    audio_keys = {"audioPath", "audioUrl", "voiceUrl", "url", "path"}
    target_keys = image_keys if media_type == "image" else audio_keys
    skip_key_words = ("avatar", "head", "icon", "badge", "logo")

    if isinstance(data, str):
        parsed = try_parse_json(data)
        if parsed is not None:
            return find_first_media_url(parsed, media_type=media_type)
        if data.startswith(("http://", "https://", "/")):
            return fix_url(data)
        return ""

    if isinstance(data, list):
        for item in data:
            url = find_first_media_url(item, media_type=media_type)
            if url:
                return url
        return ""

    if isinstance(data, dict):
        for key, value in data.items():
            key_text = str(key)
            if any(word in key_text.lower() for word in skip_key_words):
                continue
            if key_text in target_keys and isinstance(value, str) and value:
                return fix_url(value)

        for key, value in data.items():
            key_text = str(key).lower()
            if any(word in key_text for word in skip_key_words):
                continue
            url = find_first_media_url(value, media_type=media_type)
            if url:
                return url

    return ""


def encode_qmsg_image_url(url):
    # Qmsg's image parser is sensitive to special characters in URLs.
    # Keep URL separators readable, encode base64/path/query value characters like "=".
    return quote(str(url), safe=":/?&")


def normalize_room(room_config):
    if isinstance(room_config, str):
        return {"name": room_config}
    return dict(room_config)


def get_push_target(member_config, room_config):
    push_mode = room_config.get("push_mode") or member_config.get("push_mode") or DEFAULT_PUSH_MODE
    target_qq = room_config.get("target_qq") or member_config.get("target_qq") or DEFAULT_TARGET_QQ
    return push_mode, str(target_qq)


def get_member_rooms(member_config):
    return {
        str(channel_id): normalize_room(room_config)
        for channel_id, room_config in member_config["rooms"].items()
    }


def fetch_member_messages(member_config):
    resp = requests.post(
        MSG_API_URL,
        headers=get_headers(),
        json={"serverId": member_config["server_id"]},
        timeout=5,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"服务器响应异常: {resp.status_code}")

    data = resp.json()
    if data.get("status") != 200:
        raise RuntimeError(f"API返回错误: {data.get('message')}")

    return data.get("content", {}).get("lastMsgList", [])


def fetch_room_message_details(member_config, channel_id, limit=50, fetch_all=False):
    url = ROOM_MSG_ALL_API_URL if fetch_all else ROOM_MSG_API_URL
    resp = requests.post(
        url,
        headers=get_headers(),
        json={
            "channelId": int(channel_id),
            "serverId": int(member_config["server_id"]),
            "nextTime": 0,
            "limit": int(limit),
        },
        timeout=8,
    )

    if resp.status_code != 200:
        return []

    data = resp.json()
    if data.get("status") != 200:
        return []

    content = data.get("content") or {}
    msg_list = content.get("message") or content.get("messageList") or []
    return msg_list if isinstance(msg_list, list) else []


def find_latest_media_detail(member_config, channel_id, media_type="image"):
    candidates = []
    for fetch_all in (False, True):
        for index, detail in enumerate(fetch_room_message_details(member_config, channel_id, fetch_all=fetch_all)):
            if not is_member_message(member_config, detail):
                continue
            msg_type = str(detail.get("msgType") or "").upper()
            if media_type == "image" and msg_type not in ("IMAGE", "EXPRESSIMAGE", "EXPRESS", "AGENT_WARMUP_IMG", "GIFT_SKILL_IMG", "CTM_IMG"):
                continue
            if media_type == "audio" and msg_type not in ("AUDIO", "AGENT_WARMUP_AUDIO", "AUDIO_REPLY", "GIFT_SKILL_AUDIO", "FLIPCARD_AUDIO"):
                continue
            if media_type == "video" and msg_type not in ("VIDEO", "SHORTVIDEO", "AGENT_WARMUP_VIDEO", "SHARE_VIDEO", "GIFT_SKILL_VIDEO", "FLIPCARD_VIDEO"):
                continue

            media_url = find_first_media_url(detail, media_type=media_type)
            if media_url:
                candidates.append((get_msg_sort_value(detail, index), detail))

        if candidates:
            break

    if not candidates:
        return None

    return max(candidates, key=lambda row: row[0])[1]


def find_latest_detail_by_type(member_config, channel_id, msg_types):
    candidates = []
    for fetch_all in (False, True):
        for index, detail in enumerate(fetch_room_message_details(member_config, channel_id, fetch_all=fetch_all)):
            if not is_member_message(member_config, detail):
                continue
            msg_type = str(detail.get("msgType") or "").upper()
            if msg_type not in msg_types:
                continue
            candidates.append((get_msg_sort_value(detail, index), detail))
        if candidates:
            break
    if not candidates:
        return None
    return max(candidates, key=lambda row: row[0])[1]


def get_msg_sort_value(item, fallback_index):
    for key in ("msgTime", "sendTime", "createTime", "time", "msgTimeStr"):
        value = item.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    return fallback_index


def get_message_sender_name(item):
    if item.get("starName"):
        return item.get("starName")
    if item.get("senderName"):
        return item.get("senderName")

    ext = try_parse_json(item.get("extInfo"))
    user = ext.get("user", {}) if isinstance(ext, dict) else {}
    return user.get("nickName") or user.get("userName") or user.get("name") or ""


def is_member_message(member_config, item):
    member_id = str(member_config.get("member_id", ""))
    member_name = str(member_config.get("name", ""))

    ext = try_parse_json(item.get("extInfo"))
    if isinstance(ext, dict):
        user = ext.get("user") or {}
        user_id = str(user.get("userId") or user.get("id") or "")
        role_id = str(user.get("roleId") or item.get("roleId") or "")
        channel_role = str(ext.get("channelRole") or "")

        if member_id and user_id == member_id:
            return True
        if role_id and role_id != "1":
            return True
        if channel_role and channel_role != "0":
            return True

    sender_id = str(item.get("senderUserId") or item.get("senderId") or item.get("uid") or "")
    if member_id and sender_id == member_id:
        return True

    sender_name = str(item.get("starName") or item.get("senderName") or "")
    if member_name and member_name in sender_name:
        return True

    return False


def get_message_cache_key(member_config, channel_id, content, item):
    for key in ("msgIdServer", "msgIdClient", "msgId", "messageId", "id", "msgTime", "sendTime", "createTime", "time", "msgTimeStr"):
        value = item.get(key)
        if value is not None and value != "":
            return f"{member_config['server_id']}_{channel_id}_{key}_{value}"

    media_url = find_first_media_url(item, media_type="image") or find_first_media_url(item, media_type="audio")
    if media_url:
        return f"{member_config['server_id']}_{channel_id}_{media_url}"

    return f"{member_config['server_id']}_{channel_id}_{content}"


def get_live_detail(member_config):
    member_id = member_config["member_id"]
    member_name = member_config["name"]
    payload = {"debug": "true", "nextTime": 0, "memberId": member_id, "giftFrom": 0}
    try:
        resp = requests.post(LIVE_API_URL, headers=get_headers(), json=payload, timeout=5)
        live_list = resp.json().get("content", {}).get("liveList", [])

        target_live = None
        for live in live_list:
            u_name = live.get("userInfo", {}).get("nickname", "")
            u_id = str(live.get("userInfo", {}).get("userId", ""))
            if u_id == str(member_id) or member_name in u_name:
                target_live = live
                break

        if target_live:
            title = target_live.get("title", "无标题")
            cover = (
                find_first_media_url(target_live, media_type="image")
                or find_first_media_url(target_live.get("userInfo", {}), media_type="image")
                or fix_url(target_live.get("coverPath") or "")
                or fix_url(target_live.get("coverUrl") or "")
                or fix_url(target_live.get("picPath") or "")
            )
            live_type = target_live.get("liveType", 1)
            type_str = "电台" if live_type == 2 else "视频"
            return title, cover, type_str
        return f"{member_name}的直播", "", "视频"
    except Exception:
        return "获取失败", "", "视频"


def parse_msg_content(raw_content, raw_item=None):
    raw_content = "" if raw_content is None else str(raw_content)
    try:
        parsed_content = try_parse_json(raw_content)

        if "[图片消息]" in raw_content:
            image_url = find_first_media_url(parsed_content, media_type="image") or find_first_media_url(raw_item, media_type="image")
            if image_url:
                return f"@image={encode_qmsg_image_url(image_url)}@"
            return "[图片消息] 未找到图片链接"

        if "[语音消息]" in raw_content:
            audio_url = find_first_media_url(parsed_content, media_type="audio") or find_first_media_url(raw_item, media_type="audio")
            if audio_url:
                return f"[语音消息] {audio_url}"
            return raw_content

        if "[视频消息]" in raw_content:
            video_url = ""
            if raw_item:
                video_body = raw_item.get("msgContent") or raw_item.get("bodys") or {}
                video_parsed = try_parse_json(video_body) if isinstance(video_body, str) else video_body
                video_url = find_first_media_url(video_parsed, media_type="image")
            if not video_url and parsed_content is not None:
                video_url = find_first_media_url(parsed_content, media_type="image")
            if video_url:
                return f"[视频消息] {video_url}"
            return "[视频消息] 未找到视频链接"

        if parsed_content is not None:
            if isinstance(parsed_content, dict) and (
                parsed_content.get("ext") in ("mp4", "mov", "avi", "mkv", "webm", "flv", "wmv", "ts")
                or "dur" in parsed_content
            ):
                video_url = find_first_media_url(parsed_content, media_type="image")
                if video_url:
                    return f"[视频消息] {video_url}"
            image_url = find_first_media_url(parsed_content, media_type="image")
            if image_url:
                return f"@image={encode_qmsg_image_url(image_url)}@"
            audio_url = find_first_media_url(parsed_content, media_type="audio")
            if audio_url:
                return f"[语音消息] {audio_url}"
    except Exception:
        pass
    return raw_content


def send_qmsg_rich(member_config, room_config, sender_nick, content, is_live=False, raw_item=None):
    member_name = member_config["name"]
    room_name = room_config["name"]
    push_mode, target_qq = get_push_target(member_config, room_config)

    if not QMSG_KEY:
        print(" 失败: 请先填写 QMSG_KEY")
        return
    if not target_qq:
        print(f" 失败: {member_name}/{room_name} 未配置 target_qq")
        return

    print(f"正在推送到 {target_qq}...", end="")

    # 1. 翻牌消息
    if raw_item and (raw_item.get("messageType") == "FLIPCARD" or raw_item.get("msgType") == "FLIPCARD"):
        parsed = try_parse_json(content)
        if parsed and isinstance(parsed, dict):
            question = parsed.get("question") or parsed.get("questionText") or ""
            answer = parsed.get("answer") or parsed.get("answerText") or ""
            msg_body = (
                f"【{room_name}|{member_name}】\n"
                f"『公开翻牌问题』：{question}\n"
                f"『{sender_nick}|{member_name}』：{answer}\n"
                f" {get_current_datetime()}"
            )
        else:
            msg_body = (
                f"【{room_name}|{member_name}】\n"
                f"『{sender_nick}|{member_name}』：{content}\n"
                f" {get_current_datetime()}"
            )
    # 2. 回复消息
    elif raw_item and (raw_item.get("messageType") in ("REPLY", "AGENT_QCHAT_TEXT_REPLY")
                       or raw_item.get("msgType") in ("REPLY", "AGENT_QCHAT_TEXT_REPLY")):
        parsed = try_parse_json(content)
        if parsed and isinstance(parsed, dict):
            reply_info = parsed.get("replyInfo") or {}
            if not isinstance(reply_info, dict):
                reply_info = {}
            reply_name = reply_info.get("replyName") or parsed.get("replyName") or ""
            reply_text = reply_info.get("replyText") or parsed.get("replyText") or ""
            text = parsed.get("text") or parsed.get("body") or ""
            msg_body = (
                f"【{room_name}|{member_name}】\n"
                f"『{reply_name}』：{reply_text}\n"
                f"『{sender_nick}|{member_name}』：{text}\n"
                f" {get_current_datetime()}"
            )
        else:
            msg_body = (
                f"【{room_name}|{member_name}】\n"
                f"『{sender_nick}|{member_name}』：{content}\n"
                f" {get_current_datetime()}"
            )
    # 3. 礼物感谢回复（文字/语音）
    elif raw_item and (raw_item.get("messageType") in ("GIFTREPLY", "AUDIO_GIFT_REPLY", "AGENT_QCHAT_GIFT_REPLY")
                     or raw_item.get("msgType") in ("GIFTREPLY", "AUDIO_GIFT_REPLY", "AGENT_QCHAT_GIFT_REPLY")):
        parsed = try_parse_json(content)
        if parsed and isinstance(parsed, dict) and "giftReplyInfo" in parsed:
            info = parsed["giftReplyInfo"]
            reply_name = info.get("replyName", "")
            reply_text = info.get("replyText", "")
            if "voiceUrl" in info:
                member_reply = f"[语音消息] {info.get('voiceUrl', '')}"
            else:
                member_reply = info.get("text", "")
            msg_body = (
                f"【{room_name}|{member_name}】\n"
                f"『{reply_name}』{reply_text}\n"
                f"『{sender_nick}|{member_name}』：{member_reply}\n"
                f" {get_current_datetime()}"
            )
        else:
            msg_body = (
                f"【{room_name}|{member_name}】\n"
                f"『{sender_nick}|{member_name}』：{content}\n"
                f" {get_current_datetime()}"
            )
    # 4. 直播通知
    elif is_live or "[直播消息]" in content:
        parsed = try_parse_json(content)
        live_title = ""
        live_cover = ""
        if parsed and isinstance(parsed, dict):
            live_title = parsed.get("liveTitle", "")
            live_cover = parsed.get("liveCover", "")
        api_title, api_cover, live_type = get_live_detail(member_config)
        live_title = live_title or api_title
        live_cover = fix_url(live_cover) or api_cover
        cover_code = f"@image={encode_qmsg_image_url(live_cover)}@" if live_cover else ""
        msg_body = (
            f"【{room_name}|{member_name}】\n"
            f"{member_name}直播啦~\n"
            f"标题：{live_title}\n"
            f"类型：{live_type}\n"
            f"{cover_code}\n"
            f" {get_current_datetime()}"
        )
    # 5. 普通消息
    else:
        final_content = parse_msg_content(content, raw_item=raw_item)
        msg_body = (
            f"【{room_name}|{member_name}】\n"
            f"『{sender_nick}|{member_name}』：{final_content}\n"
            f" {get_current_datetime()}"
        )

    if push_mode == "group":
        url = f"https://qmsg.zendee.cn/jgroup/{QMSG_KEY}"
    else:
        url = f"https://qmsg.zendee.cn/jsend/{QMSG_KEY}"

    try:
        res = requests.post(url, json={"msg": msg_body, "qq": target_qq}, timeout=5)
        res_data = res.json()
        if res_data.get("success"):
            print("成功")
        else:
            print(f"失败: {res_data.get('reason')}")
    except Exception as e:
        print(f"网络异常: {e}")


def get_message_payload(member_config, rooms, item):
    channel_id = str(item.get("channelId"))
    if channel_id not in rooms:
        return None
    if not is_member_message(member_config, item):
        return None

    room_config = rooms[channel_id]
    content = item.get("msgContent") or ""
    if isinstance(content, (dict, list)):
        content = json.dumps(content, ensure_ascii=False)
    raw_item = item
    star_name = get_message_sender_name(item)
    is_live_msg = False

    if "[表情消息]" in content:
        content = "[图片消息]"

    if "[直播消息]" in content or room_config.get("is_live") or item.get("msgType") in ("LIVEPUSH", "LIVE_PUSH", "SHARE_LIVE") or item.get("messageType") in ("LIVEPUSH", "LIVE_PUSH", "SHARE_LIVE"):
        is_live_msg = True
        body_data = item.get("bodys") or ""
        if isinstance(body_data, dict):
            parsed = body_data
        else:
            parsed = try_parse_json(body_data)
        if parsed and isinstance(parsed, dict):
            info = parsed.get("livePushInfo") or {}
            if isinstance(info, dict):
                content = json.dumps({
                    "liveTitle": info.get("liveTitle", ""),
                    "liveCover": info.get("liveCover", ""),
                }, ensure_ascii=False)
            else:
                content = "[直播消息]"
        else:
            content = "[直播消息]"
    elif "[图片消息]" in content:
        detail = find_latest_media_detail(member_config, channel_id, media_type="image")
        if detail:
            raw_item = detail
            star_name = get_message_sender_name(detail) or star_name
    elif "[语音消息]" in content:
        detail = find_latest_media_detail(member_config, channel_id, media_type="audio")
        if detail:
            raw_item = detail
            star_name = get_message_sender_name(detail) or star_name
    elif "[视频消息]" in content:
        detail = find_latest_media_detail(member_config, channel_id, media_type="video")
        if detail:
            raw_item = detail
            star_name = get_message_sender_name(detail) or star_name
    elif "[礼物回复消息]" in content:
        detail = find_latest_detail_by_type(member_config, channel_id, ("GIFTREPLY", "AUDIO_GIFT_REPLY", "AGENT_QCHAT_GIFT_REPLY"))
        if detail:
            raw_item = detail
            star_name = get_message_sender_name(detail) or star_name
            raw_content = detail.get("msgContent") or ""
            content = json.dumps(raw_content, ensure_ascii=False) if isinstance(raw_content, (dict, list)) else str(raw_content)

    msg_key = get_message_cache_key(member_config, channel_id, content, raw_item)
    return room_config, star_name, content, is_live_msg, msg_key, raw_item


def get_detail_message_content(detail):
    msg_type = str(detail.get("msgType") or "").upper()
    body = detail.get("msgContent")
    if body is None:
        body = detail.get("bodys", "")

    if msg_type in ("IMAGE", "EXPRESSIMAGE", "EXPRESS", "AGENT_WARMUP_IMG", "GIFT_SKILL_IMG", "CTM_IMG"):
        return "[图片消息]"
    if msg_type in ("GIFTREPLY", "AUDIO_GIFT_REPLY", "AGENT_QCHAT_GIFT_REPLY"):
        if isinstance(body, (dict, list)):
            return json.dumps(body, ensure_ascii=False)
        return str(body or "[礼物回复消息]")
    if msg_type in ("VIDEO", "SHORTVIDEO", "AGENT_WARMUP_VIDEO", "SHARE_VIDEO", "GIFT_SKILL_VIDEO", "FLIPCARD_VIDEO"):
        return "[视频消息]"
    if msg_type in ("AUDIO", "AGENT_WARMUP_AUDIO", "AUDIO_REPLY", "GIFT_SKILL_AUDIO", "FLIPCARD_AUDIO"):
        return "[语音消息]"
    if msg_type in ("LIVEPUSH", "LIVE_PUSH", "SHARE_LIVE"):
        body_str = detail.get("bodys") or detail.get("msgContent") or ""
        if isinstance(body_str, dict):
            parsed = body_str
        else:
            parsed = try_parse_json(body_str)
        if parsed and isinstance(parsed, dict):
            info = parsed.get("livePushInfo") or {}
            if isinstance(info, dict):
                return json.dumps({
                    "liveTitle": info.get("liveTitle", ""),
                    "liveCover": info.get("liveCover", ""),
                }, ensure_ascii=False)
        return "[直播消息]"
    if msg_type in ("GIFT_TEXT", "GIFT_SKILL_TEXT"):
        if isinstance(body, (dict, list)):
            return json.dumps(body, ensure_ascii=False)
        return str(body or "")
    if isinstance(body, (dict, list)):
        return json.dumps(body, ensure_ascii=False)
    return str(body or "")


def get_detail_message_payload(member_config, room_config, channel_id, detail):
    content = get_detail_message_content(detail)
    msg_type = str(detail.get("msgType") or "").upper()
    is_live_msg = (
        "[直播消息]" in content
        or room_config.get("is_live")
        or msg_type in ("LIVEPUSH", "LIVE_PUSH", "SHARE_LIVE")
    )
    if is_live_msg and "[直播消息]" in content:
        content = "[直播消息]"

    star_name = get_message_sender_name(detail) or member_config["name"]
    msg_key = get_message_cache_key(member_config, channel_id, content, detail)
    return room_config, star_name, content, is_live_msg, msg_key, detail


def collect_test_candidates(member_config, limit):
    rooms = get_member_rooms(member_config)
    candidates_by_key = {}

    for channel_id, room_config in rooms.items():
        room_details = []
        for fetch_all in (False, True):
            room_details.extend(
                fetch_room_message_details(member_config, channel_id, limit=max(limit, 20), fetch_all=fetch_all)
            )

        for index, detail in enumerate(room_details):
            if not is_member_message(member_config, detail):
                continue
            payload = get_detail_message_payload(member_config, room_config, channel_id, detail)
            _, _, content, _, msg_key, _ = payload
            if not content.strip():
                continue
            candidates_by_key[msg_key] = (get_msg_sort_value(detail, index), payload)

    # Some special channels, especially live notification channels, may only appear in last-message summaries.
    for index, item in enumerate(fetch_member_messages(member_config)):
        payload = get_message_payload(member_config, rooms, item)
        if not payload:
            continue
        _, _, _, _, msg_key, _ = payload
        candidates_by_key.setdefault(msg_key, (get_msg_sort_value(item, index), payload))

    return list(candidates_by_key.values())


def monitor_member(member_config, is_silent_init=False):

    try:
        rooms = get_member_rooms(member_config)
        
        # 循环遍历配置里的每一个房间通道
        for channel_id, room_config in rooms.items():
            # 直接获取该房间最新的几条消息（fetch_all=False 只拿最新，效率高）
            msg_list = fetch_room_message_details(member_config, channel_id, limit=10, fetch_all=False)
            
            # 倒序处理，确保新消息按时间顺序一条条推送
            for detail in reversed(msg_list):
                if not is_member_message(member_config, detail):
                    continue
                    
                # 转换为标准推送 Payload
                payload = get_detail_message_payload(member_config, room_config, channel_id, detail)
                _, star_name, content, is_live_msg, msg_key, raw_item = payload
                
                if not content.strip():
                    continue
                    
                # 检查缓存，防止重复推送
                if msg_key in last_msg_cache:
                    continue

                last_msg_cache[msg_key] = True

                if not is_silent_init:
                    print("\n" + "-" * 30)
                    print(f"[{get_current_datetime()}] {member_config['name']} 新动态: {content}")
                    send_qmsg_rich(member_config, room_config, star_name, content, is_live=is_live_msg, raw_item=raw_item)
                    print("-" * 30 + "\n")

    except requests.exceptions.ConnectionError:
        print(f"{member_config['name']} 网络连接失败，等待重试...")
    except RuntimeError as e:
        print(f"{member_config['name']} {e}")
    except Exception as e:
        print(f"{member_config['name']} 发生未知异常: {e}")


def monitor_once(is_silent_init=False):
    for member_config in MEMBERS:
        monitor_member(member_config, is_silent_init=is_silent_init)


def test_push_latest_once(limit=10):
    print("=" * 50)
    print(f"测试推送模式：获取目标房间最新 {limit} 条消息并强制推送")
    print("=" * 50)

    for member_config in MEMBERS:
        try:
            candidates = collect_test_candidates(member_config, limit)

            if not candidates:
                print(f"{member_config['name']} 没有找到已配置房间的最新消息")
                continue

            latest_candidates = sorted(candidates, key=lambda row: row[0], reverse=True)[:limit]
            latest_candidates.reverse()

            print(f"\n{member_config['name']} 将测试推送 {len(latest_candidates)} 条")
            for index, (_, payload) in enumerate(latest_candidates, start=1):
                room_config, star_name, content, is_live_msg, _, raw_item = payload

                print("\n" + "-" * 30)
                print(f"[{index}/{len(latest_candidates)}] {member_config['name']} 测试推送: {content}")
                send_qmsg_rich(member_config, room_config, star_name, content, is_live=is_live_msg, raw_item=raw_item)
                print("-" * 30 + "\n")
                time.sleep(0.8)

        except requests.exceptions.ConnectionError:
            print(f"{member_config['name']} 网络连接失败")
        except RuntimeError as e:
            print(f"{member_config['name']} {e}")
        except Exception as e:
            print(f"{member_config['name']} 测试推送失败: {e}")


def debug_latest_messages():
    print("=" * 50)
    print("调试模式：打印已配置房间的最新消息原始数据")
    print("=" * 50)

    for member_config in MEMBERS:
        try:
            rooms = get_member_rooms(member_config)
            msg_list = fetch_member_messages(member_config)

            print(f"\n### {member_config['name']}")
            found = False
            for item in msg_list:
                channel_id = str(item.get("channelId"))
                if channel_id not in rooms:
                    continue

                found = True
                print(f"\n房间: {rooms[channel_id]['name']} ({channel_id})")
                print(json.dumps(item, ensure_ascii=False, indent=2))

            if not found:
                print("没有找到已配置房间的消息")

        except Exception as e:
            print(f"{member_config['name']} 调试失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="口袋48多成员消息监控与 Qmsg 推送")
    parser.add_argument("--test", action="store_true", help="抓取每个成员目标房间的最新消息并强制推送")
    parser.add_argument("--test-limit", type=int, default=10, help="测试推送条数，默认 10")
    parser.add_argument("--debug-latest", action="store_true", help="打印已配置房间的最新消息原始数据，用于排查图片字段")
    args = parser.parse_args()

    if args.debug_latest:
        debug_latest_messages()
        return

    if args.test:
        test_push_latest_once(limit=max(1, args.test_limit))
        return

    print("=" * 50)
    print("多成员全自动监控系统")
    print(f"监控成员数: {len(MEMBERS)}")
    print(f"刷新频率: {CHECK_INTERVAL}秒/次")
    print("=" * 50)

    print("正在初始化缓存(跳过旧消息)...", end="")
    monitor_once(is_silent_init=True)
    print(" 完成")
    print(f"当前已缓存 {len(last_msg_cache)} 条历史记录，开始实时监控...")
    print("-" * 50)

    while True:
        print(f"\r[{get_current_datetime().split(' ')[1]}] 正在监控中 (Ctrl+C 停止)", end="", flush=True)
        monitor_once(is_silent_init=False)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n监控已停止")
