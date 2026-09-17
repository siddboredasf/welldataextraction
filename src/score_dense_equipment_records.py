#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,re
from pathlib import Path
FIELDS=["equipment_tag","serial_number","model","equipment_description","range_or_rating","set_point","declaration_reference","order_acceptance_number","purchase_order","test_certificate_reference","source_page","linked_source_pages","link_basis","field_inheritance_note","record_status","review_status","raw_ocr_tag","tag_ocr_confidence","source_record_id","source_document_id","source_table_index","source_text","source_bbox","extraction_method","review_reason","overlaps_3d_tag","score"]
CORE=["model","equipment_description","range_or_rating","set_point"]
def rows(p):
 with p.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def main():
 p=argparse.ArgumentParser();p.add_argument("--input",required=True,type=Path);p.add_argument("--selected",required=True,type=Path);p.add_argument("--review",required=True,type=Path);a=p.parse_args();out=[];review=[]
 for r in rows(a.input):
  missing=[x for x in CORE if not r.get(x)]
  partial=bool(r.get("range_or_rating")) and not re.search(r"\d\s*[-/]\s*\d",r["range_or_rating"])
  reasons=[]
  if missing:reasons.append("missing "+", ".join(missing))
  if partial:reasons.append("range lower limit not captured")
  score=sum(bool(r.get(x)) for x in CORE)+sum(bool(r.get(x)) for x in ("serial_number","declaration_reference","purchase_order","test_certificate_reference"))
  r={k:r.get(k,"") for k in FIELDS};r["score"]=str(score);r["review_status"]="Candidate — complete core fields" if not reasons else "Review required: "+"; ".join(reasons);r["review_reason"]="; ".join(reasons);out.append(r)
  for field in missing+(["range_or_rating"] if partial else []):review.append({"equipment_tag":r["equipment_tag"],"field_name":field,"source_page":r["source_page"],"reason":r["review_status"],"recommended_action":"Inspect source page and retain blank unless field-to-tag evidence is explicit."})
 a.selected.parent.mkdir(parents=True,exist_ok=True);a.review.parent.mkdir(parents=True,exist_ok=True)
 with a.selected.open("w",encoding="utf-8",newline="") as f:w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(out)
 with a.review.open("w",encoding="utf-8",newline="") as f:w=csv.DictWriter(f,fieldnames=["equipment_tag","field_name","source_page","reason","recommended_action"]);w.writeheader();w.writerows(review)
 print(f"Candidate records scored: {len(out)}");print(f"Review items written: {len(review)}")
if __name__=="__main__":main()
