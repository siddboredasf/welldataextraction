# Vendor-specification extraction plan

## Pages 69 and 79: material test reports

Document role: material-test report / material-batch evidence.

Target fields:
- Manufacturer
- Test report number
- Test report date
- Customer
- Order number and date
- Delivery note number and date
- Heat number
- Material grade
- Material designation
- Applicable material standard
- Shape
- Size and unit
- Delivery form
- Chemical composition
- Mechanical properties
- Handwritten product/model notes

Data modelling:
- Heat number identifies a material batch, not automatically a unique installed asset.
- Handwritten annotations must remain candidate fields and require review.
- Every field must retain page, OCR confidence, raw text, and bounding-box evidence.

Output:
data/structured/vendor_manual_001/material_test_reports/

## Page 76: conformity certificate

Document role: product-series compliance evidence.

Target fields:
- Issuer
- Certificate identifier
- Certificate date
- Manufacturer
- Product series
- Applicable standards
- Explosion-protection marking
- Certificate page count
- Signatory/approval evidence

Data modelling:
- Link this certificate to a physical asset only after another source proves that
  the asset model belongs to the certified MA or TAH series.
- Do not use this page alone to create an individual digital-twin asset.

Output:
data/structured/vendor_manual_001/conformity_certificates/
