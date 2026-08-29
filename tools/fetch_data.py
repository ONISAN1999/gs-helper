#!/usr/bin/env python3
"""
ดึงข้อมูลเซ็ตปัจจุบัน + คอมพ์ยอดนิยม แล้วเขียนลง data/
รันบน GitHub Actions (runner ต่อเน็ตได้เต็มที่)

  data/set.json    - ยูนิต 65 ตัว / เผ่า / ไอเทมหลัก (CommunityDragon)
  data/comps.json  - คอมพ์ + เทียร์ + แผนเลเวล (emblemcomp.gg)
  data/status.json - สถานะการดึงล่าสุด
"""

import json, os, re, sys, html, urllib.request, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
CDRAGON = "https://raw.communitydragon.org/latest/cdragon/tft/en_us.json"
TIERLIST = "https://emblemcomp.gg/tier-list"

# ยูนิตของเซ็ตปัจจุบันใช้ id ขึ้นต้น DA_ ; TFT_/TFT17_ ฯลฯ คือของเก่าและตัวอสูรที่ถูกเรียก
SET_PREFIX = "DA_"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def get(url, timeout=180):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/json,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------- ข้อมูลเซ็ต ----------

def pick_set(raw):
    best = None
    for k, v in (raw.get("sets") or {}).items():
        try:
            n = float(k)
        except ValueError:
            continue
        if best is None or n > best[0]:
            best = (n, v)
    if best:
        return best[0], best[1]
    raise RuntimeError("หาเซ็ตใน CommunityDragon ไม่เจอ")


def build_set():
    raw = json.loads(get(CDRAGON))
    num, s = pick_set(raw)

    units = []
    for c in s.get("champions", []):
        cid, name, cost = c.get("apiName") or "", c.get("name"), c.get("cost")
        # ตัดออก: ตัวเรียก/หุ่นซ้อม (ไม่มีเผ่า), ยูนิตเซ็ตเก่า, ร่างแปลงของ Lux เช่น "Lux (Fae)"
        if not name or cost is None or cost > 5:
            continue
        if not cid.startswith(SET_PREFIX) or not (c.get("traits") or []) or "(" in name:
            continue
        ab = c.get("ability") or {}
        units.append({
            "id": cid, "name": name, "cost": cost,
            "traits": c.get("traits") or [],
            "skill": ab.get("name"),
            "skillDesc": (ab.get("desc") or "")[:400],
            "icon": c.get("squareIcon") or c.get("icon"),
        })

    traits = [{
        "id": t.get("apiName"), "name": t.get("name"),
        "desc": (t.get("desc") or "")[:400],
        "tiers": [e.get("minUnits") for e in (t.get("effects") or [])],
    } for t in s.get("traits", []) if t.get("name")]

    # ไอเทม: เอาเฉพาะของเซ็ตนี้ แยกเป็นของหลัก / ตราเผ่า / ชิ้นส่วน
    da = [i for i in raw.get("items", []) if (i.get("apiName") or "").startswith(SET_PREFIX) and i.get("name")]
    craft = [i for i in da if len(i.get("composition") or []) == 2]
    comp_ids = {c for i in craft for c in i["composition"]}
    parts = [i for i in da if i.get("apiName") in comp_ids]

    def row(i, kind):
        return {"id": i.get("apiName"), "name": i["name"], "kind": kind,
                "from": i.get("composition") or [], "desc": (i.get("desc") or "")[:300],
                "icon": i.get("icon")}

    items = ([row(i, "component") for i in parts] +
             [row(i, "emblem" if "Emblem" in i["name"] else "core") for i in craft])

    return {"set": num, "fetchedAt": now(),
            "units": sorted(units, key=lambda u: (u["cost"], u["name"])),
            "traits": traits, "items": items}


# ---------- คอมพ์จาก emblemcomp.gg ----------

CHAMP_IMG = re.compile(r'icons%2Fchampions%2F[^"\']*?&amp;w=(\d+)[^"\']*?"[^>]*?alt="([^"]+)"')
ALT_IMG = re.compile(r'alt="([^"]+)"[^>]*?src="[^"]*?icons%2Fchampions%2F[^"]*?w=(\d+)')
ITEM_IMG = re.compile(r'icons%2Fitems%2F[^"\']*?"[^>]*?title="([^"]+)"')
PLAN = re.compile(r'(Fast 9|Fast 8|Roll at \d|Standard)')
AVG = re.compile(r'>(\d\.\d{2})<')
GAMES = re.compile(r'>([\d,]+) games')


