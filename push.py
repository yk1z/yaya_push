import os
import requests
import json
import datetime
import time
import argparse
import sys
import subprocess
import signal
from urllib.parse import quote

QMSG_KEY = "" 
USER_TOKEN = "" 
CHECK_INTERVAL = 1  

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "msg_cache.json")
CONFIG_FILE = os.path.join(BASE_DIR, "push_config.json")
LOG_FILE = os.path.join(BASE_DIR, "push.log")
PID_FILE = os.path.join(BASE_DIR, "push.pid")
MEMBER_DATA_URL = "https://data.gnz.hk/members.json"

DEFAULT_PUSH_MODE = ""  
DEFAULT_TARGET_QQ = ""  

MEMBERS = []


MSG_API_URL = "https://pocketapi.48.cn/im/api/v1/team/last/message/get"
ROOM_MSG_API_URL = "https://pocketapi.48.cn/im/api/v1/team/message/list/homeowner"
ROOM_MSG_ALL_API_URL = "https://pocketapi.48.cn/im/api/v1/team/message/list/all"
LIVE_API_URL = "https://pocketapi.48.cn/live/api/v1/live/getLiveList"
SEINE_SERVER_DETAIL_API_URL = "https://pocketapi.48.cn/im/api/seine/server/detail"

last_msg_cache = {}
member_table_cache = []
room_name_cache = {}
config_file_mtime = None

def load_cache():
    """从本地文件持久化加载缓存"""
    global last_msg_cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                last_msg_cache = json.load(f)
            if len(last_msg_cache) > 2000:
                keys = list(last_msg_cache.keys())
                last_msg_cache = {k: last_msg_cache[k] for k in keys[-1000:]}
        except Exception as e:
            print(f"\n加载缓存文件失败，将使用空缓存: {e}")
            last_msg_cache = {}
    else:
        last_msg_cache = {}

def save_cache():
    """保存缓存到本地文件"""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(last_msg_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"\n保存缓存文件失败: {e}")


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"members": [dict(member) for member in MEMBERS]}

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError(f"读取配置文件失败: {e}")

    if isinstance(data, list):
        return {"members": data}
    if isinstance(data, dict):
        data.setdefault("members", [])
        return data
    raise RuntimeError("配置文件格式错误，应为 JSON 对象或成员数组")


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_config_members():
    data = load_config()
    members = data.get("members", [])
    if not isinstance(members, list):
        raise RuntimeError("配置文件格式错误，应为 JSON 对象或成员数组")
    return [dict(member) for member in members if isinstance(member, dict)]


def save_config_members(members):
    data = load_config()
    data["members"] = members
    save_config(data)


def mask_secret(value):
    value = str(value or "")
    if not value:
        return "未配置"
    if len(value) <= 8:
        return "已配置"
    return f"已配置(...{value[-4:]})"


def apply_runtime_config():
    global QMSG_KEY, USER_TOKEN
    data = load_config()
    qmsg_key = str(data.get("qmsg_key") or QMSG_KEY or "").strip()
    user_token = str(data.get("user_token") or USER_TOKEN or "").strip()
    QMSG_KEY = qmsg_key
    USER_TOKEN = user_token


def print_secret_config():
    data = load_config()
    qmsg_key = data.get("qmsg_key") or QMSG_KEY
    user_token = data.get("user_token") or USER_TOKEN
    print(f"Qmsg KEY: {mask_secret(qmsg_key)}")
    print(f"口袋账号Token: {mask_secret(user_token)}")


def interactive_update_secrets():
    data = load_config()
    current_qmsg = str(data.get("qmsg_key") or QMSG_KEY or "").strip()
    current_token = str(data.get("user_token") or USER_TOKEN or "").strip()

    print_secret_config()
    print("直接回车表示保持不变。")
    qmsg_key = input("新的 Qmsg KEY: ").strip()
    user_token = input("新的 口袋账号Token: ").strip()

    if qmsg_key:
        data["qmsg_key"] = qmsg_key
    elif "qmsg_key" not in data and current_qmsg:
        data["qmsg_key"] = current_qmsg

    if user_token:
        data["user_token"] = user_token
    elif "user_token" not in data and current_token:
        data["user_token"] = current_token

    save_config(data)
    apply_runtime_config()
    print("密钥配置已保存")


def get_config_member_label(member):
    push_mode = member.get("push_mode") or DEFAULT_PUSH_MODE
    target_qq = member.get("target_qq") or DEFAULT_TARGET_QQ
    mode_label = "群" if push_mode == "group" else "私聊"
    return f"{member.get('name')} -> {mode_label} {target_qq}"


def find_config_member_index(members, name, target_qq=None):
    target_name = normalize_member_lookup_text(name)
    target_qq = None if target_qq is None else str(target_qq)
    for index, member in enumerate(members):
        if normalize_member_lookup_text(member.get("name")) != target_name:
            continue
        if target_qq is None or str(member.get("target_qq") or DEFAULT_TARGET_QQ) == target_qq:
            return index
    return -1


def find_config_member_indexes(members, name):
    target_name = normalize_member_lookup_text(name)
    return [
        index
        for index, member in enumerate(members)
        if normalize_member_lookup_text(member.get("name")) == target_name
    ]


def add_config_member(args):
    members = load_config_members()
    name = str(args.add_member or "").strip()
    if not name:
        raise RuntimeError("请提供成员名字")

    record = validate_member_name(name)
    name = record.get("ownerName") or record.get("name") or name

    member = {
        "name": name,
        "push_mode": args.push_mode or DEFAULT_PUSH_MODE,
        "target_qq": str(args.target_qq or DEFAULT_TARGET_QQ),
        "push_big_room": True if args.push_big_room is None else bool(args.push_big_room),
        "push_small_room": True if args.push_small_room is None else bool(args.push_small_room),
    }
    if not member["target_qq"]:
        raise RuntimeError("请填写群号或 QQ 号")
    if not member["push_big_room"] and not member["push_small_room"]:
        raise RuntimeError("大房间和小房间不能同时关闭")

    index = find_config_member_index(members, name, member["target_qq"])
    if index >= 0:
        members[index].update(member)
        action = "更新"
    else:
        members.append(member)
        action = "添加"

    save_config_members(members)
    print(f"已{action}成员: {name}")
    print(f"配置文件: {CONFIG_FILE}")


def remove_config_member(name, target_qq=None):
    members = load_config_members()
    if target_qq is None:
        indexes = find_config_member_indexes(members, name)
        if len(indexes) > 1:
            print(f"{name} 有多条推送配置，请指定 target_qq，或在菜单里按序号删除:")
            for index in indexes:
                print(f"  {index + 1}. {get_config_member_label(members[index])}")
            return
        index = indexes[0] if indexes else -1
    else:
        index = find_config_member_index(members, name, target_qq)
    if index < 0:
        print(f"配置里没有找到成员: {name}")
        return
    removed = members.pop(index)
    save_config_members(members)
    print(f"已删除成员: {removed.get('name')}")
    print(f"配置文件: {CONFIG_FILE}")


def print_config_members():
    members = load_config_members()
    if not members:
        print("当前没有配置成员")
        print(f"可用 --add-member 添加，配置文件: {CONFIG_FILE}")
        return

    print(f"配置文件: {CONFIG_FILE}")
    print(f"已配置成员数: {len(members)}")
    for index, member in enumerate(members, start=1):
        push_mode = member.get("push_mode") or DEFAULT_PUSH_MODE
        target_qq = member.get("target_qq") or DEFAULT_TARGET_QQ
        big_room = member.get("push_big_room", True)
        small_room = member.get("push_small_room", True)
        print(f"{index}. {get_config_member_label(member)} 大房间={'开' if big_room else '关'} 小房间={'开' if small_room else '关'}")


def validate_member_name(name, interactive=False):
    table = load_member_table()
    if not table:
        raise RuntimeError("成员表读取失败，暂时无法校验成员名字")

    exact_matches = find_member_records_strict(name, table)
    if len(exact_matches) > 1:
        names = "、".join(str(item.get("ownerName") or item.get("name")) for item in exact_matches[:8])
        raise RuntimeError(f"{name} 匹配到多个成员: {names}，请改用成员真名或完整拼音")
    record = exact_matches[0] if exact_matches else None
    if not record:
        matches = search_member_records(name, table, limit=5)
        hint = ""
        if matches:
            names = "、".join(str(item.get("ownerName") or item.get("name")) for item in matches)
            hint = f"，相近结果: {names}"
        raise RuntimeError(f"成员表里找不到 {name}{hint}")

    display_name = record.get("ownerName") or record.get("name") or name
    print(
        f"已找到成员: {display_name} "
        f"member_id={get_record_member_id(record)} "
        f"server_id={get_record_server_id(record)} "
        f"大房间={get_record_big_room_id(record)} "
        f"小房间={get_record_small_room_id(record) or ''}"
    )
    if interactive and not prompt_bool("确认使用这个成员", default=True):
        raise RuntimeError("已取消添加成员")
    return record


