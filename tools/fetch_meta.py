#!/usr/bin/env python3
"""
ดึงข้อมูลคอมพ์จาก metatft.com ด้วย Playwright แล้วเขียนเป็น data/meta.json

เว็บ MetaTFT เป็น React ล้วน อ่าน HTML ตรง ๆ ไม่ได้ ต้องเปิดด้วยเบราว์เซอร์จริง
สคริปต์นี้ทำสิ่งเดียวกับที่ทำมือไว้:
  1) เปิดหน้า /comps  แล้วกดขยายการ์ดทุกใบ (เนื้อหาโหลดตอนขยาย)
  2) อ่านชื่อ/เทียร์/อันดับเฉลี่ย/อัตราชนะ + ยูนิตในคอมพ์
  3) อ่านผังกระดานจาก SVG (พิกัดอยู่ใน points ของช่องหกเหลี่ยม) + ดาว 3 ดาวของแต่ละยูนิต
  4) อ่านบอร์ดรายเลเวล 4-7 พร้อมอัตราชนะรอบ และแผนอัปเลเวล
  5) เปิด /items/artifact เก็บอาร์ติแฟกต์ + ยูนิตยอดนิยม
  6) เปิด /augments เก็บเทียร์เสริมพลัง + รหัสไอคอน
  7) ดึง unit_items_processed เก็บไอเทมยอดนิยมของทุกยูนิต
  8) เปิด /units เก็บเทียร์ยูนิต + ไอเทมยอดนิยม 5 ชิ้น

หน้าเว็บมีตัวกรอง (อันดับผู้เล่น/ช่วงเวลา) ที่ทำให้ตัวเลขต่างกันมาก
ค่าเริ่มต้นที่ใช้คือ แพลตตินัม+ · 3 วัน ซึ่งตรงกับชุดที่ฝังในแอป
"""
import json, re, sys, time, pathlib

URL_COMPS = "https://www.metatft.com/comps"
URL_ART   = "https://www.metatft.com/items/artifact"
URL_AUG   = "https://www.metatft.com/augments"
URL_UNITS = "https://www.metatft.com/units"
OUT       = pathlib.Path(__file__).resolve().parent.parent / "data" / "meta.json"

# ---------- โค้ดที่รันในหน้าเว็บ ----------

