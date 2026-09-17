#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,json,re
from collections import defaultdict
from pathlib import Path

FIELDS=["equipment_tag","serial_number","model","equipment_description","range_or_rating","set_point","declaration_reference","order_acceptance_number","purchase_order","test_certificate_reference","source_page","linked_source_pages","link_basis","field_inheritance_note","record_status","review_status","raw_ocr_tag","tag_ocr_confidence","source_record_id","source_document_id","source_table_index","source_text","source_bbox","extraction_method","review_reason","overlaps_3d_tag"]

def rows(path):
 with path.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def clean(v):return re.sub(r"\s+"," ",str(v or "")).strip()
def main():
 p=argparse.ArgumentParser();p.add_argument("--records",required=True,type=Path);p.add_argument("--evidence",required=True,type=Path);p.add_argument("--output",required=True,type=Path);a=p.parse_args(); records=rows(a.records); evidence=rows(a.evidence); groups=defaultdict(list)
 for e in evidence:
  for key in ("declaration_reference","order_acceptance_number","purchase_order","test_certificate_reference"):
   if clean(e.get(key)):groups[(key,clean(e[key]))].append(e.get("equipment_tag",""))
 for r in records:
  linked=set(); basis=[]
  for key in ("declaration_reference","order_acceptance_number","purchase_order","test_certificate_reference"):
   value=clean(r.get(key));
   if value:
    for tag in groups[(key,value)]:
     if tag and tag!=r.get("equipment_tag"):linked.add(tag); basis.append(key)
  pages=set()
  try:pages.update(str(x) for x in json.loads(r.get("linked_source_pages","[]")))
  except Exception:pass
  if r.get("source_page"):pages.add(r["source_page"])
  r["linked_source_pages"]=json.dumps(sorted(int(x) for x in pages if str(x).isdigit()))
  r["link_basis"]="shared " + "/".join(sorted(set(basis))) if basis else r.get("link_basis","")
  r["field_inheritance_note"]="Inherited only from explicit shared reference evidence." if basis else r.get("field_inheritance_note","")
  r={k:r.get(k,"") for k in FIELDS}; records[records.index(next(x for x in records if x.get("equipment_tag")==r.get("equipment_tag")))] = r
 a.output.parent.mkdir(parents=True,exist_ok=True)
 with a.output.open("w",encoding="utf-8",newline="") as f:w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(records)
 print(f"Records enriched: {len(records)}");print(f"Output: {a.output}")
if __name__=="__main__":main()