def prompt_text(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


MENU_BACK = "__menu_back__"


def prompt_choice(prompt, choices, default=None, zero_label="返回主菜单"):
    choice_map = {str(index): value for index, (value, _) in enumerate(choices, start=1)}

    while True:
        print(prompt)
        for index, (value, label) in enumerate(choices, start=1):
            print(f"  {index}. {label}")
        print(f"  0. {zero_label}")
        answer = input("请选择数字: ").strip()
        if answer == "0":
            return MENU_BACK
        if answer in choice_map:
            return choice_map[answer]
        print("输入无效，请重新选择。")


def prompt_bool(prompt, default=True):
    default_label = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{prompt} ({default_label}): ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes", "1", "true", "开", "是"):
            return True
        if answer in ("n", "no", "0", "false", "关", "否"):
            return False
        print("请输入 y 或 n。")


def prompt_member_index(members, prompt="请选择成员"):
    if not members:
        print("当前没有配置成员")
        return None
    for index, member in enumerate(members, start=1):
        print(f"  {index}. {get_config_member_label(member)}")
    print("  0. 返回主菜单")
    while True:
        answer = input(f"{prompt}，输入数字: ").strip()
        if answer == "0":
            return MENU_BACK
        if not answer:
            return None
        try:
            index = int(answer)
        except ValueError:
            print("请输入数字。")
            continue
        if 1 <= index <= len(members):
            return index - 1
        print("数字超出范围。")


def interactive_add_or_update_member():
    members = load_config_members()
    name = prompt_text("成员名字")
    if not name:
        print("已取消")
        return

    try:
        record = validate_member_name(name, interactive=True)
    except RuntimeError as e:
        print(f"错误: {e}")
        return
    name = record.get("ownerName") or record.get("name") or name

    push_mode = prompt_choice(
        "推送方式",
        (("group", "QQ群"), ("private", "QQ私聊")),
        default=DEFAULT_PUSH_MODE,
    )
    if push_mode == MENU_BACK:
        print("已返回主菜单")
        return
    target_qq = prompt_text("群号或 QQ 号", DEFAULT_TARGET_QQ)
    if not target_qq:
        print("群号或 QQ 号不能为空，已取消")
        return
    index = find_config_member_index(members, name, target_qq)
    old = members[index] if index >= 0 else {}
    if old:
        push_mode = prompt_choice(
            "已找到同名同目标配置，确认推送方式",
            (("group", "QQ群"), ("private", "QQ私聊")),
            default=old.get("push_mode") or push_mode,
        )
        if push_mode == MENU_BACK:
            print("已返回主菜单")
            return
    push_big_room = prompt_bool("推送大房间", old.get("push_big_room", True))
    push_small_room = prompt_bool("推送小房间", old.get("push_small_room", True))
    if not push_big_room and not push_small_room:
        print("大房间和小房间不能同时关闭，已取消")
        return

    member = {
        "name": name,
        "push_mode": push_mode,
        "target_qq": str(target_qq),
        "push_big_room": push_big_room,
        "push_small_room": push_small_room,
    }
    if index >= 0:
        members[index].update(member)
        action = "更新"
    else:
        members.append(member)
        action = "添加"
    save_config_members(members)
    print(f"已{action}成员: {name}")


def interactive_remove_member():
    members = load_config_members()
    index = prompt_member_index(members, "选择要删除的成员")
    if index == MENU_BACK:
        print("已返回主菜单")
        return
    if index is None:
        print("已取消")
        return
    member = members[index]
    if not prompt_bool(f"确认删除 {member.get('name')}", default=False):
        print("已取消")
        return
    removed = members.pop(index)
    save_config_members(members)
    print(f"已删除成员: {removed.get('name')}")


def interactive_toggle_member_rooms():
    members = load_config_members()
    index = prompt_member_index(members, "选择要修改房间开关的成员")
    if index == MENU_BACK:
        print("已返回主菜单")
        return
    if index is None:
        print("已取消")
        return
    member = members[index]
    print(f"当前成员: {member.get('name')}")
    member["push_big_room"] = prompt_bool("推送大房间", member.get("push_big_room", True))
    member["push_small_room"] = prompt_bool("推送小房间", member.get("push_small_room", True))
    if not member["push_big_room"] and not member["push_small_room"]:
        print("大房间和小房间不能同时关闭，已取消修改")
        return
    save_config_members(members)
    print(f"已更新成员: {member.get('name')}")


def read_monitor_pid():
    if not os.path.exists(PID_FILE):
        return None
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
        return pid
    except Exception:
        return None


def write_monitor_pid(pid):
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(pid))


def remove_monitor_pid():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except OSError:
        pass