JS_PULL_COMPS = r"""
() => {
  const out = [];
  document.querySelectorAll(".CompsBoardContainer").forEach(board => {
    // --- ผังกระดาน: รูปยูนิตผูกกับช่องผ่าน fill:url(#pattern) ---
    const pat = {};
    board.querySelectorAll("pattern").forEach(p => {
      const im = p.querySelector(".UnitHexImage");
      if (im) pat[p.id] = im.getAttribute("alt");
    });
    const mid = el => {
      const pts = (el.getAttribute("points") || "").split(" ").map(s => s.split(",").map(Number));
      if (!pts.length) return [0, 0];
      return [pts.reduce((a, p) => a + p[0], 0) / pts.length,
              pts.reduce((a, p) => a + p[1], 0) / pts.length];
    };
    const cells = [...board.querySelectorAll(".teambuilder_hex, .teambuilder_hex_unit")].map(el => {
      const [cx, cy] = mid(el);
      const f = (el.getAttribute("fill") || "").match(/url\(#(.+?)\)/);
      return {cx, cy, unit: f && pat[f[1]] ? pat[f[1]] : null};
    });
    if (!cells.some(c => c.unit)) return;
    const rows = [];
    cells.forEach(c => { if (!rows.some(r => Math.abs(r - c.cy) < 8)) rows.push(c.cy); });
    rows.sort((a, b) => a - b);
    const minX = Math.min(...cells.map(c => c.cx));
    const board_pos = cells.filter(c => c.unit).map(c => [
      c.unit,
      rows.findIndex(r => Math.abs(r - c.cy) < 8),      // 0 = แนวหน้า
      Math.round((c.cx - minX) / 40.3)
    ]);

    // --- การ์ดที่ครอบกระดานนี้ ---
    let card = board;
    for (let i = 0; i < 12 && card; i++) {
      card = card.parentElement;
      if (card && card.innerText && /อัตราติด Top|Top 4 Rate/.test(card.innerText)) break;
    }
    if (!card) return;
    const L = card.innerText.split("\n").map(x => x.trim()).filter(Boolean);
    const ti = L.findIndex(x => /^[SABCD]$/.test(x));
    const ai = L.findIndex(x => /อันดับเฉลี่ย|Avg Place/.test(x));
    const wi = L.findIndex(x => /อัตราการชนะ|Win Rate/.test(x));

    // --- ยูนิตทั้งหมด + ไอเทมของแต่ละตัว (อ่านจากรูปในการ์ด) ---
    const units2 = [], carries = [];
    const uc = card.querySelector(".UnitsContainer");
    if (uc) {
      uc.querySelectorAll(".Unit_Wrapper").forEach(w => {
        const imgs = [...w.querySelectorAll("img")];
        const ch = imgs.find(i => /champions\//.test(i.src || ""));
        if (!ch) return;
        const nm = (ch.alt || "").trim();
        if (!nm) return;
        units2.push(nm);
        const items = imgs.filter(i => i !== ch)
          .map(i => (i.alt || "").trim())
          .filter(x => x && !/Star Unit|ดาว/i.test(x));
        if (items.length) carries.push([nm, items.slice(0, 3)]);
      });
    }

    // --- ยูนิตที่ต้องปั้น 3 ดาว (รูป tiers/3.png บนไอคอนในการ์ด) ---
    const three = [];
    const cont = card.querySelector(".UnitsContainer");
    if (cont) {
      cont.querySelectorAll(".Unit_Wrapper").forEach(w => {
        const im = [...w.querySelectorAll("img")].find(i => /champions\//.test(i.src || ""));
        const nm = im ? (im.alt || "").trim() : "";
        const st = w.querySelector(".stars_div img");
        if (nm && st && /tiers\/3\.png/.test(st.src || "")) three.push(nm);
      });
    }

    // --- บอร์ดรายเลเวล + อัตราชนะรอบ ---
    // อ่านจาก "รูปยูนิต" ในแต่ละแถว ไม่ใช่ข้อความ เพราะข้อความมีป้ายอื่นปนมาเยอะ
    // (เทียร์ ชื่อคอมพ์ Avg Place ฯลฯ) แล้วกลายเป็นชื่อยูนิตปลอมในผลลัพธ์
    const early = [];
    const rows = [...card.querySelectorAll("div")].filter(d => {
      const t = d.innerText || "";
      return /อัตราการชนะรอบ|Round Win Rate/.test(t) && t.length < 400;
    });
    rows.forEach(row => {
      const units = [...row.querySelectorAll("img")]
        .filter(im => /champions\//.test(im.src || ""))
        .map(im => (im.alt || "").trim())
        .filter(Boolean);
      const wm = (row.innerText || "").match(/([\d.]+)%/);
      if (units.length >= 2 && wm) early.push([[...new Set(units)], +wm[1]]);
    });

    // --- แผนอัปเลเวล ---
    // หัวข้อกับตัวคั่นต่างกันตามภาษาของหน้า จึงจับแบบยืดหยุ่น
    // แผนอัปเลเวล: ข้อความเป็นบรรทัด "เลเวล N" แล้วบรรทัดถัดไปคือสเตจ (2-5) หรือ "-"
    // บางเลเวลมีตัวเลขสถิติคั่นท้าย จึงไล่ทีละบรรทัดแทนการใช้ regex ยาว ๆ
    const seg = (card.innerText.split(/การอัปเลเวล[:：]?|Leveling[:：]?/)[1] || "")
      .split(/ลำดับความสำคัญ|Item Priority/)[0] || "";
    const toks = seg.split("\n").map(s => s.trim()).filter(Boolean);
    const steps = [];
    for (let i = 0; i < toks.length; i++) {
      const mm = toks[i].match(/^(?:เลเวล|Level)\s*(\d+)$/);
      if (mm && toks[i+1] && /^(\d-\d|-)$/.test(toks[i+1])) steps.push([+mm[1], toks[i+1]]);
    }
    const plan = toks[0] || "";

    out.push({
      name: L[ti + 1] || "?",
      tier: L[ti] || "",
      avg: ai >= 0 ? parseFloat(L[ai + 1]) : null,
      win: wi >= 0 ? parseFloat(L[wi + 1]) : null,
      plan: L[ti + 2] || "",
      steps, early, board: board_pos, three, carries,
      units: units2.length >= 5 ? units2
           : [...new Set(board_pos.map(b => b[0]))].sort()
    });
  });
  return out;
}
"""

