"""Normalize equivalent LLM cluster shapes without inventing memberships."""
import re
from fastapi import HTTPException


def normalize_clusters(value, expected_ids):
    if isinstance(value, dict):
        roots = [key for key in ('clusters', 'topics', 'themes') if key in value]
        if len(roots) != 1:
            raise HTTPException(502, 'AI ไม่ส่งรายการกลุ่มหัวข้อที่อ่านได้ ผลกราฟเดิมยังอยู่')
        value = value[roots[0]]
    if not isinstance(value, list) or not 3 <= len(value) <= 5:
        raise HTTPException(502, 'AI ต้องส่งกลุ่มหัวข้อ 3–5 กลุ่ม ผลกราฟเดิมยังอยู่')
    clusters, seen = [], set()
    allowed = set(expected_ids)
    for group in value:
        if not isinstance(group, dict):
            raise HTTPException(502, 'AI ส่งรายละเอียดกลุ่มหัวข้อไม่ถูกต้อง ผลกราฟเดิมยังอยู่')
        names = [k for k in ('name', 'topic', 'theme', 'label') if k in group]
        members = [k for k in ('ids', 'paper_ids', 'reference_ids', 'references') if k in group]
        if len(names) != 1 or len(members) != 1:
            raise HTTPException(502, 'AI ไม่ส่งชื่อกลุ่มหรือเลขอ้างอิงที่ชัดเจน ผลกราฟเดิมยังอยู่')
        name, ids = group[names[0]], group[members[0]]
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or not isinstance(ids, list) or not 1 <= len(ids) <= 100:
            raise HTTPException(502, 'AI ส่งชื่อกลุ่มหรือรายการอ้างอิงไม่ถูกต้อง ผลกราฟเดิมยังอยู่')
        clean = []
        for item in ids:
            if isinstance(item, dict) and set(item) == {'id'}:
                item = item['id']
            if isinstance(item, str) and re.fullmatch(r'[1-9][0-9]*', item):
                item = int(item)
            if type(item) is not int or item not in allowed:
                raise HTTPException(502, 'AI ส่งเลขอ้างอิงที่ไม่อยู่ในชุดข้อมูล ผลกราฟเดิมยังอยู่')
            if item in clean:
                continue  # Repeating an ID within its own group changes no membership.
            if item in seen:
                raise HTTPException(502, 'AI จัดอ้างอิงเดียวกันไว้หลายกลุ่ม ผลกราฟเดิมยังอยู่')
            clean.append(item)
            seen.add(item)
        clusters.append({'name': name.strip(), 'ids': clean})
    missing = allowed - seen
    result = {'clusters': clusters}
    if missing:
        result['unassigned_ids'] = sorted(missing)
        result['warnings'] = [f'AI ยังไม่จัดกลุ่ม {len(missing)} รายการ แสดงสีเทาเพื่อให้ตรวจต่อ ไม่ได้เดาธีมให้']
    return result