def is_process_running(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_monitor_process(pid):
    if not is_process_running(pid):
        return False
    if os.name == "nt":
        # Windows 没有 /proc；这里至少确保进程存在，避免破坏本地可用性。
        return True

    cmdline_path = f"/proc/{pid}/cmdline"
    try:
        with open(cmdline_path, "rb") as f:
            parts = [part.decode("utf-8", errors="ignore") for part in f.read().split(b"\0") if part]
    except OSError:
        return False

    script_path = os.path.abspath(__file__)
    has_script = any(os.path.abspath(part) == script_path if part.endswith(".py") else part.endswith("push.py") for part in parts)
    return has_script and "--run" in parts


def start_background_monitor():
    pid = read_monitor_pid()
    if pid and is_monitor_process(pid):
        print(f"后台推送已经在运行，PID: {pid}")
        print(f"日志文件: {LOG_FILE}")
        return
    if pid:
        remove_monitor_pid()

    remove_monitor_pid()
    log_file = open(LOG_FILE, "w", encoding="utf-8")
    kwargs = {
        "cwd": BASE_DIR,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--run"], **kwargs)
    log_file.close()
    write_monitor_pid(process.pid)
    print(f"已启动后台推送，PID: {process.pid}")
    print(f"日志文件: {LOG_FILE}")


def stop_background_monitor():
    pid = read_monitor_pid()
    if not pid or not is_monitor_process(pid):
        remove_monitor_pid()
        print("后台推送没有在运行")
        return

    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        if is_process_running(pid):
            os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    except OSError as e:
        print(f"停止后台推送失败: {e}")
        return

    remove_monitor_pid()
    print(f"已停止后台推送，PID: {pid}")


def follow_push_log():
    if not os.path.exists(LOG_FILE):
        print("还没有 push.log，请先启动后台推送")
        return

    print(f"正在查看实时日志: {LOG_FILE}")
    print("按 Ctrl+C 返回主菜单")
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if line:
                    print(line, end="")
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n已返回主菜单")


def interactive_config_menu():
    while True:
        print("\n" + "=" * 40)
        print("牙牙推送 by yk1z")
        print("=" * 40)
        action = prompt_choice(
            "请选择操作",
            (
                ("list", "当前成员配置"),
                ("add", "添加成员"),
                ("remove", "删除成员"),
                ("rooms", "房间推送开关"),
                ("secrets", "密钥配置"),
                ("start", "启动后台推送"),
                ("stop", "停止后台推送"),
                ("log", "实时推送日志"),
            ),
            default="list",
            zero_label="退出菜单",
        )
        print()
        if action == MENU_BACK:
            print("已退出配置菜单")
            return
        if action == "list":
            print_config_members()
        elif action == "add":
            interactive_add_or_update_member()
        elif action == "remove":
            interactive_remove_member()
        elif action == "rooms":
            interactive_toggle_member_rooms()
        elif action == "secrets":
            interactive_update_secrets()
        elif action == "start":
            start_background_monitor()
        elif action == "stop":
            stop_background_monitor()
        elif action == "log":
            follow_push_log()


def normalize_member_lookup_text(value):
    text = str(value or "").strip().lower()
    for prefix in ("snh48", "bej48", "gnz48", "ckg48", "cgt48", "shy48", "idft", "team"):
        text = text.replace(prefix, "")
    for char in (" ", "\t", "\r", "\n", "-", "_", "|", "丨", "/", "\\", "·", ".", "。", "(", ")", "（", "）"):
        text = text.replace(char, "")
    return text


def normalize_member_data_payload(data):
    records = []
    seen = set()

    def add_record(item):
        if not isinstance(item, dict):
            return
        member_id = item.get("id") or item.get("userId") or item.get("memberId")
        name = item.get("ownerName") or item.get("name") or item.get("nickname")
        if not member_id or not name:
            return
        key = str(member_id)
        if key in seen:
            return
        seen.add(key)
        records.append(item)

    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            add_record(value)
            for key in ("members", "retired", "roomId", "data", "list"):
                if key in value:
                    walk(value.get(key))

    walk(data)
    return records


def load_member_table(force_refresh=False):
    global member_table_cache
    if member_table_cache and not force_refresh:
        return member_table_cache

    try:
        resp = requests.get(MEMBER_DATA_URL, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        records = normalize_member_data_payload(data)
        if records:
            member_table_cache = records
            return member_table_cache
        print("\n成员表读取失败: 远程成员表为空")
    except Exception as e:
        print(f"\n成员表读取失败: {e}")
    return []


def get_pinyin_initials(value):
    text = str(value or "").strip()
    if not text:
        return ""

    initials = []
    prev_is_separator = True
    for char in text:
        if not char.isalpha():
            prev_is_separator = True
            continue
        if prev_is_separator or char.isupper():
            initials.append(char)
        prev_is_separator = False
    return "".join(initials)


def get_member_aliases(record):
    aliases = []
    for key in ("ownerName", "name", "nickname", "nickName", "pinyin", "abbr", "initials"):
        value = record.get(key)
        if value:
            aliases.append(str(value))
    pinyin_initials = get_pinyin_initials(record.get("pinyin"))
    if pinyin_initials:
        aliases.append(pinyin_initials)
    owner_name = record.get("ownerName") or record.get("name")
    team = record.get("team")
    if owner_name and team:
        aliases.append(f"{team}{owner_name}")
    return aliases


def find_member_record(query, table=None):
    query_key = normalize_member_lookup_text(query)
    if not query_key:
        return None
    table = table if table is not None else load_member_table()

    contains_matches = []
    for record in table:
        alias_keys = [normalize_member_lookup_text(alias) for alias in get_member_aliases(record)]
        alias_keys = [alias for alias in alias_keys if alias]
        if query_key in alias_keys:
            return record
        if any(query_key in alias or alias in query_key for alias in alias_keys):
            contains_matches.append(record)

    return contains_matches[0] if len(contains_matches) == 1 else None


def find_member_record_strict(query, table=None):
    matches = find_member_records_strict(query, table)
    return matches[0] if len(matches) == 1 else None


def find_member_records_strict(query, table=None):
    query_key = normalize_member_lookup_text(query)
    if not query_key:
        return []
    table = table if table is not None else load_member_table()

    matches = []
    for record in table:
        alias_keys = [normalize_member_lookup_text(alias) for alias in get_member_aliases(record)]
        if query_key in [alias for alias in alias_keys if alias]:
            matches.append(record)
    return matches


def find_member_record_by_id(member_id, table=None):
    member_id = str(member_id or "")
    if not member_id:
        return None
    table = table if table is not None else load_member_table()
    for record in table:
        if str(get_record_member_id(record) or "") == member_id:
            return record
    return None


def search_member_records(query, table=None, limit=20):
    query_key = normalize_member_lookup_text(query)
    table = table if table is not None else load_member_table()
    if not query_key:
        return table[:limit]

    matches = []
    for record in table:
        alias_keys = [normalize_member_lookup_text(alias) for alias in get_member_aliases(record)]
        if any(query_key in alias or alias in query_key for alias in alias_keys if alias):
            matches.append(record)
            if len(matches) >= limit:
                break
    return matches


def get_record_member_id(record):
    return record.get("id") or record.get("userId") or record.get("memberId")


def get_record_server_id(record):
    return record.get("serverId") or record.get("server_id")


def get_record_big_room_id(record):
    return record.get("channelId") or record.get("bigChannelId") or record.get("roomId")


def get_record_small_room_id(record):
    return record.get("yklzId") or record.get("smallChannelId") or record.get("smallRoomId")


def resolve_room_alias(room_name, record):
    key = normalize_member_lookup_text(room_name)
    big_room_id = get_record_big_room_id(record)
    small_room_id = get_record_small_room_id(record)

    if str(room_name).isdigit():
        return str(room_name), str(room_name)
    if key in ("大房间", "大", "big", "bigroom", "main", "大房"):
        return str(big_room_id or ""), "大房间"
    if key in ("小房间", "小", "small", "smallroom", "yklz", "口袋房", "小房"):
        return str(small_room_id or ""), "小房间"
    return str(room_name), str(room_name)


def merge_room_config(room_config, default_name):
    room = normalize_room(room_config) if room_config is not None else {}
    room.setdefault("name", default_name)
    return room


def add_resolved_room(rooms, channel_id, default_name, room_config=None):
    if not channel_id:
        return
    rooms[str(channel_id)] = merge_room_config(room_config, default_name)


def config_pushes_room(member_config, flag_name):
    if flag_name not in member_config:
        return True
    return bool(member_config.get(flag_name))


def build_rooms_from_room_flags(record, member_config):
    rooms = {}
    big_room_id = get_record_big_room_id(record)
    small_room_id = get_record_small_room_id(record)

    if config_pushes_room(member_config, "push_big_room"):
        add_resolved_room(rooms, big_room_id, "大房间")
    if (
        config_pushes_room(member_config, "push_small_room")
        and small_room_id
        and str(small_room_id) != str(big_room_id)
    ):
        add_resolved_room(rooms, small_room_id, "小房间")
    return rooms


def build_rooms_from_member_record(record, requested_rooms=None):
    rooms = {}
    big_room_id = get_record_big_room_id(record)
    small_room_id = get_record_small_room_id(record)

    if requested_rooms in (None, "", []):
        add_resolved_room(rooms, big_room_id, "大房间")
        if small_room_id and str(small_room_id) != str(big_room_id):
            add_resolved_room(rooms, small_room_id, "小房间")
        return rooms

    if isinstance(requested_rooms, dict):
        for key, value in requested_rooms.items():
            channel_id, default_name = resolve_room_alias(key, record)
            add_resolved_room(rooms, channel_id, default_name, value)
        return rooms

    if isinstance(requested_rooms, (list, tuple, set)):
        for room_name in requested_rooms:
            channel_id, default_name = resolve_room_alias(room_name, record)
            add_resolved_room(rooms, channel_id, default_name)
        return rooms

    channel_id, default_name = resolve_room_alias(requested_rooms, record)
    add_resolved_room(rooms, channel_id, default_name)
    return rooms


def needs_member_table(member_config):
    if "rooms" not in member_config:
        return True
    if not member_config.get("member_id") or not member_config.get("server_id"):
        return True
    rooms = member_config.get("rooms")
    if not rooms:
        return True
    if isinstance(rooms, dict):
        return any(not str(key).isdigit() for key in rooms)
    if isinstance(rooms, (list, tuple, set)):
        return any(not str(room).isdigit() for room in rooms)
    return not str(rooms).isdigit()


def resolve_member_config(member_config, table=None):
    config = dict(member_config)
    if not needs_member_table(config):
        config["rooms"] = {
            str(channel_id): normalize_room(room_config)
            for channel_id, room_config in config["rooms"].items()
        }
        config["server_id"] = str(config["server_id"])
        return config

    table = table if table is not None else load_member_table()
    record = find_member_record(config.get("name") or config.get("member_name"), table)
    if not record:
        missing = config.get("name") or config.get("member_name") or config
        raise RuntimeError(f"成员表里找不到 {missing}，请检查名字，或临时手动填写 member_id/server_id/rooms")

    config["name"] = config.get("name") or record.get("ownerName") or record.get("name")
    config["member_id"] = config.get("member_id") or get_record_member_id(record)
    config["server_id"] = str(config.get("server_id") or get_record_server_id(record))
    if "rooms" in config:
        config["rooms"] = build_rooms_from_member_record(record, config.get("rooms"))
    else:
        config["rooms"] = build_rooms_from_room_flags(record, config)

    if not config.get("member_id") or not config.get("server_id") or not config.get("rooms"):
        raise RuntimeError(f"{config.get('name')} 没有可监控房间，请至少开启 push_big_room 或 push_small_room")

    return config


def resolve_member_configs(member_configs=None):
    table = load_member_table()
    source_members = MEMBERS if member_configs is None else member_configs
    return [resolve_member_config(member_config, table) for member_config in source_members]


def get_config_file_mtime():
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        return os.path.getmtime(CONFIG_FILE)
    except OSError:
        return None


def reload_members_from_config(force=False, keep_on_error=True):
    global MEMBERS, config_file_mtime
    current_mtime = get_config_file_mtime()
    if not force and current_mtime == config_file_mtime:
        return False

    try:
        apply_runtime_config()
        raw_members = load_config_members()
        if not raw_members:
            raise RuntimeError("当前没有配置成员")
        resolved_members = resolve_member_configs(raw_members)
    except Exception as e:
        if keep_on_error and MEMBERS:
            print(f"\n[{get_current_datetime()}] 配置热加载失败，继续使用旧配置: {e}")
            return False
        raise

    MEMBERS = resolved_members
    config_file_mtime = current_mtime
    if not force:
        print(f"\n[{get_current_datetime()}] 已热加载配置: {' '.join([m['name'] for m in MEMBERS])}")
    return True


def print_member_matches(query):
    table = load_member_table(force_refresh=True)
    matches = search_member_records(query, table)
    if not matches:
        print(f"没有找到成员: {query}")
        return

    for record in matches:
        print(
            f"{record.get('ownerName') or record.get('name')} "
            f"member_id={get_record_member_id(record)} "
            f"server_id={get_record_server_id(record)} "
            f"大房间={get_record_big_room_id(record)} "
            f"小房间={get_record_small_room_id(record) or ''}"
        )


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


def get_message_datetime(item=None):
    if not isinstance(item, dict):
        return get_current_datetime()

    for key in ("msgTimeStr", "timeStr", "sendTimeStr", "createTimeStr"):
        value = item.get(key)
        if value:
            return str(value)

    for key in ("msgTime", "sendTime", "createTime", "time", "timestamp"):
        value = item.get(key)
        if value is None or value == "":
            continue
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            continue
        if timestamp > 1000000000000000:
            timestamp /= 1000000
        elif timestamp > 10000000000:
            timestamp /= 1000
        try:
            return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            continue

    return get_current_datetime()


def fix_url(url, media_type=None):
    if not url:
        return ""
    url = str(url).strip()
    if url.startswith("//"):
        return f"https:{url}"
    if not url.startswith("http"):
        if url.startswith(("/mediasource/", "/imagesource/")):
            host = "https://source.48.cn"
        elif media_type == "video":
            host = "https://mp4.48.cn"
        else:
            host = "https://source.48.cn"
        return f"{host}{url if url.startswith('/') else '/' + url}"
    return url


def try_parse_json(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            decoded = json.loads(value)
            parsed = try_parse_json(decoded)
            return parsed if parsed is not None else decoded
        except Exception:
            pass
    if not value or value[0] not in "{[":
        return None
    try:
        return json.loads(value)
    except Exception:
        try:
            cleaned = value.replace('\\"', '"').replace("\\\\", "\\")
            return json.loads(cleaned)
        except Exception:
            return None


IMAGE_MSG_TYPES = {"IMAGE", "EXPRESSIMAGE", "EXPRESS", "AGENT_WARMUP_IMG", "GIFT_SKILL_IMG", "CTM_IMG"}
AUDIO_MSG_TYPES = {"AUDIO", "AGENT_WARMUP_AUDIO", "AUDIO_REPLY", "GIFT_SKILL_AUDIO", "FLIPCARD_AUDIO"}
VIDEO_MSG_TYPES = {"VIDEO", "SHORTVIDEO", "AGENT_WARMUP_VIDEO", "SHARE_VIDEO", "GIFT_SKILL_VIDEO", "FLIPCARD_VIDEO"}
LIVE_MSG_TYPES = {"LIVEPUSH", "LIVE_PUSH", "SHARE_LIVE"}
REPLY_MSG_TYPES = {"REPLY", "AGENT_QCHAT_TEXT_REPLY"}
GIFT_REPLY_MSG_TYPES = {"GIFTREPLY", "AUDIO_GIFT_REPLY", "AGENT_QCHAT_GIFT_REPLY"}
FLIPCARD_MSG_TYPES = {"FLIPCARD", "FLIP", "FLIPCARD_QUESTION", "FLIPCARD_ANSWER"}
GIFT_TEXT_MSG_TYPES = {"GIFT_TEXT", "GIFT_SKILL_TEXT"}


def get_message_type(item):
    if not isinstance(item, dict):
        return ""
    for key in ("msgType", "messageType", "type", "message_type"):
        value = item.get(key)
        if value is not None and value != "":
            return str(value).upper()
    return ""


def get_message_body(item):
    if not isinstance(item, dict):
        return ""
    for key in ("msgContent", "bodys", "body", "content", "message", "msg"):
        value = item.get(key)
        if value is not None and value != "":
            return value
    return ""


def body_to_string(body, default=""):
    if body is None or body == "":
        return default
    if isinstance(body, (dict, list)):
        return json.dumps(body, ensure_ascii=False)
    return str(body)


def parse_body_data(body):
    parsed = try_parse_json(body)
    if parsed is not None:
        return parsed
    return body


def extract_text_value(data):
    parsed = try_parse_json(data)
    if parsed is not None:
        return extract_text_value(parsed)

    if data is None:
        return ""
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, (int, float)):
        return str(data)
    if isinstance(data, list):
        parts = [extract_text_value(item) for item in data]
        return "\n".join(part for part in parts if part)
    if not isinstance(data, dict):
        return ""

    text_keys = (
        "text", "msg", "message", "content", "body", "value", "desc",
        "description", "title", "answer", "answerText",
    )
    for key in text_keys:
        if key in data:
            text = extract_text_value(data.get(key))
            if text:
                return text

    skip_key_words = ("url", "path", "cover", "image", "audio", "voice", "video", "avatar", "head", "icon")
    for key, value in data.items():
        if any(word in str(key).lower() for word in skip_key_words):
            continue
        text = extract_text_value(value)
        if text:
            return text

    return ""


def get_url_path(url):
    text = str(url or "").split("?", 1)[0].split("#", 1)[0].lower()
    return text


def looks_like_media_url(url, media_type):
    path = get_url_path(url)
    if media_type == "image":
        return path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"))
    if media_type == "audio":
        return path.endswith((".aac", ".mp3", ".m4a", ".wav", ".amr", ".ogg"))
    if media_type == "video":
        return path.endswith((".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".flv", ".wmv", ".ts"))
    return False


def media_type_from_item(data):
    if not isinstance(data, dict):
        return ""
    value = (
        data.get("sourceType")
        or data.get("contentType")
        or data.get("type")
        or data.get("msgType")
        or data.get("messageType")
        or data.get("ext")
        or ""
    )
    text = str(value or "").upper()
    if "IMAGE" in text or text in ("JPG", "JPEG", "PNG", "WEBP", "GIF", "BMP"):
        return "image"
    if "AUDIO" in text or "VOICE" in text or text in ("AAC", "MP3", "M4A", "WAV", "AMR", "OGG"):
        return "audio"
    if "VIDEO" in text or "MOVIE" in text or text in ("MP4", "MOV", "M4V", "WEBM", "AVI", "MKV", "FLV", "WMV", "TS"):
        return "video"
    return ""


def is_probable_media_value(key_text, value, media_type, data=None):
    if not isinstance(value, str) or not value:
        return False
    lower_key = key_text.lower()
    hinted_type = media_type_from_item(data)
    if hinted_type and hinted_type != media_type and lower_key in ("url", "path"):
        return False
    if looks_like_media_url(value, media_type):
        return True
    if media_type == "image":
        return any(word in lower_key for word in ("image", "img", "pic", "photo", "cover", "poster", "preview"))
    if media_type == "audio":
        return any(word in lower_key for word in ("audio", "voice"))
    if media_type == "video":
        return any(word in lower_key for word in ("video", "mp4", "play"))
    return lower_key in ("url", "path")


def find_first_media_url(data, media_type="image"):
    if data is None:
        return ""

    image_keys = {
        "url", "imageUrl", "imgUrl", "picUrl", "pictureUrl", "photoUrl", "originalUrl",
        "originUrl", "bigUrl", "coverUrl", "coverPath", "cover", "roomCover",
        "liveCover", "previewImg", "poster", "path",
    }
    audio_keys = {"audioPath", "audioUrl", "voicePath", "voiceUrl", "url", "path"}
    video_keys = {"videoUrl", "videoPath", "mp4Url", "playUrl", "url", "path"} 

    if media_type == "image":
        target_keys = image_keys
    elif media_type == "audio":
        target_keys = audio_keys
    else:
        target_keys = video_keys
    skip_key_words = ("avatar", "head", "icon", "badge", "logo")

    if isinstance(data, str):
        parsed = try_parse_json(data)
        if parsed is not None:
            return find_first_media_url(parsed, media_type=media_type)
        if data.startswith(("http://", "https://", "/")):
            other_types = {"image", "audio", "video"} - {media_type}
            if any(looks_like_media_url(data, other_type) for other_type in other_types):
                return ""
            return fix_url(data, media_type=media_type)
        return ""

    if isinstance(data, list):
        for item in data:
            url = find_first_media_url(item, media_type=media_type)
            if url:
                return url
        return ""

    if isinstance(data, dict):
        content = data.get("content")
        if isinstance(content, dict):
            content_candidates = [
                content.get("voiceInfo"),
                content.get("audioInfo"),
                content.get("videoInfo"),
                content.get("imageInfo"),
                content.get("imgInfo"),
                content.get("pictureInfo"),
                content.get("replyInfo"),
            ]
            for candidate in content_candidates:
                url = find_first_media_url(candidate, media_type=media_type)
                if url:
                    return url

        for array_key in ("bodys", "body", "bodyList", "mediaList", "images", "imageList", "files"):
            value = data.get(array_key)
            parsed_value = parse_body_data(value)
            if parsed_value is not value:
                url = find_first_media_url(parsed_value, media_type=media_type)
                if url:
                    return url

        for key, value in data.items():
            key_text = str(key)
            if any(word in key_text.lower() for word in skip_key_words):
                continue
            if key_text in target_keys and is_probable_media_value(key_text, value, media_type, data=data):
                return fix_url(value, media_type=media_type)

        for key, value in data.items():
            key_text = str(key).lower()
            if any(word in key_text for word in skip_key_words):
                continue
            url = find_first_media_url(value, media_type=media_type)
            if url:
                return url

    return ""


def encode_qmsg_image_url(url):
    return quote(str(url), safe=":/?&")


def normalize_room(room_config):
    if isinstance(room_config, str):
        return {"name": room_config}
    return dict(room_config)


GENERIC_ROOM_NAMES = {"大房间", "小房间", "大", "小"}


def fetch_server_channel_names(member_config):
    server_id = str(member_config.get("server_id") or "")
    if not server_id:
        return {}
    if server_id in room_name_cache:
        return room_name_cache[server_id]

    try:
        resp = requests.post(
            SEINE_SERVER_DETAIL_API_URL,
            headers=get_headers(),
            json={"serverId": int(server_id)},
            timeout=8,
        )
        if resp.status_code != 200:
            room_name_cache[server_id] = {}
            return {}
        data = resp.json()
        if data.get("status") != 200:
            room_name_cache[server_id] = {}
            return {}
        content = data.get("content") or {}
        channels = content.get("channelInfoList") or []
        names = {}
        if isinstance(channels, list):
            for channel in channels:
                if not isinstance(channel, dict):
                    continue
                channel_id = channel.get("channelId")
                channel_name = str(channel.get("channelName") or "").strip()
                if channel_id and channel_name:
                    names[str(channel_id)] = channel_name
        room_name_cache[server_id] = names
        return names
    except Exception:
        room_name_cache[server_id] = {}
        return {}


def should_replace_room_name(room_config, channel_id):
    name = str(room_config.get("name") or "").strip()
    return not name or name in GENERIC_ROOM_NAMES or name == str(channel_id)


def get_push_target(member_config, room_config):
    push_mode = room_config.get("push_mode") or member_config.get("push_mode") or DEFAULT_PUSH_MODE
    target_qq = room_config.get("target_qq") or member_config.get("target_qq") or DEFAULT_TARGET_QQ
    return push_mode, str(target_qq)


def get_member_rooms(member_config):
    rooms = {
        str(channel_id): normalize_room(room_config)
        for channel_id, room_config in member_config["rooms"].items()
    }
    channel_names = fetch_server_channel_names(member_config)
    for channel_id, room_config in rooms.items():
        channel_name = channel_names.get(str(channel_id))
        if channel_name and should_replace_room_name(room_config, channel_id):
            room_config["name"] = channel_name
    return rooms


def fetch_member_messages(member_config):
    try:
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
    except Exception as e:
        raise RuntimeError(f"请求失败: {e}")


def fetch_room_message_details(member_config, channel_id, limit=50, fetch_all=False):
    url = ROOM_MSG_ALL_API_URL if fetch_all else ROOM_MSG_API_URL
    try:
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
    except Exception:
        return []


def find_latest_media_detail(member_config, channel_id, media_type="image"):
    candidates = []
    for fetch_all in (False, True):
        for index, detail in enumerate(fetch_room_message_details(member_config, channel_id, fetch_all=fetch_all)):
            if not is_member_message(member_config, detail):
                continue
            msg_type = get_message_type(detail)
            if media_type == "image" and msg_type not in IMAGE_MSG_TYPES:
                continue
            if media_type == "audio" and msg_type not in AUDIO_MSG_TYPES:
                continue
            if media_type == "video" and msg_type not in VIDEO_MSG_TYPES:
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
            msg_type = get_message_type(detail)
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
    user = item.get("user") or item.get("sender") or {}
    if isinstance(user, dict):
        name = user.get("starName") or user.get("realNickName") or user.get("nickName") or user.get("nickname") or user.get("userName") or user.get("name")
        if name:
            return name

    ext = try_parse_json(item.get("extInfo"))
    user = ext.get("user", {}) if isinstance(ext, dict) else {}
    return user.get("starName") or user.get("realNickName") or user.get("nickName") or user.get("nickname") or user.get("userName") or user.get("name") or ""


def get_message_sender_id(item):
    if not isinstance(item, dict):
        return ""

    for key in ("senderUserId", "senderId", "uid", "userId", "memberId", "starId"):
        value = item.get(key)
        if value:
            return str(value)

    user = item.get("user") or item.get("sender") or {}
    if isinstance(user, dict):
        for key in ("userId", "id", "memberId", "starId"):
            value = user.get(key)
            if value:
                return str(value)

    ext = try_parse_json(item.get("extInfo"))
    if isinstance(ext, dict):
        user = ext.get("user") or {}
        if isinstance(user, dict):
            for key in ("userId", "id", "memberId", "starId"):
                value = user.get(key)
                if value:
                    return str(value)
    return ""


def get_message_sender_real_name(item, fallback_name=""):
    sender_id = get_message_sender_id(item)
    record = find_member_record_by_id(sender_id)
    if not record:
        sender_name = get_message_sender_name(item)
        record = find_member_record(sender_name)
    if record:
        return str(record.get("ownerName") or record.get("name") or fallback_name or "")
    return fallback_name or ""


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
    user = item.get("user") or item.get("sender") or {}
    if isinstance(user, dict):
        sender_id = sender_id or str(user.get("userId") or user.get("id") or user.get("memberId") or user.get("starId") or "")
    if member_id and sender_id == member_id:
        return True

    sender_name = str(item.get("starName") or item.get("senderName") or get_message_sender_name(item) or "")
    if member_name and member_name in sender_name:
        return True

    return False


def get_push_cache_scope(member_config, room_config=None):
    room_config = room_config or {}
    push_mode, target_qq = get_push_target(member_config, room_config)
    return f"{member_config['server_id']}_{push_mode}_{target_qq}"


def get_message_cache_key(member_config, channel_id, content, item, room_config=None):
    cache_scope = get_push_cache_scope(member_config, room_config)
    for key in ("msgIdServer", "msgIdClient", "msgId", "messageId", "id", "msgTime", "sendTime", "createTime", "time", "msgTimeStr"):
        value = item.get(key)
        if value is not None and value != "":
            return f"{cache_scope}_{channel_id}_{key}_{value}"

    media_url = (
        find_first_media_url(item, media_type="image")
        or find_first_media_url(item, media_type="audio")
        or find_first_media_url(item, media_type="video")
    )
    if media_url:
        return f"{cache_scope}_{channel_id}_{media_url}"

    return f"{cache_scope}_{channel_id}_{content}"


def get_room_cache_key(member_config, channel_id, room_config=None):
    return f"room_init_{get_push_cache_scope(member_config, room_config)}_{channel_id}"


def has_cached_room_messages(member_config, channel_id, room_config=None):
    prefix = f"{get_push_cache_scope(member_config, room_config)}_{channel_id}_"
    return any(str(key).startswith(prefix) for key in last_msg_cache)


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


def find_named_payload(data, names):
    if data is None:
        return None
    parsed = try_parse_json(data)
    if parsed is not None:
        return find_named_payload(parsed, names)
    if isinstance(data, list):
        for item in data:
            found = find_named_payload(item, names)
            if found is not None:
                return found
        return None
    if isinstance(data, dict):
        for name in names:
            value = data.get(name)
            parsed_value = try_parse_json(value)
            if isinstance(parsed_value, dict):
                return parsed_value
            if isinstance(value, dict):
                return value
        for value in data.values():
            found = find_named_payload(value, names)
            if found is not None:
                return found
    return None


def get_flipcard_payload(content, raw_item=None):
    parsed_content = parse_body_data(content)
    info = find_named_payload(
        [parsed_content, raw_item],
        ("flipCardInfo", "filpCardInfo", "flipCardAudioInfo", "filpCardAudioInfo", "flipCardVideoInfo", "filpCardVideoInfo"),
    )
    if info:
        return info
    return parsed_content if isinstance(parsed_content, dict) else {}


def get_reply_payload(content, raw_item=None):
    parsed_content = parse_body_data(content)
    reply_info = find_named_payload([parsed_content, raw_item], ("replyInfo",))
    if not isinstance(reply_info, dict):
        reply_info = {}
    parsed_dict = parsed_content if isinstance(parsed_content, dict) else {}
    return parsed_dict, reply_info


REPLY_META_KEYS = {
    "replyInfo", "quoteInfo", "sourceInfo", "targetInfo", "originInfo", "originalInfo",
    "replyName", "replyText", "targetName", "targetText", "sourceName", "sourceText",
}

REPLY_MARKER_TEXTS = {"REPLY", "GIFTREPLY", "AUDIO_REPLY", "AGENT_QCHAT_TEXT_REPLY", "[回复消息]", "回复消息"}
REPLY_SKIP_KEYS = {
    "msgType", "messageType", "type", "msgTimeStr", "timeStr", "sendTimeStr", "createTimeStr",
    "msgTime", "sendTime", "createTime", "time", "timestamp", "msgIdServer", "msgIdClient",
    "msgId", "messageId", "id", "channelId", "serverId", "senderUserId", "senderId", "uid",
    "starName", "senderName", "nickName", "nickname", "userName", "name", "roleId", "extInfo",
}


def strip_reply_metadata(data):
    parsed = try_parse_json(data)
    if parsed is not None:
        return strip_reply_metadata(parsed)
    if isinstance(data, list):
        return [strip_reply_metadata(item) for item in data]
    if isinstance(data, dict):
        return {
            key: strip_reply_metadata(value)
            for key, value in data.items()
            if key not in REPLY_META_KEYS
        }
    return data


def compact_text(value):
    return "".join(str(value or "").split())


def is_quoted_reply_text(text, reply_text):
    text_key = compact_text(text)
    reply_key = compact_text(reply_text)
    return bool(text_key and reply_key and (text_key == reply_key or text_key in reply_key or reply_key in text_key))


def is_reply_marker_text(text):
    return str(text or "").strip().upper() in REPLY_MARKER_TEXTS


def find_reply_text_value(data, reply_text=""):
    parsed = try_parse_json(data)
    if parsed is not None:
        return find_reply_text_value(parsed, reply_text=reply_text)

    if data is None:
        return ""
    if isinstance(data, str):
        text = data.strip()
        if is_reply_marker_text(text):
            return ""
        return "" if is_quoted_reply_text(text, reply_text) else text
    if isinstance(data, (int, float)):
        return str(data)
    if isinstance(data, list):
        for item in data:
            text = find_reply_text_value(item, reply_text=reply_text)
            if text:
                return text
        return ""
    if not isinstance(data, dict):
        return ""

    text_keys = (
        "answer", "answerText", "messageText", "bodyText", "replyContent",
        "sendMsg", "sendText", "text", "msg", "message", "content", "body", "value",
    )
    for key in text_keys:
        if key not in data:
            continue
        value = data.get(key)
        if isinstance(value, (dict, list)):
            text = find_reply_text_value(value, reply_text=reply_text)
        else:
            text = extract_text_value(value)
        if text and not is_quoted_reply_text(text, reply_text):
            return text

    skip_key_words = (
        "url", "path", "cover", "image", "audio", "voice", "video",
        "avatar", "head", "icon", "reply", "quote", "source", "target", "origin",
    )
    for key, value in data.items():
        key_text = str(key).lower()
        if key in REPLY_META_KEYS or key in REPLY_SKIP_KEYS or any(word in key_text for word in skip_key_words):
            continue
        text = find_reply_text_value(value, reply_text=reply_text)
        if text:
            return text

    return ""


def get_reply_member_content(parsed, reply_info, raw_item=None):
    reply_text = extract_text_value(
        reply_info.get("replyText")
        or reply_info.get("sourceText")
        or reply_info.get("targetText")
        or ""
    )

    own_payload = strip_reply_metadata(parsed)
    raw_own_payload = strip_reply_metadata(raw_item or {})

    text = (
        find_reply_text_value(reply_info, reply_text=reply_text)
        or find_reply_text_value((raw_item or {}).get("replyInfo"), reply_text=reply_text)
        or find_reply_text_value(own_payload, reply_text=reply_text)
        or find_reply_text_value(raw_own_payload, reply_text=reply_text)
    )
    if text:
        return text

    for media_type, label in (("image", "image"), ("audio", "语音消息"), ("video", "视频消息")):
        media_url = find_first_media_url(own_payload, media_type=media_type) or find_first_media_url(raw_own_payload, media_type=media_type)
        if not media_url:
            continue
        if media_type == "image":
            return f"@image={encode_qmsg_image_url(media_url)}@"
        return f"[{label}] {media_url}"

    return ""


def get_gift_reply_payload(content, raw_item=None):
    parsed_content = parse_body_data(content)
    info = find_named_payload([parsed_content, raw_item], ("giftReplyInfo",))
    if info:
        return info
    return parsed_content if isinstance(parsed_content, dict) else {}


def build_live_content(raw_item):
    body_data = parse_body_data((raw_item or {}).get("bodys") or (raw_item or {}).get("msgContent") or "")
    if isinstance(body_data, dict):
        info = body_data.get("livePushInfo") or body_data.get("liveInfo") or body_data
        if isinstance(info, dict):
            return json.dumps({
                "liveTitle": info.get("liveTitle") or info.get("title") or "",
                "liveCover": info.get("liveCover") or info.get("cover") or info.get("coverUrl") or "",
            }, ensure_ascii=False)
    return "[直播消息]"


def parse_msg_content(raw_content, raw_item=None):
    raw_text = body_to_string(raw_content)
    try:
        parsed_content = parse_body_data(raw_content)
        msg_type = get_message_type(raw_item or {})

        if "[图片消息]" in raw_text or msg_type in IMAGE_MSG_TYPES:
            image_url = find_first_media_url(parsed_content, media_type="image") or find_first_media_url(raw_item, media_type="image")
            if image_url:
                return f"@image={encode_qmsg_image_url(image_url)}@"
            return "[图片消息] 未找到图片链接"

        if "[语音消息]" in raw_text or msg_type in AUDIO_MSG_TYPES:
            audio_url = find_first_media_url(parsed_content, media_type="audio") or find_first_media_url(raw_item, media_type="audio")
            if audio_url:
                return f"[语音消息] {audio_url}"
            return raw_text

        if "[视频消息]" in raw_text or msg_type in VIDEO_MSG_TYPES:
            video_url = find_first_media_url(parsed_content, media_type="video") or find_first_media_url(raw_item, media_type="video")
            if video_url:
                return f"[视频消息] {video_url}"
            cover_url = find_first_media_url(parsed_content, media_type="image") or find_first_media_url(raw_item, media_type="image")
            if cover_url:
                return f"[视频消息] {cover_url}"
            return "[视频消息] 未找到视频链接"

        if parsed_content is not None and not isinstance(parsed_content, str):
            if isinstance(parsed_content, dict) and (
                parsed_content.get("ext") in ("mp4", "mov", "avi", "mkv", "webm", "flv", "wmv", "ts")
                or "dur" in parsed_content
                or media_type_from_item(parsed_content) == "video"
            ):
                video_url = find_first_media_url(parsed_content, media_type="video")
                if video_url:
                    return f"[视频消息] {video_url}"
            image_url = find_first_media_url(parsed_content, media_type="image")
            if image_url:
                return f"@image={encode_qmsg_image_url(image_url)}@"
            audio_url = find_first_media_url(parsed_content, media_type="audio")
            if audio_url:
                return f"[语音消息] {audio_url}"
            video_url = find_first_media_url(parsed_content, media_type="video")
            if video_url:
                return f"[视频消息] {video_url}"
            text = extract_text_value(parsed_content)
            if text:
                return text
    except Exception:
        pass
    return raw_text


def format_sender_label(sender_nick, member_name):
    nick = str(sender_nick or "").strip()
    real_name = str(member_name or "").strip()
    if nick and real_name and nick != real_name and real_name not in nick:
        return f"{nick}({real_name})"
    return nick or real_name


def send_qmsg_rich(member_config, room_config, sender_nick, content, is_live=False, raw_item=None):
    member_name = member_config["name"]
    room_name = room_config["name"]
    push_mode, target_qq = get_push_target(member_config, room_config)
    msg_type = get_message_type(raw_item or {})
    msg_time = get_message_datetime(raw_item)
    sender_real_name = get_message_sender_real_name(raw_item or {}, fallback_name=member_name)
    sender_label = format_sender_label(sender_nick, sender_real_name)

    if not QMSG_KEY:
        print(" 失败: 请先填写 Qmsg KEY")
        return
    if not target_qq:
        print(f" 失败: {member_name}/{room_name} 未配置 target_qq")
        return

    print(f"正在推送到 {target_qq}...", end="")

    # 1. 翻牌消息
    if raw_item and msg_type in FLIPCARD_MSG_TYPES:
        parsed = get_flipcard_payload(content, raw_item)
        if parsed and isinstance(parsed, dict):
            question = extract_text_value(parsed.get("question") or parsed.get("questionText") or parsed.get("q") or "")
            answer = (
                extract_text_value(parsed.get("answer") or parsed.get("answerText") or parsed.get("a") or "")
                or parse_msg_content(parsed, raw_item=raw_item)
            )
            msg_body = (
                f"【{room_name} | {member_name}】\n"
                f"公开翻牌问题：{question or '[未识别问题]'}\n"
                f"{sender_label}：{answer}\n"
                f"{msg_time}"
            )
        else:
            msg_body = (
                f"【{room_name} | {member_name}】\n"
                f"{sender_label}：{content}\n"
                f"{msg_time}"
            )
    # 2. 回复消息
    elif raw_item and msg_type in REPLY_MSG_TYPES:
        parsed, reply_info = get_reply_payload(content, raw_item)
        if isinstance(parsed, dict) and (parsed or reply_info):
            reply_name = reply_info.get("replyName") or reply_info.get("nickName") or parsed.get("replyName") or parsed.get("targetName") or ""
            reply_text = extract_text_value(
                reply_info.get("replyText")
                or reply_info.get("sourceText")
                or reply_info.get("targetText")
                or parsed.get("replyText")
                or parsed.get("sourceText")
                or parsed.get("targetText")
                or ""
            )

            text = get_reply_member_content(parsed, reply_info, raw_item)
            if not text:
                text = "[未识别回复正文]"

            msg_body = (
                f"【{room_name} | {member_name}】\n"
                f"回复 {reply_name or '被回复消息'}：{reply_text or '[未识别引用内容]'}\n"
                f"{sender_label}：{text}\n"
                f"{msg_time}"
            )
        else:
            msg_body = (
                f"【{room_name} | {member_name}】\n"
                f"{sender_label}：{content}\n"
                f"{msg_time}"
            )
    # 3. 礼物感谢回复
    elif raw_item and msg_type in GIFT_REPLY_MSG_TYPES:
        info = get_gift_reply_payload(content, raw_item)
        if info and isinstance(info, dict):
            reply_name = info.get("replyName") or info.get("nickName") or info.get("sourceName") or ""
            reply_text = extract_text_value(info.get("replyText") or info.get("sourceText") or info.get("targetText") or "")
            member_reply = extract_text_value(
                info.get("text")
                or info.get("messageText")
                or info.get("bodyText")
                or info.get("replyContent")
                or info.get("answer")
                or info.get("answerText")
                or ""
            )
            voice_url = find_first_media_url(info, "audio")
            video_url = find_first_media_url(info, "video")
            image_url = find_first_media_url(info, "image")
            if member_reply:
                pass
            elif voice_url:
                member_reply = f"[语音消息] {voice_url}"
            elif video_url:
                member_reply = f"[视频消息] {video_url}"
            elif image_url:
                member_reply = f"@image={encode_qmsg_image_url(image_url)}@"
            else:
                member_reply = "[未识别礼物回复正文]"
            msg_body = (
                f"【{room_name} | {member_name}】\n"
                f"{reply_name}：{reply_text}\n"
                f"{sender_label}：{member_reply}\n"
                f"{msg_time}"
            )
        else:
            msg_body = (
                f"【{room_name} | {member_name}】\n"
                f"{sender_label}：{content}\n"
                f"{msg_time}"
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
            f"【{room_name} | {member_name}】\n"
            f"{member_name}直播啦~\n"
            f"标题：{live_title}\n"
            f"类型：{live_type}\n"
            f"{cover_code}\n"
            f"{msg_time}"
        )
    # 5. 普通消息
    else:
        final_content = parse_msg_content(content, raw_item=raw_item)
        msg_body = (
            f"【{room_name} | {member_name}】\n"
            f"{sender_label}：{final_content}\n"
            f"{msg_time}"
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
    content = body_to_string(get_message_body(item))
    raw_item = item
    star_name = get_message_sender_name(item)
    is_live_msg = False
    msg_type = get_message_type(item)

    if "[表情消息]" in content:
        content = "[图片消息]"

    if "[直播消息]" in content or room_config.get("is_live") or msg_type in LIVE_MSG_TYPES:
        is_live_msg = True
        content = build_live_content(item)
    elif "[图片消息]" in content or msg_type in IMAGE_MSG_TYPES:
        detail = find_latest_media_detail(member_config, channel_id, media_type="image")
        if detail:
            raw_item = detail
            star_name = get_message_sender_name(detail) or star_name
            content = "[图片消息]"
    elif "[语音消息]" in content or msg_type in AUDIO_MSG_TYPES:
        detail = find_latest_media_detail(member_config, channel_id, media_type="audio")
        if detail:
            raw_item = detail
            star_name = get_message_sender_name(detail) or star_name
            content = "[语音消息]"
    elif "[视频消息]" in content or msg_type in VIDEO_MSG_TYPES:
        detail = find_latest_media_detail(member_config, channel_id, media_type="video")
        if detail:
            raw_item = detail
            star_name = get_message_sender_name(detail) or star_name
            content = "[视频消息]"
    elif "[礼物回复消息]" in content or msg_type in GIFT_REPLY_MSG_TYPES:
        detail = find_latest_detail_by_type(member_config, channel_id, GIFT_REPLY_MSG_TYPES)
        if detail:
            raw_item = detail
            star_name = get_message_sender_name(detail) or star_name
            content = body_to_string(get_message_body(detail), "[礼物回复消息]")
    elif msg_type in FLIPCARD_MSG_TYPES | REPLY_MSG_TYPES:
        detail = find_latest_detail_by_type(member_config, channel_id, FLIPCARD_MSG_TYPES | REPLY_MSG_TYPES)
        if detail:
            raw_item = detail
            star_name = get_message_sender_name(detail) or star_name
            content = body_to_string(get_message_body(detail), content)

    msg_key = get_message_cache_key(member_config, channel_id, content, raw_item, room_config=room_config)
    return room_config, star_name, content, is_live_msg, msg_key, raw_item


def get_detail_message_content(detail):
    msg_type = get_message_type(detail)
    body = get_message_body(detail)
    parsed_body = parse_body_data(body)

    if msg_type in IMAGE_MSG_TYPES:
        return "[图片消息]"
    if msg_type in GIFT_REPLY_MSG_TYPES:
        return body_to_string(parsed_body, "[礼物回复消息]")
    if msg_type in VIDEO_MSG_TYPES:
        return "[视频消息]"
    if msg_type in AUDIO_MSG_TYPES:
        return "[语音消息]"
    if msg_type in LIVE_MSG_TYPES:
        return build_live_content(detail)
    if msg_type in FLIPCARD_MSG_TYPES | REPLY_MSG_TYPES:
        return body_to_string(parsed_body, "")
    if msg_type in GIFT_TEXT_MSG_TYPES:
        return body_to_string(parsed_body, "")
    if parsed_body is not body:
        text = extract_text_value(parsed_body)
        if text:
            return text
        image_url = find_first_media_url(parsed_body, "image")
        audio_url = find_first_media_url(parsed_body, "audio")
        video_url = find_first_media_url(parsed_body, "video")
        if image_url:
            return "[图片消息]"
        if audio_url:
            return "[语音消息]"
        if video_url:
            return "[视频消息]"
        return body_to_string(parsed_body, "")
    if media_type_from_item(detail) == "image":
        return "[图片消息]"
    if media_type_from_item(detail) == "audio":
        return "[语音消息]"
    if media_type_from_item(detail) == "video":
        return "[视频消息]"
    return body_to_string(body, "")


def get_detail_message_payload(member_config, room_config, channel_id, detail):
    content = get_detail_message_content(detail)
    msg_type = get_message_type(detail)
    is_live_msg = (
        "[直播消息]" in content
        or room_config.get("is_live")
        or msg_type in LIVE_MSG_TYPES
    )
    if is_live_msg and "[直播消息]" in content:
        content = "[直播消息]"

    star_name = get_message_sender_name(detail) or member_config["name"]
    msg_key = get_message_cache_key(member_config, channel_id, content, detail, room_config=room_config)
    return room_config, star_name, content, is_live_msg, msg_key, detail


def collect_test_candidates(member_config, limit):
    rooms = get_member_rooms(member_config)
    candidates_by_key = {}

    for channel_id, room_config in rooms.items():
        room_details = []
        room_details.extend(
            fetch_room_message_details(member_config, channel_id, limit=max(limit, 20), fetch_all=False)
        )

        for index, detail in enumerate(room_details):
            if not is_member_message(member_config, detail):
                continue
            payload = get_detail_message_payload(member_config, room_config, channel_id, detail)
            _, _, content, _, msg_key, _ = payload
            if not content.strip():
                continue
            candidates_by_key[msg_key] = (get_msg_sort_value(detail, index), payload)

    try:
        for index, item in enumerate(fetch_member_messages(member_config)):
            payload = get_message_payload(member_config, rooms, item)
            if not payload:
                continue
            _, _, _, _, msg_key, _ = payload
            candidates_by_key.setdefault(msg_key, (get_msg_sort_value(item, index), payload))
    except Exception:
        pass

    return list(candidates_by_key.values())


def monitor_member(member_config, is_silent_init=False):
    global last_msg_cache
    try:
        rooms = get_member_rooms(member_config)
        need_save = False
        
        for channel_id, room_config in rooms.items():
            msg_list = fetch_room_message_details(member_config, channel_id, limit=10, fetch_all=False)
            room_cache_key = get_room_cache_key(member_config, channel_id, room_config=room_config)
            is_new_room_cache = room_cache_key not in last_msg_cache and not has_cached_room_messages(member_config, channel_id, room_config=room_config)
            initialized_count = 0
            
            for detail in reversed(msg_list):
                if not is_member_message(member_config, detail):
                    continue
                    
                payload = get_detail_message_payload(member_config, room_config, channel_id, detail)
                _, star_name, content, is_live_msg, msg_key, raw_item = payload
                
                if not content.strip():
                    continue
                    
                if msg_key in last_msg_cache:
                    continue

                last_msg_cache[msg_key] = True
                need_save = True
                if is_new_room_cache or is_silent_init:
                    initialized_count += 1
                    continue

                print("\n" + "-" * 30)
                print(f"[{get_current_datetime()}] {member_config['name']} 新动态: {content}")
                send_qmsg_rich(member_config, room_config, star_name, content, is_live=is_live_msg, raw_item=raw_item)
                print("-" * 30 + "\n")

            if room_cache_key not in last_msg_cache:
                last_msg_cache[room_cache_key] = True
                need_save = True
                if is_new_room_cache and not is_silent_init:
                    print(
                        f"\n[{get_current_datetime()}] {member_config['name']}/{room_config['name']} "
                        f"首次加入监控，已缓存最近 {initialized_count} 条历史消息，后续新消息才会推送"
                    )
        
        if need_save:
            if len(last_msg_cache) > 2000:
                keys = list(last_msg_cache.keys())
                last_msg_cache = {k: last_msg_cache[k] for k in keys[-1000:]}
            save_cache()

    except RuntimeError as e:
        print(f"\n[{get_current_datetime()}] {member_config['name']} 业务提示: {e}")
    except Exception as e:
        print(f"\n[{get_current_datetime()}] {member_config['name']} 发生网络或未知异常: {e}")


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


def start_monitor_from_config(return_to_menu=False):
    try:
        reload_members_from_config(force=True, keep_on_error=False)
    except Exception:
        print("当前没有配置成员，请先添加，例如:")
        print("python push.py --add-member 陈琳 --target-qq 829068921 --push-mode group")
        return

    print("=" * 50)
    print("牙牙推送 By yk1z")
    print(f"已添加成员数: {len(MEMBERS)}")
    print(f"已添加成员: {' '.join([m['name'] for m in MEMBERS])}")
    print(f"刷新频率: {CHECK_INTERVAL}秒/次")
    print("=" * 50)

    load_cache()

    if not last_msg_cache:
        print("正在初始化新缓存(跳过历史消息)...", end="")
        monitor_once(is_silent_init=True)
        save_cache()
        print(" 完成")
    else:
        print(f"成功恢复本地缓存 {len(last_msg_cache)} 条")

    print("-" * 50)

    try:
        while True:
            stop_text = "返回菜单" if return_to_menu else "停止"
            print(f"\r[{get_current_datetime().split(' ')[1]}] 正在监控中 (Ctrl+C {stop_text})", end="", flush=True)
            reload_members_from_config(keep_on_error=True)
            monitor_once(is_silent_init=False)
            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        print()
        if return_to_menu:
            print("已返回配置菜单")
            return
        raise


def main():
    global MEMBERS
    parser = argparse.ArgumentParser(description="口袋48成员房间消息推送")
    parser.add_argument("--test", action="store_true", help="抓取每个成员目标房间的最新消息并推送")
    parser.add_argument("--test-limit", type=int, default=10, help="测试推送条数，默认 10")
    parser.add_argument("--debug-latest", action="store_true", help="打印已配置房间的最新消息原始数据，用于排查图片字段")
    parser.add_argument("--find-member", help="按姓名/拼音搜索内置成员表")
    parser.add_argument("--menu", action="store_true", help="打开交互式配置菜单")
    parser.add_argument("--run", action="store_true", help="开始监控推送")
    parser.add_argument("--list-config", action="store_true", help="列出当前推送成员配置")
    parser.add_argument("--add-member", help="添加或更新推送成员，例如 --add-member 陈琳 --target-qq 829068921")
    parser.add_argument("--remove-member", help="从推送配置中删除成员")
    parser.add_argument("--push-mode", choices=("group", "private"), help="添加成员时的推送模式: group/private")
    parser.add_argument("--target-qq", help="目标群号或 QQ 号；添加时用于设置，删除同名多目标时用于指定")
    big_room_group = parser.add_mutually_exclusive_group()
    big_room_group.add_argument("--big-room", dest="push_big_room", action="store_true", default=None, help="添加成员时开启大房间推送")
    big_room_group.add_argument("--no-big-room", dest="push_big_room", action="store_false", help="添加成员时关闭大房间推送")
    small_room_group = parser.add_mutually_exclusive_group()
    small_room_group.add_argument("--small-room", dest="push_small_room", action="store_true", default=None, help="添加成员时开启小房间推送")
    small_room_group.add_argument("--no-small-room", dest="push_small_room", action="store_false", help="添加成员时关闭小房间推送")
    args = parser.parse_args()

    if len(sys.argv) == 1:
        interactive_config_menu()
        return

    if args.find_member:
        print_member_matches(args.find_member)
        return

    if args.menu:
        interactive_config_menu()
        return

    if args.add_member:
        add_config_member(args)
        return

    if args.remove_member:
        remove_config_member(args.remove_member, target_qq=args.target_qq)
        return

    if args.list_config:
        print_config_members()
        return

    if not (args.run or args.test or args.debug_latest):
        interactive_config_menu()
        return

    apply_runtime_config()
    raw_members = load_config_members()
    if not raw_members:
        print("当前没有配置成员，请先添加，例如:")
        print("python push.py --add-member 陈琳 --target-qq 829068921 --push-mode group")
        return

    MEMBERS = resolve_member_configs(raw_members)

    if args.debug_latest:
        debug_latest_messages()
        return

    if args.test:
        test_push_latest_once(limit=max(1, args.test_limit))
        return

    start_monitor_from_config()


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"\n错误: {e}")
    except KeyboardInterrupt:
        print("\n\n已手动停止")