JS_EXPAND = r"""
async () => {
  const ex = [...document.querySelectorAll(".Expand")];
  for (const e of ex) {
    const t = e.ownerSVGElement ? e.ownerSVGElement.parentElement : e;
    ["mousedown", "mouseup", "click"].forEach(k =>
      t.dispatchEvent(new MouseEvent(k, {bubbles: true, cancelable: true, view: window})));
    await new Promise(r => setTimeout(r, 120));
  }
  await new Promise(r => setTimeout(r, 2500));
  return document.querySelectorAll(".CompsBoardContainer").length;
}
"""

JS_ARTIFACTS = r"""
() => {
  const out = [];
  document.querySelectorAll("tr").forEach(tr => {
    const a = tr.querySelector('a[href*="/items/DA_Artifact_"]');
    if (!a) return;
    const c = [...tr.querySelectorAll("td")].map(td => td.innerText.trim());
    if (c.length < 6 || !/^[SABCD]$/.test(c[1])) return;
    const f = (c[5] || "").match(/([\d,]+)\s+([\d.]+)%/);
    const units = [...tr.querySelectorAll("img")]
      .map(i => (i.getAttribute("alt") || "").trim()).filter(x => x && x.length < 20).slice(1, 6);
    out.push({
      id: a.getAttribute("href").split("/").pop(),
      name: c[0], tier: c[1], avg: parseFloat(c[2]),
      delta: parseFloat((c[3] || "0").replace("−", "-")),
      win: parseFloat(c[4]),
      games: f ? +f[1].replace(/,/g, "") : 0,
      freq: f ? +f[2] : 0,
      units
    });
  });
  return out;
}
"""


JS_UNIT_ITEMS = r"""
async () => {
  // API ตัวนี้บอกไอเทมที่นิยมใส่ให้แต่ละยูนิต เรียงจากที่ใช้บ่อยสุด
  const r = await fetch("https://api-hc.metatft.com/tft-comps-api/unit_items_processed");
  const j = await r.json();
  const root = j.units || j;
  return Object.keys(root).map(id => {
    const u = root[id];
    // ตัดคำนำหน้า/ท้ายของ id ให้เหลือชื่อยูนิต (DA_18_Akali_AD -> Akali)
    const nm = id.replace(/^DA_|^TFT18_/, "").replace(/^18_/, "")
                 .replace(/18(_\w+)?$/, "").replace(/_(AD|AP)$/, "");
    return {
      unit: nm,
      items: (u.items || []).slice(0, 3).map(x =>
        (x.itemName || "").replace(/^DA_/, "").replace(/^Component_/, "")),
      avg: u.avg, games: u.count
    };
  }).filter(x => x.unit && x.items.length);
}
"""

JS_UNIT_TIERS = r"""
() => {
  // ตารางเทียร์ยูนิต: ชื่อ | ระดับ | อันดับเฉลี่ย | ชนะ | ความถี่ | ไอเทมยอดนิยม (จาก alt ของรูป)
  return [...document.querySelectorAll("tr")]
    .filter(tr => tr.querySelector('a[href*="/units/"]'))
    .map(tr => {
      const c = [...tr.querySelectorAll("td")].map(td => td.innerText.trim());
      const f = (c[4] || "").match(/([\d,]+)\s+([\d.]+)%/);
      const items = [...tr.querySelectorAll("img")].map(i => (i.alt || "").trim())
        .filter(x => x && x.length < 26).slice(1, 6);
      return {unit: c[0], tier: c[1], avg: parseFloat(c[2]),
              win: parseFloat(c[3] || 0), freq: f ? +f[2] : 0, items};
    })
    .filter(x => x.unit && /^[SABCD]$/.test(x.tier || ""));
}
"""

