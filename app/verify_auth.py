# -*- coding: utf-8 -*-
"""验证：用户表迁移 + 注册 + 登录 + 看板 + AI + 登出"""
import json, subprocess, time

BASE = "http://127.0.0.1:5000"

def curl(args):
    r = subprocess.run(["curl", "-s"] + args, capture_output=True)
    return r.stdout.decode("utf-8", "replace")

# 等服务起来
for _ in range(10):
    try:
        subprocess.run(["curl", "-s", "-o", "/dev/null", BASE + "/"],
                       capture_output=True, timeout=2)
        break
    except Exception:
        time.sleep(1)

# 1. 查看用户表是否迁移成功（通过 /api/me 间接验证）
print("=" * 50)
print("1. 登录 admin/admin123")
login = json.loads(curl(["-X", "POST", BASE + "/api/login",
                         "-H", "Content-Type: application/json",
                         "-d", '{"username":"admin","password":"admin123"}']))
print("   ->", login)
admin_token = login.get("token", "")
auth = ["-H", "Authorization: Bearer " + admin_token]

print("\n2. /api/me 验证 nickname/role 字段（证明表迁移成功）")
me = json.loads(curl([BASE + "/api/me"] + auth))
print("   ->", me)

# 3. 注册新用户（若已存在则改用登录）
print("\n3. 注册新用户 testuser/abc123")
reg_raw = curl(["-X", "POST", BASE + "/api/register",
                "-H", "Content-Type: application/json",
                "-d", '{"username":"testuser","password":"abc123","confirm":"abc123","nickname":"测试用户"}'])
reg = json.loads(reg_raw)
print("   ->", reg_raw)
if not reg.get("token"):
    print("   (已存在，改用登录)")
    reg = json.loads(curl(["-X", "POST", BASE + "/api/login",
                           "-H", "Content-Type: application/json",
                           "-d", '{"username":"testuser","password":"abc123"}']))
    print("   登录 ->", reg)
new_token = reg.get("token", "")
new_auth = ["-H", "Authorization: Bearer " + new_token]

# 4. 重复注册同名用户（应 409）
print("\n4. 重复注册 testuser（预期 409）")
dup = curl(["-X", "POST", BASE + "/api/register",
            "-H", "Content-Type: application/json",
            "-d", '{"username":"testuser","password":"abc123","confirm":"abc123"}'])
print("   ->", dup)

# 5. 注册密码不一致
print("\n5. 注册密码不一致（预期 400）")
bad = curl(["-X", "POST", BASE + "/api/register",
            "-H", "Content-Type: application/json",
            "-d", '{"username":"user2","password":"abc123","confirm":"xyz"}'])
print("   ->", bad)

# 6. 新用户访问看板
print("\n6. 新用户访问 /api/dashboard")
dash = json.loads(curl([BASE + "/api/dashboard"] + new_auth))
print("   -> KPI:", json.dumps(dash["kpi"], ensure_ascii=False))

# 7. 新用户访问 AI
print("\n7. 新用户访问 /api/ai/chat")
chat = json.loads(curl(["-X", "POST", BASE + "/api/ai/chat",
                        "-H", "Content-Type: application/json"] + new_auth +
                       ["-d", '{"messages":[{"role":"user","content":"你好"}]}']))
print("   ->", chat["reply"][:30], "...")

# 8. 未登录访问
print("\n8. 未登录访问 dashboard（预期 401）")
code = curl(["-o", "/dev/null", "-w", "%{http_code}", BASE + "/api/dashboard"])
print("   -> HTTP", code)

print("\n" + "=" * 50)
print("全部验证完成")
