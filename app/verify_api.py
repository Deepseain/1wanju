# -*- coding: utf-8 -*-
import json, subprocess, sys

BASE = "http://127.0.0.1:5000"

def curl(args):
    r = subprocess.run(["curl", "-s"] + args, capture_output=True)
    return r.stdout.decode("utf-8", "replace")

login = json.loads(curl(["-X", "POST", BASE + "/api/login",
                         "-H", "Content-Type: application/json",
                         "-d", '{"username":"admin","password":"admin123"}']))
token = login["token"]
auth = ["-H", "Authorization: Bearer " + token]
print("登录 OK:", login)

d = json.loads(curl(["-X", "GET?".replace("?","")] + [BASE + "/api/dashboard"] + auth))
print("meta:", d["meta"])
print("kpi:", json.dumps(d["kpi"], ensure_ascii=False))
print("daily 天数:", len(d["daily"]), "| 首日:", d["daily"][0], "| 末日:", d["daily"][-1])
print("Top1:", json.dumps(d["top_products"][0], ensure_ascii=False))
print("门店:", [(s["name"], s["revenue"]) for s in d["stores"]])
print("支付:", d["payments"])
print("品类:", [(c["name"], c["value"]) for c in d["categories"]])

d7 = json.loads(curl([BASE + "/api/dashboard?start=2026-07-25&end=2026-07-31"] + auth))
print("近7天 kpi:", json.dumps(d7["kpi"], ensure_ascii=False), "| daily天数:", len(d7["daily"]))

noauth = curl(["-o", "/dev/null", "-w", "%{http_code}", BASE + "/api/dashboard"])
print("未带token访问 dashboard:", noauth)

chat = json.loads(curl(["-X", "POST", BASE + "/api/ai/chat", "-H", "Content-Type: application/json"] + auth +
                       ["-d", '{"messages":[{"role":"user","content":"上周哪家店营业额最高"}]}']))
print("AI占位回复:", chat["reply"][:40], "...")

logout = curl(["-X", "POST", BASE + "/api/logout"] + auth)
after = curl(["-o", "/dev/null", "-w", "%{http_code}", BASE + "/api/dashboard"] + auth)
print("登出后再访问:", after, "(预期401)")