JS_AUGMENTS = r"""
() => {
  // หน้าเทียร์ลิสต์: แต่ละแถวคือหนึ่งเทียร์ ในแถวมีไอคอนของเสริมพลังทุกใบ
  const map = {};
  document.querySelectorAll(".TierListRow").forEach(row => {
    const tier = ((row.querySelector(".TierListTierTitle") || {}).innerText || "").trim();
    if (!/^[SABCD]$/.test(tier)) return;
    row.querySelectorAll("img").forEach(img => {
      const nm = (img.alt || "").trim();
      if (!nm || /^กรอง|^Filter/.test(nm)) return;          // ปุ่มกรองก็เป็น img เหมือนกัน
      const f = (img.getAttribute("src") || "").split("/").pop().split("?")[0]
        .replace(/\.\w+$/, "").replace("t_augmenticon_", "");
      if (!map[nm]) map[nm] = {tier, icon: f};
    });
  });
  return Object.keys(map).map(n => ({name: n, tier: map[n].tier, icon: map[n].icon}));
}
"""


def goto(page, url, wait_ms=6000, tries=3):
    """เปิดหน้าแบบทนต่อเว็บที่โหลดโฆษณาไม่จบ (networkidle แทบไม่มีทางเกิด)"""
    last = None
    for i in range(tries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(wait_ms)
            return
        except Exception as e:
            last = e
            print(f"  เปิด {url} ไม่สำเร็จ (ครั้งที่ {i+1}) — ลองใหม่", file=sys.stderr)
            page.wait_for_timeout(3000)
    raise last


def scroll_and_collect(page, js_pull, rounds=14, step=2200):
    """เลื่อนหน้าเป็นช่วง ๆ แล้วเก็บสะสม เพราะการ์ดที่พ้นจอถูกถอดออกจาก DOM"""
    seen, acc = set(), []
    for i in range(rounds):
        try:
            page.evaluate(JS_EXPAND)
        except Exception as e:
            print("  ขยายการ์ดไม่สำเร็จ:", e, file=sys.stderr)
        for row in page.evaluate(js_pull):
            key = ",".join(row["units"])
            if key and key not in seen:
                seen.add(key)
                acc.append(row)
        print(f"  รอบ {i+1}/{rounds} · เก็บได้ {len(acc)} คอมพ์", flush=True)
        page.mouse.wheel(0, step)
        page.wait_for_timeout(1800)
    return acc


def main():
    from playwright.sync_api import sync_playwright

    data = {"fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "metatft.com", "filter": "แพลตตินัม+ · 3 วัน"}

    with sync_playwright() as pw:
        br = pw.chromium.launch(args=["--no-sandbox"])
        page = br.new_page(viewport={"width": 1500, "height": 1000},
                           locale="th-TH", user_agent=(
                               "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"))

        print("เปิดหน้าคอมพ์…", flush=True)
        goto(page, URL_COMPS, 7000)
        comps = scroll_and_collect(page, JS_PULL_COMPS)
        data["comps"] = comps
        print(f"คอมพ์ทั้งหมด {len(comps)}", flush=True)

        print("เปิดหน้าอาร์ติแฟกต์…", flush=True)
        goto(page, URL_ART, 6000)
        # ตารางโหลดแถวเพิ่มตอนเลื่อน ต้องเลื่อนหลายรอบจนจำนวนแถวนิ่ง
        last = 0
        for i in range(10):
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(1500)
            n = page.evaluate("() => document.querySelectorAll('a[href*=\"/items/DA_Artifact_\"]').length")
            print(f"  อาร์ติแฟกต์ที่เห็น {n} ชิ้น", flush=True)
            if n == last and n >= 24:
                break
            last = n
        page.wait_for_timeout(1500)
        try:
            arts = page.evaluate(JS_ARTIFACTS)
            data["artifacts"] = arts
            print(f"อาร์ติแฟกต์ {len(arts)} ชิ้น", flush=True)
        except Exception as e:
            print("  อ่านอาร์ติแฟกต์ไม่สำเร็จ:", e, file=sys.stderr)

        print("ดึงไอเทมยอดนิยมของยูนิต…", flush=True)
        units = page.evaluate(JS_UNIT_ITEMS)
        data["unitItems"] = units
        print(f"ยูนิต {len(units)} ตัว", flush=True)

        print("เปิดหน้าเทียร์ยูนิต…", flush=True)
        goto(page, URL_UNITS, 7000)
        try:
            utiers = page.evaluate(JS_UNIT_TIERS)
            data["unitTiers"] = utiers
            print(f"เทียร์ยูนิต {len(utiers)} ตัว", flush=True)
        except Exception as e:
            print("  อ่านเทียร์ยูนิตไม่สำเร็จ:", e, file=sys.stderr)

        print("เปิดหน้าเสริมพลัง…", flush=True)
        goto(page, URL_AUG, 7000)
        try:
            augs = page.evaluate(JS_AUGMENTS)
            # เก็บเฉพาะที่ใช้ตัดสินใจ: S/A ควรหยิบ, C ควรเลี่ยง (B คือค่าเริ่มต้นอยู่แล้ว)
            augs = [a for a in augs if a["tier"] in ("S", "A", "C")]
            data["augments"] = augs
            print(f"เสริมพลัง {len(augs)} ใบ "
                  f"(S {sum(1 for a in augs if a['tier']=='S')} · "
                  f"A {sum(1 for a in augs if a['tier']=='A')} · "
                  f"C {sum(1 for a in augs if a['tier']=='C')})", flush=True)
        except Exception as e:
            print("  อ่านเสริมพลังไม่สำเร็จ:", e, file=sys.stderr)

        br.close()

    if len(comps) < 20:
        print(f"เก็บคอมพ์ได้แค่ {len(comps)} ชุด (ปกติ 40+) — ไม่เขียนทับไฟล์เดิม", file=sys.stderr)
        sys.exit(1)

    if len(data.get("artifacts", [])) < 24:
        print(f"อาร์ติแฟกต์ได้แค่ {len(data.get('artifacts', []))} ชิ้น — ใช้ของเดิม", file=sys.stderr)
        if OUT.exists():
            try:
                old = json.loads(OUT.read_text(encoding="utf-8"))
                if old.get("artifacts"):
                    data["artifacts"] = old["artifacts"]
            except Exception:
                pass

    if len(data.get("unitTiers", [])) < 30:
        print(f"เทียร์ยูนิตได้แค่ {len(data.get('unitTiers', []))} ตัว — ใช้ของเดิม", file=sys.stderr)
        if OUT.exists():
            try:
                old = json.loads(OUT.read_text(encoding="utf-8"))
                if old.get("unitTiers"):
                    data["unitTiers"] = old["unitTiers"]
            except Exception:
                pass

    if len(data.get("unitItems", [])) < 30:
        print(f"ไอเทมยูนิตได้แค่ {len(data.get('unitItems', []))} ตัว — ใช้ของเดิม", file=sys.stderr)
        if OUT.exists():
            try:
                old = json.loads(OUT.read_text(encoding="utf-8"))
                if old.get("unitItems"):
                    data["unitItems"] = old["unitItems"]
            except Exception:
                pass

    # เสริมพลังดึงไม่ได้ก็ไม่เป็นไร แต่ต้องไม่เขียนทับของเดิมด้วยค่าว่าง
    if len(data.get("augments", [])) < 40:
        print(f"เสริมพลังได้แค่ {len(data.get('augments', []))} ใบ — ใช้ของเดิมแทน", file=sys.stderr)
        if OUT.exists():
            try:
                old = json.loads(OUT.read_text(encoding="utf-8"))
                if old.get("augments"):
                    data["augments"] = old["augments"]
            except Exception:
                pass

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("เขียน", OUT, f"({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
