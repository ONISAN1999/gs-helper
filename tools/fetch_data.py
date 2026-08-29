#!/usr/bin/env python3
"""
ดึงข้อมูลเซ็ตปัจจุบันของ TFT/Golden Spatula + คอมพ์ยอดนิยม แล้วเขียนลง data/
รันบน GitHub Actions (runner ต่อเน็ตได้เต็มที่)

ผลลัพธ์:
  data/set.json    - ยูนิต / เผ่า / ไอเทม ของเซ็ตล่าสุด (จาก CommunityDragon)
  data/comps.json  - คอมพ์ + สถิติ (จาก MetaTFT ถ้าดึงได้)
  data/status.json - สถานะการดึงล่าสุด ไว้ดูว่าอันไหนพัง
"""

import json, os, sys, time, urllib.request, urllib.error, datetime

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
CDRAGON = "https://raw.communitydragon.org/latest/cdragon/tft/en_us.json"

# MetaTFT ไม่มี API สาธารณะอย่างเป็นทางการ -> ลองหลาย endpoint แล้วใช้อันที่ผ่าน
COMP_ENDPOINTS = [
    "https://api.metatft.com/tft-stats/general/comps?queue=1100&days=3&rank=CHALLENGER,GRANDMASTER,MASTER&patch=current",
    "https://api2.metatft.com/tft-stats/general/comps?queue=1100&days=3&rank=CHALLENGER,GRANDMASTER,MASTER&patch=current",
    "https://www.metatft.com/api/comps?queue=1100&days=3",
]


def get(url, timeout=180):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.metatft.com/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def pick_set(raw):
    """เลือกเซ็ตที่เลขสูงสุดจากก้อน CommunityDragon"""
    sets = raw.get("sets") or {}
    best = None
    for k, v in sets.items():
        try:
            n = float(k)
        except ValueError:
            continue
        if best is None or n > best[0]:
            best = (n, v)
    if best:
        return best[0], best[1]
    for s in raw.get("setData", []):
        if s.get("number"):
            return s["number"], s
    raise SystemExit("หาเซ็ตใน CommunityDragon ไม่เจอ")


def build_set():
    raw = json.loads(get(CDRAGON))
    num, s = pick_set(raw)

    units = []
    for c in s.get("champions", []):
        cost = c.get("cost")
        if not c.get("name") or cost is None:
            continue
        if cost > 7:  # ตัวพิเศษ/หุ่นเชิด ไม่เอา
            continue
        ab = c.get("ability") or {}
        units.append({
            "id": c.get("apiName"),
            "name": c.get("name"),
            "cost": cost,
            "traits": c.get("traits") or [],
            "skill": ab.get("name"),
            "skillDesc": (ab.get("desc") or "")[:400],
            "icon": c.get("squareIcon") or c.get("icon"),
            "ad": (c.get("stats") or {}).get("damage"),
            "ap": (c.get("stats") or {}).get("mana"),
        })

    traits = [{
        "id": t.get("apiName"),
        "name": t.get("name"),
        "desc": (t.get("desc") or "")[:400],
        "tiers": [e.get("minUnits") for e in (t.get("effects") or [])],
        "icon": t.get("icon"),
    } for t in s.get("traits", []) if t.get("name")]

    items = []
    for it in raw.get("items", []):
        name = it.get("name")
        if not name or name.startswith("tft_item_name"):
            continue
        comp = it.get("composition") or []
        items.append({
            "id": it.get("apiName"),
            "name": name,
            "from": comp,
            "component": len(comp) == 0,
            "desc": (it.get("desc") or "")[:300],
            "icon": it.get("icon"),
        })

    return {
        "set": num,
        "fetchedAt": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "units": sorted(units, key=lambda u: (u["cost"], u["name"])),
        "traits": traits,
        "items": items,
    }


def build_comps():
    tried = []
    for url in COMP_ENDPOINTS:
        try:
            body = get(url, timeout=90)
            data = json.loads(body)
            return {"ok": True, "source": url,
                    "fetchedAt": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "data": data}, tried
        except Exception as e:  # noqa: BLE001
            tried.append({"url": url, "error": f"{type(e).__name__}: {e}"[:200]})
            time.sleep(2)
    return {"ok": False, "tried": tried,
            "fetchedAt": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}, tried


def main():
    os.makedirs(OUT, exist_ok=True)
    status = {"ranAt": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}

    try:
        st = build_set()
        with open(os.path.join(OUT, "set.json"), "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
        status["set"] = {"ok": True, "number": st["set"], "units": len(st["units"]),
                         "traits": len(st["traits"]), "items": len(st["items"])}
        print(f"set {st['set']}: {len(st['units'])} units / {len(st['traits'])} traits / {len(st['items'])} items")
    except Exception as e:  # noqa: BLE001
        status["set"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
        print("set fetch failed:", e, file=sys.stderr)

    comps, tried = build_comps()
    with open(os.path.join(OUT, "comps.json"), "w", encoding="utf-8") as f:
        json.dump(comps, f, ensure_ascii=False, separators=(",", ":"))
    status["comps"] = {"ok": comps["ok"], "source": comps.get("source"), "tried": tried}
    print("comps ok:", comps["ok"], comps.get("source"))

    with open(os.path.join(OUT, "status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)

    # ไม่ fail job ถ้า comps พัง เพราะ set.json ยังใช้ได้
    if not status.get("set", {}).get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
