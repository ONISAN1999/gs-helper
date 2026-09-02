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
    const seg = card.innerText.split(/การจัดตำแหน่ง|Positioning/)[0];
    const re = /((?:[^\n]+\n)+?)([\d.]+)%\n(?:อัตราการชนะรอบ|Round Win Rate)/g;
    const early = [];
    let m;
    while ((m = re.exec(seg))) {
      const units = m[1].trim().split("\n").filter(x =>
        !/^เลเวล|^Level|^ช่วงต้นเกม|^Early|^กำหนดเอง|^Custom|%$/.test(x) && !/^[\d.]+%$/.test(x));
      if (units.length) early.push([units, +m[2]]);
    }

    // --- แผนอัปเลเวล ---
    const lv = card.innerText.split(/การอัปเลเวล:|Leveling:/)[1] || "";
    const plan = (lv.split(/ลำดับความสำคัญ|Item Priority/)[0] || "").trim();
    const steps = [...plan.matchAll(/(?:เลเวล|Level) (\d+)\n([\d\-]+|-)/g)].map(x => [+x[1], x[2]]);

    out.push({
      name: L[ti + 1] || "?",
      tier: L[ti] || "",
      avg: ai >= 0 ? parseFloat(L[ai + 1]) : null,
      win: wi >= 0 ? parseFloat(L[wi + 1]) : null,
      plan: L[ti + 2] || "",
      steps, early, board: board_pos, three,
      units: [...new Set(board_pos.map(b => b[0]))].sort()
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
        page.goto(URL_COMPS, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(6000)
        comps = scroll_and_collect(page, JS_PULL_COMPS)
        data["comps"] = comps
        print(f"คอมพ์ทั้งหมด {len(comps)}", flush=True)

        print("เปิดหน้าอาร์ติแฟกต์…", flush=True)
        page.goto(URL_ART, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(5000)
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(3000)
        arts = page.evaluate(JS_ARTIFACTS)
        data["artifacts"] = arts
        print(f"อาร์ติแฟกต์ {len(arts)} ชิ้น", flush=True)

        print("ดึงไอเทมยอดนิยมของยูนิต…", flush=True)
        units = page.evaluate(JS_UNIT_ITEMS)
        data["unitItems"] = units
        print(f"ยูนิต {len(units)} ตัว", flush=True)

        print("เปิดหน้าเทียร์ยูนิต…", flush=True)
        page.goto(URL_UNITS, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(6000)
        utiers = page.evaluate(JS_UNIT_TIERS)
        data["unitTiers"] = utiers
        print(f"เทียร์ยูนิต {len(utiers)} ตัว", flush=True)

        print("เปิดหน้าเสริมพลัง…", flush=True)
        page.goto(URL_AUG, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(6000)
        augs = page.evaluate(JS_AUGMENTS)
        # เก็บเฉพาะที่ใช้ตัดสินใจ: S/A ควรหยิบ, C ควรเลี่ยง (B คือค่าเริ่มต้นอยู่แล้ว)
        augs = [a for a in augs if a["tier"] in ("S", "A", "C")]
        data["augments"] = augs
        print(f"เสริมพลัง {len(augs)} ใบ "
              f"(S {sum(1 for a in augs if a['tier']=='S')} · "
              f"A {sum(1 for a in augs if a['tier']=='A')} · "
              f"C {sum(1 for a in augs if a['tier']=='C')})", flush=True)

        br.close()

    if len(comps) < 20:
        print(f"เก็บคอมพ์ได้แค่ {len(comps)} ชุด (ปกติ 40+) — ไม่เขียนทับไฟล์เดิม", file=sys.stderr)
        sys.exit(1)

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
