#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, json
from pathlib import Path

FIELDS = ["equipment_tag","serial_number","document_id","source_page","linked_page","field_name","field_value","linkage_method","evidence_tier","confidence","source_text","source_bbox","review_status"]

def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as f: return list(csv.DictReader(f))

def main():
    p=argparse.ArgumentParser(); p.add_argument("--records",required=True,type=Path); p.add_argument("--metadata",required=True,type=Path); p.add_argument("--all-ocr-lines",required=True,type=Path); p.add_argument("--output",required=True,type=Path); a=p.parse_args()
    records=rows(a.records); evidence=rows(a.metadata)+rows(a.all_ocr_lines); out=[]; n=1
    for rec in records:
        tag=rec.get("equipment_tag",""); pages=set()
        try: pages.update(str(x) for x in json.loads(rec.get("linked_source_pages","[]")))
        except Exception: pass
        pages.add(rec.get("source_page",""))
        for field in ("serial_number","model","equipment_description","range_or_rating","set_point","declaration_reference","order_acceptance_number","purchase_order","test_certificate_reference"):
            value=rec.get(field,"")
            if value:
                out.append({"equipment_tag":tag,"serial_number":rec.get("serial_number",""),"document_id":rec.get("source_document_id",""),"source_page":rec.get("source_page",""),"linked_page":"|".join(sorted(x for x in pages if x)),"field_name":field,"field_value":value,"linkage_method":"6c_record_field","evidence_tier":"tier_1_dense_record_evidence","confidence":rec.get("tag_ocr_confidence",""),"source_text":rec.get("source_text",""),"source_bbox":rec.get("source_bbox",""),"review_status":rec.get("review_status","")}); n+=1
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open("w",encoding="utf-8",newline="") as f: w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerows(out)
    print(f"Candidate records read: {len(records)}"); print(f"Evidence rows written: {len(out)}"); print(f"Output: {a.output}")
if __name__=="__main__": main()