def strip_tags(x):
    return html.unescape(re.sub(r"<[^>]+>", " ", x)).strip()


def build_comps():
    page = get(TIERLIST, timeout=90).decode("utf-8", "replace")

    # ตำแหน่งเริ่มของแต่ละเทียร์ ใช้ตัดสินว่าคอมพ์ไหนอยู่เทียร์อะไร
    tier_at = []
    for m in re.finditer(r'id="tier-([SABCD])"', page):
        tier_at.append((m.start(), m.group(1)))
    tier_at.sort()

    def tier_of(pos):
        cur = "?"
        for at, t in tier_at:
            if at <= pos:
                cur = t
            else:
                break
        return cur

    anchors = list(re.finditer(r'href="(?:https://emblemcomp\.gg)?/comp/(\d+)"[^>]*>(.*?)</a>', page, re.S))
    comps = []
    for i, a in enumerate(anchors):
        chunk = page[a.end(): anchors[i + 1].start() if i + 1 < len(anchors) else a.end() + 12000]
        name = strip_tags(a.group(2))
        if not name:
            continue

        board, carries, cur = [], [], None
        for m in re.finditer(r'<img[^>]+>', chunk):
            tag = m.group(0)
            alt = re.search(r'alt="([^"]+)"', tag)
            wide = re.search(r'w=(\d+)', tag)
            if not alt or not wide:
                continue
            nm, w = html.unescape(alt.group(1)), wide.group(1)
            if "champions" in tag:
                if w == "128":
                    board.append(nm)
                else:                      # w=64 = หัวแถวไอเทมของตัวคีย์
                    cur = {"unit": nm, "items": []}
                    carries.append(cur)
            elif "items" in tag and cur is not None:
                t = re.search(r'title="([^"]+)"', tag)
                cur["items"].append(html.unescape(t.group(1)) if t else nm)

        plan = PLAN.search(chunk)
        avg = AVG.search(chunk)
        games = GAMES.search(chunk)
        if not board or not carries:
            continue
        comps.append({
            "id": a.group(1), "name": name, "tier": tier_of(a.start()),
            "plan": plan.group(1) if plan else "Standard",
            "avgPlace": float(avg.group(1)) if avg else None,
            "games": int(games.group(1).replace(",", "")) if games else None,
            "units": board,
            "carries": [c for c in carries if c["items"]],
        })
    return comps


def main():
    os.makedirs(OUT, exist_ok=True)
    status = {"ranAt": now()}

    try:
        st = build_set()
        with open(os.path.join(OUT, "set.json"), "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
        by_cost = {}
        for u in st["units"]:
            by_cost[u["cost"]] = by_cost.get(u["cost"], 0) + 1
        status["set"] = {"ok": True, "number": st["set"], "units": len(st["units"]),
                         "byCost": by_cost, "traits": len(st["traits"]), "items": len(st["items"])}
        print("set ok:", status["set"])
    except Exception as e:  # noqa: BLE001
        status["set"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
        print("set failed:", e, file=sys.stderr)

    path = os.path.join(OUT, "comps.json")
    try:
        comps = build_comps()
        if len(comps) < 5:
            raise RuntimeError(f"parse ได้แค่ {len(comps)} คอมพ์ — หน้าเว็บอาจเปลี่ยนโครงสร้าง")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"source": TIERLIST, "fetchedAt": now(), "comps": comps},
                      f, ensure_ascii=False, separators=(",", ":"))
        status["comps"] = {"ok": True, "count": len(comps),
                           "tiers": {t: sum(1 for c in comps if c["tier"] == t) for t in "SABCD"}}
        print("comps ok:", status["comps"])
    except Exception as e:  # noqa: BLE001
        # เก็บไฟล์เดิมไว้ ดีกว่าเขียนทับด้วยของว่าง
        status["comps"] = {"ok": False, "keptOld": os.path.exists(path),
                           "error": f"{type(e).__name__}: {e}"[:300]}
        print("comps failed:", e, file=sys.stderr)

    with open(os.path.join(OUT, "status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)

    if not status.get("set", {}).get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
